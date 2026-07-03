"""Tests for the dashboard's auth / CSRF / token gating (pure, no sockets)."""

import asyncio

import pytest

from kilovault.config import Config
from kilovault.manager import Manager
from kilovault.server.app import DashboardServer, _hsan, _safe_filename


def _server(tmp_path, host):
    cfg = Config()
    cfg.db_path = tmp_path / "h.db"
    cfg.transport.type = "simulator"
    cfg.retention_days = 0  # no housekeeping needed for these unit tests
    return DashboardServer(Manager(cfg), host=host)


def test_loopback_has_no_token_and_guards_posts(tmp_path):
    s = _server(tmp_path, "127.0.0.1")
    try:
        assert s.token is None
        # reads are always allowed on loopback
        assert s._auth_denied("GET", "/api/snapshot", {}, {}) is None
        # same-origin POST allowed
        assert s._auth_denied("POST", "/api/rename", {},
                              {"host": "127.0.0.1:8765"}) is None
        assert s._auth_denied("POST", "/api/rename", {},
                              {"host": "localhost:8765",
                               "origin": "http://localhost:8765"}) is None
        # cross-site POST (foreign Origin) denied -> CSRF protection
        assert s._auth_denied("POST", "/api/rename", {},
                              {"host": "127.0.0.1:8765",
                               "origin": "http://evil.example"}) == 403
        # DNS-rebinding (foreign Host) denied
        assert s._auth_denied("POST", "/api/transport", {},
                              {"host": "evil.example"}) == 403
    finally:
        s.manager.storage.close()


def test_lan_requires_token(tmp_path):
    s = _server(tmp_path, "0.0.0.0")
    try:
        assert s.token  # token generated when exposed beyond loopback
        # any /api/* without the token is rejected
        assert s._auth_denied("GET", "/api/snapshot", {}, {}) == 401
        assert s._auth_denied("POST", "/api/transport", {}, {}) == 401
        # with the correct token it is allowed
        assert s._auth_denied("GET", "/api/snapshot", {"token": [s.token]}, {}) is None
        # a wrong token is rejected
        assert s._auth_denied("GET", "/api/snapshot", {"token": ["nope"]}, {}) == 401
        # the page shell itself is not gated (carries no data)
        assert s._auth_denied("GET", "/", {}, {}) is None
        assert s._auth_denied("GET", "/static/app.js", {}, {}) is None
    finally:
        s.manager.storage.close()


class _FakeWriter:
    """Captures bytes written by DashboardServer._send (no real socket)."""

    def __init__(self):
        self.buf = bytearray()

    def write(self, data):
        self.buf.extend(data)

    async def drain(self):
        return None


class _FakeReader:
    """Feeds canned lines to DashboardServer._read_request (no real socket)."""

    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        return self._lines.pop(0) if self._lines else b""

    async def readexactly(self, n):
        return b""


def test_read_request_caps_header_count(tmp_path):
    # A flood of headers must be rejected (431), not buffered without bound.
    s = _server(tmp_path, "127.0.0.1")
    try:
        lines = ([b"GET /api/snapshot HTTP/1.1\r\n"]
                 + [f"X-Pad-{i}: y\r\n".encode() for i in range(200)]
                 + [b"\r\n"])
        result = asyncio.run(s._read_request(_FakeReader(lines)))
        assert result[0] == "__err__" and result[1] == 431
    finally:
        s.manager.storage.close()


def test_on_sample_never_kills_collector(tmp_path):
    # An unexpected error while handling a frame must be swallowed so the
    # collector task (and the whole monitor) stays alive.
    import types

    cfg = Config()
    cfg.db_path = tmp_path / "h.db"
    cfg.transport.type = "simulator"
    m = Manager(cfg)
    try:
        def boom(_addr):
            raise RuntimeError("boom")
        m._state_for = boom
        # Must not raise despite _state_for blowing up.
        asyncio.run(m._on_sample(types.SimpleNamespace(address="AA", timestamp=0)))
    finally:
        m.storage.close()


def test_header_values_cannot_inject_crlf():
    # A CR/LF in a header value must not create a new header line.
    assert _hsan("kilovault_abc\r\nX-Injected: PWNED_1.csv") == \
        "kilovault_abcX-Injected: PWNED_1.csv"
    assert "\r" not in _hsan("a\rb") and "\n" not in _hsan("a\nb")


def test_safe_filename_strips_dangerous_chars():
    # The address query param flows into a Content-Disposition filename; make
    # sure a crafted value can't smuggle CRLF or quotes into the header.
    dirty = "all_\r\nSet-Cookie: x=1_\"attack\"_123.csv"
    clean = _safe_filename(dirty)
    assert "\r" not in clean and "\n" not in clean and '"' not in clean
    assert len(clean) <= 120


def test_send_neutralizes_injected_content_disposition(tmp_path):
    # End-to-end: a malicious filename passed to _send is emitted on a single
    # header line — no response splitting.
    s = _server(tmp_path, "127.0.0.1")
    try:
        w = _FakeWriter()
        evil = 'attachment; filename="x\r\nX-Injected: PWNED"'
        asyncio.run(s._send(w, 200, "text/csv", b"col\n",
                            extra_headers={"Content-Disposition": evil}))
        head = bytes(w.buf).split(b"\r\n\r\n", 1)[0].decode("latin1")
        lines = head.split("\r\n")
        assert not any(line.strip().lower().startswith("x-injected")
                       for line in lines), head
    finally:
        s.manager.storage.close()


def test_connect_info_url_encodes_as_qr(tmp_path):
    from kilovault import qrcode as qr
    s = _server(tmp_path, "0.0.0.0")
    try:
        info = s._connect_info()
        assert info["lan_accessible"] is True
        assert info["has_token"] is True
        assert info["url"].startswith("http://") and "token=" in info["url"]
        # The phone URL must be encodable as a QR (what /api/qr.svg serves).
        m = qr.matrix(info["url"])
        assert len(m) >= 21
    finally:
        s.manager.storage.close()


def test_set_capacity_rejects_non_positive(tmp_path):
    cfg = Config()
    cfg.db_path = tmp_path / "h.db"
    cfg.transport.type = "simulator"
    m = Manager(cfg)
    try:
        with pytest.raises(ValueError):
            m.set_capacity("AA", 0)
        with pytest.raises(ValueError):
            m.set_capacity("AA", -5)
    finally:
        m.storage.close()
