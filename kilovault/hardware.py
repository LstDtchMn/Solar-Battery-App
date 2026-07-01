"""Physical alerting for an unattended off-grid install.

When an alarm fires, drive something the owner can hear/see even when nobody is
watching the screen: a USB serial relay (siren/light), a Raspberry Pi GPIO pin,
and/or a local command. Everything is local and offline.

The alerter is edge-triggered: it acts only when the active/inactive state
changes, so it never spams the relay or re-runs the command while an alarm
persists. Missing hardware or libraries fail quietly (logged), never crashing
the collector.
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import List, Optional

from .config import HardwareConfig

log = logging.getLogger(__name__)


class HardwareAlerter:
    def __init__(self, cfg: HardwareConfig):
        self.cfg = cfg
        self._active = False
        self._gpio_dev = None

    @property
    def enabled(self) -> bool:
        c = self.cfg
        return c.alert_on != "none" and bool(
            c.serial_relay_port or c.gpio_pin or c.command
        )

    def set_active(self, active: bool, alarms: Optional[List] = None) -> None:
        """Turn the physical alert on/off. Acts only on state transitions."""
        if not self.enabled or active == self._active:
            return
        self._active = active
        try:
            if active:
                self._activate(alarms or [])
            else:
                self._deactivate()
        except Exception:
            log.warning("hardware alert %s failed", "on" if active else "off",
                        exc_info=True)

    # ------------------------------------------------------------------
    def _activate(self, alarms) -> None:
        c = self.cfg
        codes = ",".join(getattr(a, "code", str(a)) for a in alarms)
        log.warning("HARDWARE ALERT ON (%s)", codes or "alarm")
        if c.serial_relay_port and c.serial_relay_on:
            self._serial_write(c.serial_relay_on)
        if c.gpio_pin:
            self._gpio(True)
        if c.command:
            self._run_command(codes)

    def _deactivate(self) -> None:
        c = self.cfg
        log.info("Hardware alert OFF")
        if c.serial_relay_port and c.serial_relay_off:
            self._serial_write(c.serial_relay_off)
        if c.gpio_pin:
            self._gpio(False)

    # ------------------------------------------------------------------
    def _serial_write(self, hex_bytes: str) -> None:
        import serial  # optional dependency (pyserial)

        data = bytes.fromhex(hex_bytes.replace(" ", ""))
        with serial.Serial(self.cfg.serial_relay_port,
                           self.cfg.serial_relay_baud,
                           timeout=1, write_timeout=2) as s:
            s.write(data)

    def _gpio(self, on: bool) -> None:
        try:
            from gpiozero import OutputDevice
        except Exception:
            log.warning("gpio_pin set but gpiozero is not installed "
                        "(pip install gpiozero); skipping")
            return
        if self._gpio_dev is None:
            self._gpio_dev = OutputDevice(
                self.cfg.gpio_pin, active_high=self.cfg.gpio_active_high,
                initial_value=False)
        self._gpio_dev.on() if on else self._gpio_dev.off()

    def _run_command(self, codes: str) -> None:
        env = {**os.environ, "KV_ALARMS": codes}
        subprocess.Popen(self.cfg.command, shell=True, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ------------------------------------------------------------------
    def close(self) -> None:
        try:
            if self._active:
                self._deactivate()
                self._active = False
            if self._gpio_dev is not None:
                self._gpio_dev.close()
                self._gpio_dev = None
        except Exception:
            log.debug("hardware close error", exc_info=True)
