"""Command-line entry point for the KiloVault HLX+ monitor.

    kvmon serve                 # web dashboard + logging (default transport)
    kvmon serve --simulate      # try it with no hardware
    kvmon serve --serial COM3   # use an ESP32 bridge on COM3
    kvmon scan                  # list nearby HLX+ batteries (BLE)
    kvmon monitor               # headless console monitor
    kvmon export out.csv        # export logged history to CSV
    kvmon init-config           # write a documented config.toml
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from . import __version__
from .config import Config, CONFIG_TEMPLATE, DEFAULT_CONFIG_NAME


def _make_console_utf8() -> None:
    """Make stdout/stderr tolerate non-ASCII output on legacy Windows consoles.

    Without this, glyphs like the degree sign or warning symbol raise
    UnicodeEncodeError on cp1252/cp850 code pages and abort the command.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _load_config(args) -> Config:
    cfg = Config.load(getattr(args, "config", None))
    # CLI overrides
    if getattr(args, "simulate", False):
        cfg.transport.type = "simulator"
    if getattr(args, "serial", None):
        cfg.transport.type = "serial"
        cfg.transport.serial_port = args.serial
    if getattr(args, "host", None):
        cfg.web.host = args.host
    if getattr(args, "port", None):
        cfg.web.port = args.port
    if getattr(args, "db", None):
        cfg.db_path = Path(args.db)
    return cfg


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------
async def _scan(cfg: Config, timeout: float) -> int:
    from .transports.ble import BleTransport

    async def noop(_):
        return None

    t = BleTransport(noop, scan_timeout=timeout)
    print(f"Scanning for KiloVault HLX+ batteries ({timeout:.0f}s)…")
    try:
        found = await t.discover(timeout)
    except ImportError:
        print("Bluetooth support is not installed. Run: pip install bleak")
        return 2
    except Exception as exc:  # no adapter, permissions, etc.
        print(f"Bluetooth scan failed: {exc}")
        print("Check that a Bluetooth adapter is present and enabled "
              "(on Linux, scanning may require elevated privileges).")
        return 2
    if not found:
        print("No HLX+ batteries found. Make sure they are awake (apply a load or "
              "charger) and Bluetooth is on.")
        return 1
    print(f"\nFound {len(found)} batter{'y' if len(found)==1 else 'ies'}:")
    for d in found:
        rssi = f"{d.rssi} dBm" if d.rssi is not None else "?"
        print(f"  {d.address}   {d.name:<22}  {rssi}")
    print("\nAdd these to config.toml under [transport].addresses to pin them.")
    return 0


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------
async def _serve(cfg: Config) -> int:
    from .manager import Manager
    from .server import DashboardServer

    manager = Manager(cfg)
    server = DashboardServer(manager, cfg.web.host, cfg.web.port)
    await server.start()
    await manager.start()

    host_display = "localhost" if cfg.web.host in ("127.0.0.1", "0.0.0.0", "::") else cfg.web.host
    url = f"http://{host_display}:{cfg.web.port}/"
    bind_url = f"http://{cfg.web.host}:{cfg.web.port}/"
    print("=" * 56)
    print(f"  KiloVault HLX+ Monitor  v{__version__}")
    print("=" * 56)
    print(f"  Open the dashboard:  {url}")
    if cfg.web.host in ("0.0.0.0", "::"):
        print("  On your phone/tablet (same Wi-Fi): http://<this-PC-IP>:"
              f"{cfg.web.port}/")
    print(f"  Data + log folder:   {cfg.data_dir}")
    print(f"  Source / mode:       {cfg.transport.type}")
    print("  Close this window (or press Ctrl+C) to stop.")
    print("=" * 56)

    if cfg.web.open_browser:
        try:
            import webbrowser
            webbrowser.open(bind_url if host_display != "localhost" else
                            f"http://127.0.0.1:{cfg.web.port}/")
        except Exception:
            pass

    try:
        await server._server.serve_forever()
    except asyncio.CancelledError:
        pass
    finally:
        await manager.stop()
    return 0


# ---------------------------------------------------------------------------
# monitor (headless console)
# ---------------------------------------------------------------------------
async def _monitor(cfg: Config) -> int:
    from .manager import Manager

    manager = Manager(cfg)

    async def printer():
        while True:
            await asyncio.sleep(2.0)
            snap = manager.snapshot()
            bank = snap["bank"]
            line = time.strftime("%H:%M:%S")
            if bank.get("online_count"):
                line += (f"  bank {bank.get('soc','—')}%  "
                         f"{bank.get('total_power','—')}W  "
                         f"{bank.get('avg_voltage','—')}V  "
                         f"{bank.get('online_count')}/{bank.get('battery_count')} online")
                if bank.get("alarms"):
                    line += "  ⚠ " + ",".join(bank["alarms"])
            else:
                line += "  waiting for data…"
            print(line)
            for b in snap["batteries"]:
                s = b.get("sample")
                if not s:
                    continue
                print(f"    {b['name']:<20} {s['voltage']:>6.2f}V {s['current']:>7.1f}A "
                      f"{s['soc']:>3.0f}% {s['temperature']:>5.1f}°C  Δ{round(s['cell_delta']*1000)}mV "
                      f"cells={s['cell_voltages']}")

    collector = asyncio.ensure_future(manager.run())
    pr = asyncio.ensure_future(printer())
    try:
        await asyncio.gather(collector, pr)
    except asyncio.CancelledError:
        pass
    finally:
        await manager.stop()
    return 0


