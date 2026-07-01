"""Alarm evaluation and offline notification.

Two alarm sources are combined:

1. **BMS protection flags** decoded from the status word (HV, LV, OCC, OCD,
   SCD, and the four temperature alarms) — these are the pack's own
   protections.
2. **Threshold alarms** the original app never offered: cell imbalance (the
   manual says keep cells within 300 mV), high/low temperature, low SoC and
   out-of-range pack voltage.

Active alarms are logged to the event table (so there is finally a history) and
announced via a dependency-free notifier (terminal bell, OS sound, and a
best-effort desktop toast). The web dashboard also surfaces them live.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from .config import AlarmConfig
from .estimator import BatteryState
from .protocol import ALARM_BITS

# Which BMS protection flags are critical vs. warning.
_CRITICAL_BMS = {"HV", "LV", "OCC", "OCD", "SCD"}


@dataclass
class Alarm:
    address: str
    code: str
    severity: str  # "critical" | "warning" | "info"
    message: str


class Notifier:
    """Best-effort, offline, cross-platform notifications."""

    def __init__(self, sound: bool = True, desktop: bool = True):
        self.sound = sound
        self.desktop = desktop
        self._is_windows = platform.system() == "Windows"
        self._notify_send = shutil.which("notify-send")

    def notify(self, alarm: Alarm) -> None:
        line = f"[{alarm.severity.upper()}] {alarm.address}: {alarm.message}"
        # ASCII-only marker so a redirected log on a cp1252/cp850 Windows
        # console never raises UnicodeEncodeError.
        try:
            print(f"\a[!] {line}", file=sys.stderr, flush=True)  # bell + marker
        except Exception:
            pass
        if self.sound:
            self._beep(alarm.severity)
        if self.desktop:
            self._desktop(alarm)

    def _beep(self, severity: str) -> None:
        try:
            if self._is_windows:
                import winsound

                freq = 880 if severity == "critical" else 660
                winsound.Beep(freq, 350)
            else:
                sys.stdout.write("\a")
                sys.stdout.flush()
        except Exception:
            pass

    def _desktop(self, alarm: Alarm) -> None:
        title = f"KiloVault {alarm.severity}"
        try:
            if self._notify_send:
                subprocess.Popen(
                    [self._notify_send, "-u",
                     "critical" if alarm.severity == "critical" else "normal",
                     title, alarm.message],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            elif self._is_windows:
                # PowerShell balloon tip; fully offline, no extra packages.
                # Escape single quotes (double them) so an apostrophe in the
                # message can't break out of the PS string literal or inject.
                t = title.replace("'", "''")
                m = alarm.message.replace("'", "''")
                ps = (
                    "[reflection.assembly]::loadwithpartialname('System.Windows.Forms')"
                    "|Out-Null;$n=New-Object System.Windows.Forms.NotifyIcon;"
                    "$n.Icon=[System.Drawing.SystemIcons]::Warning;$n.Visible=$true;"
                    f"$n.ShowBalloonTip(8000,'{t}','{m}',"
                    "[System.Windows.Forms.ToolTipIcon]::Warning)"
                )
                subprocess.Popen(
                    ["powershell", "-NoProfile", "-Command", ps],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
        except Exception:
            pass


class AlarmEngine:
    """Stateful alarm evaluator with hysteresis and event logging."""

    def __init__(
        self,
        config: AlarmConfig,
        storage=None,
        notifier: Optional[Notifier] = None,
    ):
        self.cfg = config
        self.storage = storage
        self.notifier = notifier or Notifier(config.sound, config.notify_desktop)
        # (address, code) -> {"event_id", "raised", "last_notify"}
        self._active: Dict[Tuple[str, str], dict] = {}

    # ------------------------------------------------------------------
    def evaluate(self, state: BatteryState) -> List[Alarm]:
        """Update alarm state for one battery; return its active alarms."""
        if not self.cfg.enabled or state.sample is None:
            return []
        current = {a.code: a for a in self._conditions(state)}
        addr = state.address
        now = time.time()

        # Invariant: while a record exists, its event row stays OPEN. A record
        # lingers (``cleared`` set) for ``repeat_seconds`` after the condition
        # goes away, so brief flapping across a threshold is throttled instead of
        # spamming notifications and duplicate event rows. The event is closed
        # exactly once, when the record is finally forgotten.
        for code, alarm in current.items():
            key = (addr, code)
            rec = self._active.get(key)
            if rec is None:
                event_id = (
                    self.storage.raise_event(addr, code, alarm.severity, alarm.message)
                    if self.storage else None
                )
                self._active[key] = {"event_id": event_id, "raised": now,
                                     "last_notify": now, "cleared": None}
                self.notifier.notify(alarm)
            else:
                # Already tracked (active, or cooling down after a brief clear):
                # the same open event continues; re-notify only on the interval.
                rec["cleared"] = None
                if now - rec["last_notify"] >= self.cfg.repeat_seconds:
                    rec["last_notify"] = now
                    self.notifier.notify(alarm)

        # Start the cooldown for no-longer-active alarms; close+forget the event
        # only once the cooldown has fully elapsed.
        for key in list(self._active):
            if key[0] != addr or key[1] in current:
                continue
            rec = self._active[key]
            if rec.get("cleared") is None:
                rec["cleared"] = now  # keep the event open during the cooldown
            elif now - rec["cleared"] >= self.cfg.repeat_seconds:
                if self.storage and rec.get("event_id"):
                    self.storage.clear_event(rec["event_id"])
                del self._active[key]

        return list(current.values())

    @property
    def active_alarms(self) -> List[Alarm]:
        out = []
        for (addr, code), _rec in self._active.items():
            out.append(Alarm(addr, code, "", ""))
        return out

    def active_for(self, address: str) -> List[str]:
        return [
            code for (addr, code), rec in self._active.items()
            if addr == address and rec.get("cleared") is None
        ]

    # ------------------------------------------------------------------
    def _conditions(self, state: BatteryState) -> List[Alarm]:
        s = state.sample
        addr = state.address
        cfg = self.cfg
        out: List[Alarm] = []

        # 1) BMS protection flags.
        for code in s.alarms:
            sev = "critical" if code in _CRITICAL_BMS else "warning"
            desc = ALARM_BITS.get(code, (0, code))[1]
            out.append(Alarm(addr, f"BMS_{code}", sev, f"BMS protection: {desc}"))

        # 2) Cell imbalance (manual: keep within 300 mV).
        if s.cell_delta >= cfg.cell_delta_critical:
            out.append(Alarm(addr, "CELL_IMBALANCE", "critical",
                             f"Cell imbalance {s.cell_delta*1000:.0f} mV "
                             f"(cell {s.max_cell_index} high, {s.min_cell_index} low)"))
        elif s.cell_delta >= cfg.cell_delta_warn:
            out.append(Alarm(addr, "CELL_IMBALANCE", "warning",
                             f"Cell imbalance {s.cell_delta*1000:.0f} mV"))

        # 3) Temperature.
        if s.temperature >= cfg.temp_high:
            out.append(Alarm(addr, "TEMP_HIGH", "warning",
                             f"High temperature {s.temperature:.1f} °C"))
        if s.temperature <= cfg.temp_low:
            out.append(Alarm(addr, "TEMP_LOW", "warning",
                             f"Low temperature {s.temperature:.1f} °C — "
                             f"charging may be blocked near freezing"))

        # 4) State of charge.
        if s.soc <= cfg.soc_critical:
            out.append(Alarm(addr, "SOC_LOW", "critical",
                             f"State of charge critically low: {s.soc:.0f}%"))
        elif s.soc <= cfg.soc_low:
            out.append(Alarm(addr, "SOC_LOW", "warning",
                             f"State of charge low: {s.soc:.0f}%"))

        # 5) Pack voltage envelope.
        if s.voltage >= cfg.voltage_high:
            out.append(Alarm(addr, "VOLT_HIGH", "critical",
                             f"Pack voltage high: {s.voltage:.2f} V"))
        if s.voltage <= cfg.voltage_low:
            out.append(Alarm(addr, "VOLT_LOW", "critical",
                             f"Pack voltage low: {s.voltage:.2f} V"))

        return out
