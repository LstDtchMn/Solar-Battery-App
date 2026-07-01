"""Configuration for the KiloVault monitor.

User preferences live in an optional TOML file (read with the stdlib
``tomllib``). Everything has a sensible default so the program runs with no
config at all. Per-battery friendly names and capacities are stored in the
database (see :mod:`kilovault.storage`), not here, because they are edited from
the UI at runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore


DEFAULT_CONFIG_NAME = "config.toml"
DEFAULT_DB_NAME = "kilovault_history.db"


@dataclass
class AlarmConfig:
    enabled: bool = True
    # The manual states cells should stay within 300 mV of each other.
    cell_delta_warn: float = 0.30
    cell_delta_critical: float = 0.40
    # The pack's own BMS protects ~0 C / high temp; warn before it acts.
    temp_high: float = 45.0
    temp_low: float = 2.0
    soc_low: float = 15.0
    soc_critical: float = 8.0
    voltage_high: float = 14.6
    voltage_low: float = 11.5
    # Re-raise an alarm only after it clears; minimum seconds between repeats.
    repeat_seconds: float = 300.0
    notify_desktop: bool = True
    sound: bool = True


@dataclass
class TransportConfig:
    # "ble" | "serial" | "simulator"
    type: str = "ble"
    scan_timeout: float = 8.0
    # Only connect to these BLE addresses (empty = all HLX devices found).
    addresses: list = field(default_factory=list)
    # Connection supervision
    reconnect_seconds: float = 5.0
    stale_after_seconds: float = 15.0
    # Serial bridge (ESP32 -> PC)
    serial_port: str = ""
    serial_baud: int = 115200
    # Simulator
    sim_batteries: int = 2


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    # Bind to 0.0.0.0 to reach the dashboard from a phone on the cabin LAN.
    open_browser: bool = False


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path.cwd())
    db_path: Path = field(default_factory=lambda: Path.cwd() / DEFAULT_DB_NAME)
    log_interval: float = 10.0  # seconds between persisted history rows
    retention_days: float = 90.0  # history older than this is pruned (0 = keep all)
    transport: TransportConfig = field(default_factory=TransportConfig)
    alarms: AlarmConfig = field(default_factory=AlarmConfig)
    web: WebConfig = field(default_factory=WebConfig)

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: Optional[os.PathLike] = None) -> "Config":
        """Load config from ``path`` (or ./config.toml). Missing file -> defaults."""
        cfg = cls()
        p = Path(path) if path else Path.cwd() / DEFAULT_CONFIG_NAME
        if not p.exists() or tomllib is None:
            cfg.db_path = cfg.data_dir / DEFAULT_DB_NAME
            return cfg

        with open(p, "rb") as fh:
            raw = tomllib.load(fh)

        app = raw.get("app", {})
        if "data_dir" in app:
            cfg.data_dir = Path(app["data_dir"]).expanduser()
        cfg.db_path = (
            Path(app["db_path"]).expanduser()
            if "db_path" in app
            else cfg.data_dir / DEFAULT_DB_NAME
        )
        cfg.log_interval = float(app.get("log_interval", cfg.log_interval))
        cfg.retention_days = float(app.get("retention_days", cfg.retention_days))

        cfg.transport = _merge(TransportConfig(), raw.get("transport", {}))
        cfg.alarms = _merge(AlarmConfig(), raw.get("alarms", {}))
        cfg.web = _merge(WebConfig(), raw.get("web", {}))
        return cfg

    def to_dict(self) -> dict:
        d = asdict(self)
        d["data_dir"] = str(self.data_dir)
        d["db_path"] = str(self.db_path)
        return d


def _merge(obj, overrides: dict):
    """Apply a dict of overrides onto a dataclass instance, ignoring unknowns."""
    for k, v in overrides.items():
        if hasattr(obj, k):
            setattr(obj, k, v)
    return obj


#: A documented template written by ``kvmon init-config``.
CONFIG_TEMPLATE = """\
# KiloVault HLX+ Monitor configuration. All values are optional.

[app]
# Where the history database and exports are stored.
# data_dir = "C:/Users/you/kilovault"
log_interval = 10          # seconds between rows written to history
retention_days = 90        # delete history older than this (0 = keep everything)

[transport]
type = "ble"               # "ble" | "serial" | "simulator"
scan_timeout = 8.0         # BLE scan time (seconds)
# addresses = ["AA:BB:CC:DD:EE:FF"]   # restrict to specific batteries
reconnect_seconds = 5.0
stale_after_seconds = 15.0

# When using an ESP32 bridge instead of the PC's own Bluetooth:
# type = "serial"
# serial_port = "COM3"     # e.g. COM3 on Windows, /dev/ttyUSB0 on Linux
# serial_baud = 115200

# To try the app with no hardware:
# type = "simulator"
# sim_batteries = 2

[web]
host = "127.0.0.1"         # use "0.0.0.0" to reach it from a phone on the LAN
port = 8765
open_browser = false

[alarms]
enabled = true
cell_delta_warn = 0.30     # volts; manual says keep cells within 300 mV
cell_delta_critical = 0.40
temp_high = 45.0
temp_low = 2.0
soc_low = 15.0
soc_critical = 8.0
voltage_high = 14.6
voltage_low = 11.5
notify_desktop = true
sound = true
"""
