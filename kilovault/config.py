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
class HardwareConfig:
    """Physical alerting for an unattended cabin: drive a siren/light/relay when
    an alarm fires. All actions are local and offline."""
    # When to activate: "critical" (default), "any" alarm, or "none" (disabled).
    alert_on: str = "critical"
    # USB serial relay board (works on Windows/Linux). Bytes are hex, e.g.
    # "A0 01 01 A2" to close, "A0 01 00 A1" to open (varies by board).
    serial_relay_port: str = ""
    serial_relay_baud: int = 9600
    serial_relay_on: str = ""
    serial_relay_off: str = ""
    # Raspberry Pi GPIO (needs gpiozero). 0 = disabled.
    gpio_pin: int = 0
    gpio_active_high: bool = True
    # A local shell command to run when an alert starts (e.g. play a sound).
    # The active alarm codes are passed in the KV_ALARMS environment variable.
    command: str = ""


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
    hardware: HardwareConfig = field(default_factory=HardwareConfig)

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
        cfg.hardware = _merge(HardwareConfig(), raw.get("hardware", {}))
        return cfg

    def validate(self) -> list:
        """Clamp out-of-range settings to safe values; return warning strings."""
        warnings = []

        def clamp(obj, attr, lo, hi, name):
            v = getattr(obj, attr)
            try:
                v = float(v)
            except (TypeError, ValueError):
                warnings.append(f"{name} is not a number; using default")
                return
            if v < lo or v > hi:
                nv = min(max(v, lo), hi)
                warnings.append(f"{name}={v} out of range [{lo},{hi}]; using {nv}")
                setattr(obj, attr, nv)  # float; port is re-cast to int below

        clamp(self, "log_interval", 0.1, 3600, "log_interval")
        clamp(self, "retention_days", 0, 36500, "retention_days")
        clamp(self.web, "port", 1, 65535, "web.port")
        a = self.alarms
        clamp(a, "temp_low", -40, 100, "alarms.temp_low")
        clamp(a, "temp_high", -40, 100, "alarms.temp_high")
        clamp(a, "soc_low", 0, 100, "alarms.soc_low")
        clamp(a, "soc_critical", 0, 100, "alarms.soc_critical")
        clamp(a, "voltage_low", 0, 60, "alarms.voltage_low")
        clamp(a, "voltage_high", 0, 60, "alarms.voltage_high")

        if a.temp_low >= a.temp_high:
            warnings.append("alarms.temp_low >= temp_high; disabling those alarms")
            a.temp_low, a.temp_high = -273.0, 999.0
        if a.voltage_low >= a.voltage_high:
            warnings.append("alarms.voltage_low >= voltage_high; disabling those alarms")
            a.voltage_low, a.voltage_high = 0.0, 999.0
        if a.soc_critical > a.soc_low:
            warnings.append("alarms.soc_critical > soc_low; swapping")
            a.soc_low, a.soc_critical = a.soc_critical, a.soc_low
        if a.cell_delta_warn > a.cell_delta_critical:
            warnings.append("alarms.cell_delta_warn > cell_delta_critical; swapping")
            a.cell_delta_warn, a.cell_delta_critical = a.cell_delta_critical, a.cell_delta_warn
        if self.transport.type not in ("ble", "serial", "simulator"):
            warnings.append(f"transport.type '{self.transport.type}' invalid; using 'ble'")
            self.transport.type = "ble"
        if self.hardware.alert_on not in ("critical", "any", "none"):
            warnings.append(f"hardware.alert_on '{self.hardware.alert_on}' invalid; using 'critical'")
            self.hardware.alert_on = "critical"
        self.web.port = int(self.web.port)  # port must be an integer
        return warnings

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

[hardware]
# Drive a physical siren/light/relay when an alarm fires (all local, offline).
alert_on = "critical"       # "critical" | "any" | "none"

# USB serial relay board (Windows or Linux). Bytes are hex; values vary by board.
# serial_relay_port = "COM5"       # or /dev/ttyUSB1
# serial_relay_baud = 9600
# serial_relay_on  = "A0 01 01 A2"  # command that closes the relay (siren ON)
# serial_relay_off = "A0 01 00 A1"  # command that opens the relay (siren OFF)

# Raspberry Pi GPIO (needs: pip install gpiozero). 0 = disabled.
# gpio_pin = 17
# gpio_active_high = true

# Or run any local command when an alert starts (active codes in $KV_ALARMS):
# command = "powershell -c (New-Object Media.SoundPlayer 'C:/siren.wav').PlaySync()"
"""
