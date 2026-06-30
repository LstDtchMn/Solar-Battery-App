"""Derived metrics and whole-bank aggregation.

The original app showed one battery at a time and computed nothing beyond what
the BMS reported. This module adds, per battery and for the whole bank:

- instantaneous power and a smoothed (EMA) power,
- energy integration (Wh in / out, Ah throughput) by coulomb counting,
- time-to-full / time-to-empty estimates,
- a cycle-based State-of-Health estimate (clearly labelled an estimate),
- bank totals (parallel-bank assumption: sum current/power/Ah, weighted SoC).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .protocol import BatterySample
from .transports.base import ConnectionState

# LiFePO4 reaches roughly 80% capacity near ~3500 full cycles; a coarse linear
# model is enough for a "health" hint and is labelled an estimate in the UI.
_CYCLE_LIFE_TO_80PCT = 3500.0


@dataclass
class BatteryState:
    address: str
    name: str = ""
    sample: Optional[BatterySample] = None
    connection: Optional[ConnectionState] = None
    capacity_ah: float = 100.0
    #: A user-configured capacity (Ah). When set it wins over the pack-reported
    #: value, so a manual override is not clobbered by the next status frame.
    capacity_override: Optional[float] = None

    # Energy / charge integration.
    wh_charged: float = 0.0
    wh_discharged: float = 0.0
    ah_charged: float = 0.0
    ah_discharged: float = 0.0
    session_start: float = field(default_factory=time.time)
    _last_ts: Optional[float] = None
    ema_power: float = 0.0

    # -- ingest ---------------------------------------------------------
    def mark_disconnected(self) -> None:
        """Reset the integration clock so the post-reconnect gap is not counted."""
        self._last_ts = None

    def update(self, sample: BatterySample) -> None:
        if self.capacity_override is not None:
            self.capacity_ah = self.capacity_override
        elif sample.total_capacity and sample.total_capacity > 0:
            self.capacity_ah = sample.total_capacity
        ts = sample.timestamp or time.time()
        if self._last_ts is not None:
            dt = ts - self._last_ts
            # Integrate brief sampling stalls but ignore long gaps. _last_ts is
            # reset on disconnect, so a true reconnect gap is never counted here.
            if 0 < dt <= 120:
                hours = dt / 3600.0
                ah = sample.current * hours
                wh = sample.power * hours
                if sample.current >= 0:
                    self.ah_charged += ah
                    self.wh_charged += wh
                else:
                    self.ah_discharged += -ah
                    self.wh_discharged += -wh
        self._last_ts = ts
        # Exponential moving average for a stable power read.
        alpha = 0.2
        self.ema_power = (1 - alpha) * self.ema_power + alpha * sample.power
        self.sample = sample
        if sample.name:
            self.name = sample.name

    # -- derived --------------------------------------------------------
    @property
    def soh_estimate(self) -> float:
        """Coarse State-of-Health % from cycle count (LiFePO4 model).

        ~20% fade by the rated cycle life, continuing to decline beyond it (so a
        well-worn pack does not plateau at a misleadingly healthy 80%).
        """
        if not self.sample:
            return 100.0
        fade = (self.sample.cycles / _CYCLE_LIFE_TO_80PCT) * 20.0
        return round(max(0.0, 100.0 - fade), 1)

    @property
    def time_to_full_h(self) -> Optional[float]:
        if not self.sample or self.sample.current <= 0.1:
            return None
        # Derive from the chosen capacity and SoC so a user capacity override
        # and the pack-reported capacity never get mixed.
        remaining = self.capacity_ah * (1.0 - self.sample.soc / 100.0)
        if remaining <= 0:
            return 0.0
        return round(remaining / self.sample.current, 2)

    @property
    def time_to_empty_h(self) -> Optional[float]:
        if not self.sample or self.sample.current >= -0.1:
            return None
        remaining = self.capacity_ah * (self.sample.soc / 100.0)
        return round(remaining / abs(self.sample.current), 2)

    def to_dict(self) -> dict:
        d = {
            "address": self.address,
            "name": self.name or self.address,
            "capacity_ah": round(self.capacity_ah, 1),
            "wh_charged": round(self.wh_charged, 1),
            "wh_discharged": round(self.wh_discharged, 1),
            "ah_charged": round(self.ah_charged, 2),
            "ah_discharged": round(self.ah_discharged, 2),
            "ema_power": round(self.ema_power, 1),
            "soh_estimate": self.soh_estimate,
            "time_to_full_h": self.time_to_full_h,
            "time_to_empty_h": self.time_to_empty_h,
            "session_seconds": round(time.time() - self.session_start),
        }
        if self.connection:
            d["connected"] = self.connection.connected
            d["rssi"] = self.connection.rssi
            d["model"] = self.connection.model
            d["serial"] = self.connection.serial
            d["firmware"] = self.connection.firmware
            d["last_seen"] = self.connection.last_seen
            d["error"] = self.connection.error
        if self.sample:
            d["sample"] = self.sample.to_dict()
        return d


def bank_summary(states: List[BatteryState]) -> dict:
    """Aggregate a parallel battery bank into one set of totals."""
    live = [s for s in states if s.sample is not None]
    if not live:
        return {
            "battery_count": len(states),
            "online_count": 0,
        }

    total_current = sum(s.sample.current for s in live)
    total_power = sum(s.sample.power for s in live)
    total_capacity = sum(s.capacity_ah for s in live)
    total_remaining = sum(s.sample.remaining_capacity for s in live)
    weighted_soc = (
        sum(s.sample.soc * s.capacity_ah for s in live) / total_capacity
        if total_capacity else 0.0
    )
    avg_voltage = sum(s.sample.voltage for s in live) / len(live)

    all_cells = [c for s in live for c in s.sample.active_cells]
    min_cell = min(all_cells) if all_cells else 0.0
    max_cell = max(all_cells) if all_cells else 0.0

    alarms = sorted({a for s in live for a in s.sample.alarms})
    temps = [s.sample.temperature for s in live]

    return {
        "battery_count": len(states),
        "online_count": len(live),
        "avg_voltage": round(avg_voltage, 3),
        "total_current": round(total_current, 2),
        "total_power": round(total_power, 1),
        "total_charging_power": round(sum(s.sample.charging_power for s in live), 1),
        "total_discharging_power": round(sum(s.sample.discharging_power for s in live), 1),
        "total_capacity_ah": round(total_capacity, 1),
        "remaining_capacity_ah": round(total_remaining, 1),
        "soc": round(weighted_soc, 1),
        "min_temperature": round(min(temps), 1),
        "max_temperature": round(max(temps), 1),
        "min_cell": round(min_cell, 3),
        "max_cell": round(max_cell, 3),
        "bank_cell_delta": round(max_cell - min_cell, 3),
        "alarms": alarms,
        "wh_charged": round(sum(s.wh_charged for s in live), 1),
        "wh_discharged": round(sum(s.wh_discharged for s in live), 1),
    }
