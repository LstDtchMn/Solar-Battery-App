"""Local web dashboard — a minimal asyncio HTTP/1.1 server with Server-Sent
Events. No web framework, so it runs in the Manager's event loop and adds no
dependencies. All assets are served from disk (bundled), so it works with no
internet access.

Endpoints
---------
GET  /                      dashboard HTML
GET  /static/<file>         CSS / JS assets
GET  /api/snapshot          full current state (JSON)
GET  /api/history           ?address=&minutes=&limit=  time-series (JSON)
GET  /api/events            ?address=&limit=  alarm event log (JSON)
GET  /api/stream            Server-Sent Events live feed
GET  /api/export.csv        ?address=&minutes=  CSV download
POST /api/rename            {"address":..., "name":...}
POST /api/capacity          {"address":..., "capacity_ah":...}
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Optional, Tuple

from ..config import TransportConfig
from ..manager import Manager

log = logging.getLogger(__name__)

mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("image/svg+xml", ".svg")


def _resolve_static_dir() -> Path:
    """Find the bundled static assets, including inside a PyInstaller .exe."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidate = base / "kilovault" / "server" / "static"
        if candidate.exists():
            return candidate
    return Path(__file__).parent / "static"


STATIC_DIR = _resolve_static_dir()


_LOOPBACK = {"127.0.0.1", "localhost", "::1", ""}
_MAX_BODY = 64 * 1024  # POST bodies are tiny JSON objects


