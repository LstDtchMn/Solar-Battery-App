"""Collect a diagnostics bundle the user can email for support.

Everything here is local and offline. The bundle is a single .zip containing
the log file(s), system/version info, the current configuration, and a summary
of the database (device list + row counts) — enough to troubleshoot a setup or
connection problem without remote access.
"""

from __future__ import annotations

import json
import platform
import sys
import time
import zipfile
from pathlib import Path
from typing import Optional

from .config import Config
from .logging_setup import LOG_FILENAME


def collect_info(cfg: Config) -> dict:
    import kilovault

    info = {
        "app_version": kilovault.__version__,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "frozen_exe": bool(getattr(sys, "frozen", False)),
        "transport": cfg.transport.type,
        "serial_port": cfg.transport.serial_port,
        "db_path": str(cfg.db_path),
        "data_dir": str(cfg.data_dir),
        "web": {"host": cfg.web.host, "port": cfg.web.port},
    }
    for mod in ("bleak", "serial"):
        try:
            m = __import__(mod)
            info[f"{mod}_version"] = getattr(m, "__version__", "installed")
        except Exception:
            info[f"{mod}_version"] = None

    info["serial_ports"] = list_serial_ports()

    # Database summary (best-effort).
    try:
        from .storage import Storage

        st = Storage(cfg.db_path)
        devices = st.get_devices()
        info["devices"] = list(devices.values())
        with st._lock:  # noqa: SLF001 - read row counts for the report
            info["sample_rows"] = st._conn.execute(
                "SELECT COUNT(*) FROM samples"
            ).fetchone()[0]
            info["event_rows"] = st._conn.execute(
                "SELECT COUNT(*) FROM events"
            ).fetchone()[0]
        info["recent_events"] = st.recent_events(limit=30)
        st.close()
    except Exception as exc:
        info["database_error"] = str(exc)

    return info


def list_serial_ports() -> list:
    """List available serial ports (for ESP32 bridge setup). Best-effort."""
    try:
        from serial.tools import list_ports

        return [
            {"device": p.device, "description": p.description,
             "hwid": getattr(p, "hwid", "")}
            for p in list_ports.comports()
        ]
    except Exception:
        return []


def build_zip(cfg: Config, out_path: Optional[Path] = None) -> Path:
    if out_path is None:
        out_path = cfg.data_dir / f"kilovault_diagnostics_{int(time.time())}.zip"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    info = collect_info(cfg)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("system_info.json", json.dumps(info, indent=2, default=str))
        # All rotated log files.
        for logfile in sorted(cfg.data_dir.glob(LOG_FILENAME + "*")):
            try:
                z.write(logfile, logfile.name)
            except Exception:
                pass
        # The config file, if present (it holds no secrets).
        for name in ("config.toml", "config.local.toml"):
            p = cfg.data_dir / name
            if p.exists():
                try:
                    z.write(p, name)
                except Exception:
                    pass
    return out_path
