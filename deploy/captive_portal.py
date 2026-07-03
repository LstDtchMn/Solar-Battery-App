#!/usr/bin/env python3
"""Minimal captive-portal responder for the KiloVault cabin hotspot.

When a phone joins a Wi-Fi network, its OS immediately probes a well-known URL to
check for internet (Apple: captive.apple.com, Android: connectivitycheck.*,
Windows: msftconnecttest.com). With the hotspot's DNS pointing every name at the
Pi, those probes land here — and because we DON'T return the "success" response
the OS expects, it pops up a login sheet showing this page, which sends the phone
straight to the battery dashboard.

Standard library only. Reads the port/token/host from the monitor's config so the
link (and its access token) are always correct.
"""

import html
import http.server
import socketserver
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None

CONFIG = sys.argv[1] if len(sys.argv) > 1 else "/home/pi/kilovault/config.toml"


def _load_target():
    port, token, host, data_dir = 8765, "", "10.42.0.1", "/home/pi/kilovault"
    if tomllib and Path(CONFIG).exists():
        try:
            with open(CONFIG, "rb") as fh:
                d = tomllib.load(fh)
            w, a = d.get("web", {}), d.get("app", {})
            port = w.get("port", port)
            token = (w.get("token") or "").strip()
            host = (w.get("advertised_host") or host).strip()
            data_dir = a.get("data_dir", data_dir)
        except Exception:
            pass
    if not token:  # fall back to the auto-generated persistent token
        p = Path(data_dir) / ".web_token"
        if p.exists():
            try:
                token = p.read_text().strip()
            except Exception:
                token = ""
    q = f"?token={token}" if token else ""
    return f"http://{host}:{port}/{q}"


DASH = _load_target()
_DASH_ESC = html.escape(DASH, quote=True)
PAGE = (
    "<!doctype html><html><head><meta charset=utf-8>"
    "<meta name=viewport content='width=device-width,initial-scale=1'>"
    f"<meta http-equiv=refresh content='0; url={_DASH_ESC}'>"
    "<title>KiloVault Monitor</title><style>"
    "body{background:#0e1116;color:#e7ecf3;font-family:-apple-system,BlinkMacSystemFont,"
    "'Segoe UI',sans-serif;text-align:center;padding:3rem 1rem}"
    "a{display:inline-block;margin-top:1rem;padding:.9rem 1.4rem;background:#38d39f;"
    "color:#0d1117;border-radius:10px;text-decoration:none;font-weight:700;font-size:1.1rem}"
    "</style></head><body><h2>🔋 KiloVault Monitor</h2>"
    "<p>Opening your battery dashboard…</p>"
    f"<a href='{_DASH_ESC}'>Open the dashboard</a></body></html>"
).encode("utf-8")


class Handler(http.server.BaseHTTPRequestHandler):
    def _portal(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(PAGE)

    do_GET = _portal
    do_POST = _portal
    do_HEAD = _portal

    def log_message(self, *args):  # keep the journal quiet
        pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with Server(("0.0.0.0", 80), Handler) as srv:
        srv.serve_forever()
