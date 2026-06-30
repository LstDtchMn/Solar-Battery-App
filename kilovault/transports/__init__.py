"""Transport implementations and a factory keyed by config."""

from __future__ import annotations

from typing import Optional

from ..config import TransportConfig
from .base import (  # noqa: F401
    ConnectionState,
    DiscoveredBattery,
    Transport,
    SampleCallback,
    StateCallback,
)
from .simulator import SimulatorTransport


def build_transport(
    cfg: TransportConfig,
    on_sample: SampleCallback,
    on_state: Optional[StateCallback] = None,
) -> Transport:
    """Construct the transport named by ``cfg.type``."""
    kind = (cfg.type or "ble").lower()
    if kind == "simulator":
        return SimulatorTransport(on_sample, on_state, count=cfg.sim_batteries)
    if kind == "serial":
        from .serial_bridge import SerialBridgeTransport

        tcp = cfg.serial_port if ":" in cfg.serial_port and "/" not in cfg.serial_port else None
        return SerialBridgeTransport(
            on_sample, on_state,
            port=cfg.serial_port, baud=cfg.serial_baud, tcp=tcp,
        )
    if kind == "ble":
        from .ble import BleTransport

        return BleTransport(
            on_sample, on_state,
            addresses=cfg.addresses, scan_timeout=cfg.scan_timeout,
            reconnect_seconds=cfg.reconnect_seconds,
        )
    raise ValueError(f"unknown transport type: {cfg.type!r}")