# ---------------------------------------------------------------------------
# export / init-config
# ---------------------------------------------------------------------------
def _export(cfg: Config, out: str, address: str, minutes: float) -> int:
    from .storage import Storage

    st = Storage(cfg.db_path)
    since = time.time() - minutes * 60 if minutes > 0 else None
    n = st.export_csv(Path(out), address=address or None, since=since)
    st.close()
    print(f"Exported {n} rows to {out}")
    return 0


def _init_config(path: str) -> int:
    p = Path(path or DEFAULT_CONFIG_NAME)
    if p.exists():
        print(f"{p} already exists; not overwriting.")
        return 1
    p.write_text(CONFIG_TEMPLATE)
    print(f"Wrote {p}. Edit it to taste, then run: kvmon serve")
    return 0


def _diagnostics(cfg: Config, out) -> int:
    from .diagnostics import build_zip

    path = build_zip(cfg, Path(out) if out else None)
    print("Diagnostics bundle written to:")
    print(f"  {path}")
    print("\nEmail this file (it contains the log, your settings and system info)")
    print("so the problem can be diagnosed. It contains no passwords.")
    return 0


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kvmon", description="KiloVault HLX+ battery monitor")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("-c", "--config", help="path to config.toml")
    # No subcommand -> serve (so double-clicking the .exe just works).
    sub = p.add_subparsers(dest="cmd", required=False)

    sp = sub.add_parser("serve", help="run the web dashboard + logger")
    sp.add_argument("--simulate", action="store_true", help="use the hardware-free simulator")
    sp.add_argument("--serial", help="use an ESP32 serial bridge (e.g. COM3 or /dev/ttyUSB0)")
    sp.add_argument("--host", help="web bind host (use 0.0.0.0 for LAN access)")
    sp.add_argument("--lan", action="store_true", help="allow access from other devices on the LAN (binds 0.0.0.0)")
    sp.add_argument("--port", type=int, help="web port")
    sp.add_argument("--db", help="history database path")
    sp.add_argument("--open", dest="open_browser", action="store_true", help="open a browser")

    sm = sub.add_parser("scan", help="discover nearby HLX+ batteries over BLE")
    sm.add_argument("--timeout", type=float, default=8.0)

    mo = sub.add_parser("monitor", help="headless console monitor")
    mo.add_argument("--simulate", action="store_true")
    mo.add_argument("--serial")
    mo.add_argument("--db")

    ex = sub.add_parser("export", help="export logged history to CSV")
    ex.add_argument("out", help="output .csv path")
    ex.add_argument("--address", default="", help="limit to one battery address")
    ex.add_argument("--minutes", type=float, default=0, help="only the last N minutes")
    ex.add_argument("--db")

    ic = sub.add_parser("init-config", help="write a documented config.toml")
    ic.add_argument("path", nargs="?", default=DEFAULT_CONFIG_NAME)

    dg = sub.add_parser("diagnostics", help="write a support bundle (.zip) to email")
    dg.add_argument("out", nargs="?", help="output .zip path")
    dg.add_argument("--db")

    return p


def _run(argv) -> int:
    _make_console_utf8()
    args = build_parser().parse_args(argv)

    if args.cmd == "init-config":
        return _init_config(args.path)

    cfg = _load_config(args)
    if getattr(args, "open_browser", False):
        cfg.web.open_browser = True
    if getattr(args, "lan", False):
        cfg.web.host = "0.0.0.0"

    # A double-clicked .exe runs with no subcommand: behave like `serve --open`
    # and store data in a stable per-user folder (not next to the .exe).
    cmd = args.cmd or "serve"
    if args.cmd is None:
        cfg.web.open_browser = True
    if getattr(sys, "frozen", False) and not getattr(args, "db", None):
        base = Path.home() / "KiloVault Monitor"
        cfg.data_dir = base
        cfg.db_path = base / "kilovault_history.db"

    from .logging_setup import setup_logging, log_environment

    log_path = setup_logging(cfg.data_dir)
    if cmd in ("serve", "monitor"):
        log_environment(cfg)
        print(f"Log file: {log_path}")

    if cmd == "serve":
        return asyncio.run(_serve(cfg))
    if cmd == "scan":
        return asyncio.run(_scan(cfg, args.timeout))
    if cmd == "monitor":
        return asyncio.run(_monitor(cfg))
    if cmd == "export":
        return _export(cfg, args.out, args.address, args.minutes)
    if cmd == "diagnostics":
        return _diagnostics(cfg, getattr(args, "out", None))
    return 0


def main(argv=None) -> int:
    frozen = bool(getattr(sys, "frozen", False))
    try:
        return _run(argv)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except Exception as exc:  # surface fatal errors clearly for non-technical users
        import logging
        logging.getLogger("kilovault").critical("Fatal error", exc_info=True)
        print(f"\nERROR: {exc}")
        print("See the log file above for details, or run "
              "'kvmon diagnostics' to create a support bundle.")
        return 1
    finally:
        # When launched by double-clicking the .exe, keep the window open so the
        # user can read any message instead of it vanishing instantly.
        if frozen:
            try:
                input("\nPress Enter to close this window...")
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