class DashboardServer:
    def __init__(self, manager: Manager, host: str = "127.0.0.1", port: int = 8765):
        self.manager = manager
        self.host = host
        self.port = port
        self._server: Optional[asyncio.AbstractServer] = None
        # When exposed beyond loopback (e.g. --lan / 0.0.0.0), require a shared
        # token on every /api/* request so the LAN can't read data or control
        # the monitor without it. On loopback, no token (only this PC can reach).
        self.token: Optional[str] = (
            self._resolve_token() if host not in _LOOPBACK else None
        )
        self._allowed_hosts = {"127.0.0.1", "localhost", "::1"}
        if host not in ("0.0.0.0", "::") and host not in self._allowed_hosts:
            self._allowed_hosts.add(host)

    def _resolve_token(self) -> str:
        """A stable access token: an explicit config value, else a persistent
        auto-generated one stored in the data dir (so a phone's saved link keeps
        working across restarts)."""
        import secrets

        cfgtok = (getattr(self.manager.cfg.web, "token", "") or "").strip()
        if cfgtok:
            return cfgtok
        try:
            p = Path(self.manager.cfg.data_dir) / ".web_token"
            if p.exists():
                existing = p.read_text().strip()
                if existing:
                    return existing
            token = secrets.token_urlsafe(16)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(token)
            return token
        except Exception:
            return secrets.token_urlsafe(16)

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)

    async def serve_forever(self) -> None:
        await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    # ------------------------------------------------------------------
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            request_line = await reader.readline()
            if not request_line:
                writer.close()
                return
            parts = request_line.decode("latin1").split()
            if len(parts) < 2:
                await self._send(writer, 400, "text/plain", b"bad request")
                return
            method, raw_path = parts[0], parts[1]

            headers = {}
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                k, _, v = line.decode("latin1").partition(":")
                headers[k.strip().lower()] = v.strip()

            body = b""
            if "content-length" in headers:
                try:
                    n = int(headers["content-length"])
                except ValueError:
                    n = 0
                if n > _MAX_BODY:
                    return await self._send(writer, 413, "text/plain", b"payload too large")
                try:
                    body = await reader.readexactly(n) if n > 0 else b""
                except asyncio.IncompleteReadError:
                    body = b""

            path, _, query = raw_path.partition("?")
            params = urllib.parse.parse_qs(query)

            denied = self._auth_denied(method, path, params, headers)
            if denied is not None:
                return await self._send(writer, denied, "text/plain", b"forbidden")

            await self._route(method, path, params, body, writer)
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass
        except Exception as exc:  # never let one request kill the server
            try:
                await self._send(writer, 500, "text/plain",
                                 f"server error: {exc}".encode())
            except Exception:
                pass
        finally:
            if not writer.is_closing():
                try:
                    writer.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    def _auth_denied(self, method, path, params, headers):
        """Return an HTTP status to deny with, or None to allow.

        - Token mode (bound beyond loopback): every /api/* request must carry the
          shared token (query ``token`` or ``X-KV-Token`` header).
        - Loopback mode: no token, but state-changing POSTs must have a same-host
          Host header and (if present) same-origin Origin — this blocks DNS
          rebinding and cross-site POST CSRF from a page the user is browsing.
        """
        import hmac

        if self.token is not None:
            if path.startswith("/api/"):
                supplied = _one(params, "token") or headers.get("x-kv-token", "")
                if not hmac.compare_digest(supplied, self.token):
                    return 401
            return None

        if method == "POST":
            host = headers.get("host", "").rsplit(":", 1)[0].strip("[]").lower()
            if host and host not in self._allowed_hosts:
                return 403
            origin = headers.get("origin", "")
            if origin:
                oh = urllib.parse.urlparse(origin).hostname or ""
                if oh.lower() not in self._allowed_hosts:
                    return 403
        return None

    async def _route(self, method, path, params, body, writer):
        if method == "GET" and path == "/":
            return await self._send_file(writer, STATIC_DIR / "index.html")
        if method == "GET" and path == "/favicon.ico":
            return await self._send(writer, 204, "image/x-icon", b"")
        if method == "GET" and path.startswith("/static/"):
            name = path[len("/static/"):]
            target = (STATIC_DIR / name).resolve()
            if STATIC_DIR.resolve() in target.parents and target.exists():
                return await self._send_file(writer, target)
            return await self._send(writer, 404, "text/plain", b"not found")

        if method == "GET" and path == "/api/snapshot":
            return await self._json(writer, self.manager.snapshot())

        if method == "GET" and path == "/api/history":
            return await self._history(writer, params)

        if method == "GET" and path == "/api/summary":
            addr = _one(params, "address")
            if not addr:
                return await self._json(writer, {"error": "address required"}, 400)
            days = _num(params, "days", 30, int, 1, 366)
            return await self._json(writer, {
                "address": addr,
                "days": self.manager.storage.daily_summary(addr, days),
            })

        if method == "GET" and path == "/api/events":
            addr = _one(params, "address")
            limit = _num(params, "limit", 200, int, 1, 2000)
            return await self._json(
                writer, {"events": self.manager.storage.recent_events(addr, limit)}
            )

        if method == "GET" and path == "/api/diagnostics":
            return await self._json(writer, self.manager.diagnostics())

        if method == "GET" and path == "/api/preflight":
            return await self._json(writer, self._preflight())

        if method == "POST" and path == "/api/test-bluetooth":
            from ..transports.ble import quick_scan
            timeout = _num(params, "timeout", 5.0, float, 1.0, 15.0)
            return await self._json(writer, await quick_scan(timeout))

        if method == "GET" and path == "/api/log":
            return await self._serve_log(writer, params)

        if method == "GET" and path == "/api/diagnostics.zip":
            return await self._serve_diagnostics_zip(writer)

        if method == "POST" and path == "/api/transport":
            return await self._set_transport(writer, _json_body(body))

        if method == "GET" and path == "/api/stream":
            return await self._stream(writer)

        if method == "GET" and path == "/api/export.csv":
            return await self._export(writer, params)

        if method == "POST" and path == "/api/rename":
            data = _json_body(body)
            self.manager.rename(data.get("address", ""), data.get("name", ""))
            return await self._json(writer, {"ok": True})

        if method == "POST" and path == "/api/reset-counters":
            data = _json_body(body)
            self.manager.reset_counters(data.get("address") or None)
            return await self._json(writer, {"ok": True})

        if method == "GET" and path == "/api/thresholds":
            addr = _one(params, "address")
            return await self._json(writer, {
                "global": self.manager.global_thresholds(),
                "overrides": self.manager.get_thresholds(addr) if addr else {},
            })

        if method == "POST" and path == "/api/thresholds":
            data = _json_body(body)
            self.manager.set_thresholds(data.get("address", ""), data.get("overrides", {}))
            return await self._json(writer, {"ok": True})

        if method == "POST" and path == "/api/capacity":
            data = _json_body(body)
            try:
                cap = float(data.get("capacity_ah"))
                self.manager.set_capacity(data.get("address", ""), cap)
                return await self._json(writer, {"ok": True})
            except (TypeError, ValueError):
                return await self._json(writer, {"ok": False, "error": "bad capacity"}, 400)

        return await self._send(writer, 404, "text/plain", b"not found")

    # ------------------------------------------------------------------
    async def _history(self, writer, params):
        addr = _one(params, "address")
        if not addr:
            return await self._json(writer, {"error": "address required"}, 400)
        minutes = _num(params, "minutes", 180.0, float, 0.0, 525600.0)
        points = _num(params, "points", 2000, int, 100, 8000)
        since = time.time() - minutes * 60 if minutes > 0 else None
        rows = self.manager.storage.history(
            addr, since=since, limit=20000, max_points=points)
        return await self._json(writer, {"address": addr, "rows": rows})

    def _preflight(self) -> dict:
        """Environment capability check for the setup wizard."""
        from ..diagnostics import list_serial_ports
        result = {"transport": self.manager.cfg.transport.type}
        try:
            import bleak
            result["bluetooth"] = {"installed": True,
                                   "version": getattr(bleak, "__version__", "installed")}
        except Exception as exc:
            result["bluetooth"] = {"installed": False, "error": str(exc)}
        try:
            import serial  # noqa: F401
            result["serial"] = {"installed": True}
        except Exception as exc:
            result["serial"] = {"installed": False, "error": str(exc)}
        result["serial_ports"] = list_serial_ports()
        return result

    async def _set_transport(self, writer, data: dict):
        """Hot-swap the data source from the wizard."""
        kind = (data.get("type") or "").lower()
        if kind not in ("ble", "serial", "simulator"):
            return await self._json(writer, {"ok": False, "error": "bad type"}, 400)
        cur = self.manager.cfg.transport

        def _asint(v, default):
            try:
                return int(v)
            except (TypeError, ValueError):
                return default

        tcfg = TransportConfig(
            type=kind,
            serial_port=str(data.get("serial_port", cur.serial_port))[:120],
            serial_baud=_asint(data.get("serial_baud"), cur.serial_baud),
            sim_batteries=max(1, min(16, _asint(data.get("sim_batteries"), cur.sim_batteries))),
            scan_timeout=cur.scan_timeout,
            reconnect_seconds=cur.reconnect_seconds,
        )
        try:
            await self.manager.set_transport(tcfg)
            return await self._json(writer, {"ok": True, "transport": kind})
        except Exception as exc:
            log.exception("transport switch failed")
            return await self._json(writer, {"ok": False, "error": str(exc)}, 500)

    async def _serve_log(self, writer, params):
        """Return the tail of the log file as plain text."""
        from ..logging_setup import get_log_path
        kb = _num(params, "kb", 64, int, 1, 1024)
        path = get_log_path(self.manager.cfg.data_dir)
        if not path.exists():
            return await self._send(writer, 200, "text/plain",
                                    b"(log file not created yet)")

        def _read_tail():
            try:
                size = path.stat().st_size
                with open(path, "rb") as fh:
                    if size > kb * 1024:
                        fh.seek(size - kb * 1024)
                    return fh.read()
            except Exception as exc:
                return f"(could not read log: {exc})".encode()

        data = await asyncio.get_running_loop().run_in_executor(None, _read_tail)
        return await self._send(writer, 200, "text/plain; charset=utf-8", data)

    async def _serve_diagnostics_zip(self, writer):
        from ..diagnostics import build_zip
        try:
            loop = asyncio.get_running_loop()
            out = await loop.run_in_executor(None, build_zip, self.manager.cfg)
            data = await loop.run_in_executor(None, Path(out).read_bytes)
            Path(out).unlink(missing_ok=True)
        except Exception as exc:
            log.exception("diagnostics zip failed")
            return await self._send(writer, 500, "text/plain",
                                    f"diagnostics failed: {exc}".encode())
        fname = f"kilovault_diagnostics_{int(time.time())}.zip"
        await self._send(writer, 200, "application/zip", data,
                         extra_headers={"Content-Disposition":
                                        f'attachment; filename="{fname}"'})

    async def _export(self, writer, params):
        addr = _one(params, "address")
        minutes = _num(params, "minutes", 0.0, float, 0.0, 5256000.0)
        since = time.time() - minutes * 60 if minutes > 0 else None
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as tf:
            tmp = Path(tf.name)

        def _do_export():
            self.manager.storage.export_csv(tmp, address=addr or None, since=since)
            return tmp.read_bytes()

        data = await asyncio.get_running_loop().run_in_executor(None, _do_export)
        tmp.unlink(missing_ok=True)
        fname = f"kilovault_{addr or 'all'}_{int(time.time())}.csv"
        await self._send(
            writer, 200, "text/csv", data,
            extra_headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    async def _stream(self, writer):
        q = self.manager.subscribe()
        headers = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/event-stream\r\n"
            "Cache-Control: no-cache\r\n"
            "Connection: keep-alive\r\n"
            "\r\n"
        )
        try:
            writer.write(headers.encode())
            # Prime the client with a full snapshot.
            writer.write(self._sse(json.dumps(
                {"type": "snapshot", "snapshot": self.manager.snapshot()})))
            await writer.drain()
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    writer.write(self._sse(json.dumps(msg)))
                except asyncio.TimeoutError:
                    writer.write(b": keep-alive\n\n")  # comment heartbeat
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass
        finally:
            self.manager.unsubscribe(q)

    @staticmethod
    def _sse(data: str) -> bytes:
        return f"data: {data}\n\n".encode()

    # ------------------------------------------------------------------
    async def _json(self, writer, obj, status=200):
        await self._send(writer, status, "application/json",
                         json.dumps(obj, default=_default).encode())

    async def _send_file(self, writer, path: Path):
        if not path.exists():
            return await self._send(writer, 404, "text/plain", b"not found")
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        await self._send(writer, 200, ctype, path.read_bytes())

    async def _send(self, writer, status, ctype, body: bytes, extra_headers=None):
        reason = {200: "OK", 400: "Bad Request", 404: "Not Found",
                  500: "Internal Server Error"}.get(status, "OK")
        head = [
            f"HTTP/1.1 {status} {reason}",
            f"Content-Type: {ctype}",
            f"Content-Length: {len(body)}",
            # No CORS header: the dashboard is same-origin, so this keeps the
            # browser's same-origin policy protecting the data from other sites.
            "X-Content-Type-Options: nosniff",
            "Connection: close",
        ]
        for k, v in (extra_headers or {}).items():
            head.append(f"{k}: {v}")
        writer.write(("\r\n".join(head) + "\r\n\r\n").encode() + body)
        await writer.drain()


def _one(params, key, default=""):
    vals = params.get(key)
    return vals[0] if vals else default


def _num(params, key, default, cast, lo=None, hi=None):
    """Parse a numeric query param, falling back to default and clamping."""
    try:
        v = cast(_one(params, key, str(default)))
    except (ValueError, TypeError):
        v = default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def _json_body(body: bytes) -> dict:
    try:
        return json.loads(body.decode("utf-8")) if body else {}
    except (ValueError, UnicodeDecodeError):
        return {}


def _default(o):
    return str(o)
