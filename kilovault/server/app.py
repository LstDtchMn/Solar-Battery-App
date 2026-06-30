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


def _resolve_static_dir() -> Path:
    """Find the bundled static assets, including inside a PyInstaller .exe."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidate = base / "kilovault" / "server" / "static"
        if candidate.exists():
            return candidate
    return Path(__file__).parent / "static"


STATIC_DIR = _resolve_static_dir()


class DashboardServer:
    def __init__(self, manager: Manager, host: str = "127.0.0.1", port: int = 8765):
        self.manager = manager
        self.host = host
        self.port = port
        self._server: Optional[asyncio.AbstractServer] = None

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
                    body = await reader.readexactly(n) if n > 0 else b""
                except (ValueError, asyncio.IncompleteReadError):
                    body = b""

            path, _, query = raw_path.partition("?")
            params = urllib.parse.parse_qs(query)
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

        if method == "GET" and path == "/api/events":
            addr = _one(params, "address")
            limit = int(_one(params, "limit", "200"))
            return await self._json(
                writer, {"events": self.manager.storage.recent_events(addr, limit)}
            )

        if method == "GET" and path == "/api/diagnostics":
            return await self._json(writer, self.manager.diagnostics())

        if method == "GET" and path == "/api/preflight":
            return await self._json(writer, self._preflight())

        if method == "POST" and path == "/api/test-bluetooth":
            from ..transports.ble import quick_scan
            timeout = float(_one(params, "timeout", "5"))
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
        minutes = float(_one(params, "minutes", "180"))
        limit = int(_one(params, "limit", "3000"))
        since = time.time() - minutes * 60 if minutes > 0 else None
        rows = self.manager.storage.history(addr, since=since, limit=limit)
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
        tcfg = TransportConfig(
            type=kind,
            serial_port=data.get("serial_port", self.manager.cfg.transport.serial_port),
            serial_baud=int(data.get("serial_baud", self.manager.cfg.transport.serial_baud)),
            sim_batteries=int(data.get("sim_batteries", self.manager.cfg.transport.sim_batteries)),
            scan_timeout=self.manager.cfg.transport.scan_timeout,
            reconnect_seconds=self.manager.cfg.transport.reconnect_seconds,
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
        kb = int(_one(params, "kb", "64"))
        path = get_log_path(self.manager.cfg.data_dir)
        if not path.exists():
            return await self._send(writer, 200, "text/plain",
                                    b"(log file not created yet)")
        try:
            size = path.stat().st_size
            with open(path, "rb") as fh:
                if size > kb * 1024:
                    fh.seek(size - kb * 1024)
                data = fh.read()
        except Exception as exc:
            data = f"(could not read log: {exc})".encode()
        return await self._send(writer, 200, "text/plain; charset=utf-8", data)

    async def _serve_diagnostics_zip(self, writer):
        from ..diagnostics import build_zip
        try:
            out = build_zip(self.manager.cfg)
            data = Path(out).read_bytes()
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
        minutes = float(_one(params, "minutes", "0"))
        since = time.time() - minutes * 60 if minutes > 0 else None
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as tf:
            tmp = Path(tf.name)
        n = self.manager.storage.export_csv(tmp, address=addr or None, since=since)
        data = tmp.read_bytes()
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
            "Access-Control-Allow-Origin: *\r\n"
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
            "Access-Control-Allow-Origin: *",
            "Connection: close",
        ]
        for k, v in (extra_headers or {}).items():
            head.append(f"{k}: {v}")
        writer.write(("\r\n".join(head) + "\r\n\r\n").encode() + body)
        await writer.drain()


def _one(params, key, default=""):
    vals = params.get(key)
    return vals[0] if vals else default


def _json_body(body: bytes) -> dict:
    try:
        return json.loads(body.decode("utf-8")) if body else {}
    except (ValueError, UnicodeDecodeError):
        return {}


def _default(o):
    return str(o)
