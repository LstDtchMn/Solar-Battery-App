"""Tests for the dashboard's auth / CSRF / token gating (pure, no sockets)."""

import pytest

from kilovault.config import Config
from kilovault.manager import Manager
from kilovault.server.app import DashboardServer


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
