"""Tests for the physical alerting (HardwareAlerter) using the command action."""

import sys
import time

import pytest

from kilovault.config import Config, HardwareConfig
from kilovault.hardware import HardwareAlerter


def _touch_cmd(path):
    # Cross-platform "create this file" shell command.
    if sys.platform == "win32":
        return f'type nul > "{path}"'
    return f"touch '{path}'"


def test_disabled_with_no_targets():
    h = HardwareAlerter(HardwareConfig())
    assert not h.enabled
    h.set_active(True)  # no-op, must not raise


def test_alert_on_none_disables(tmp_path):
    h = HardwareAlerter(HardwareConfig(alert_on="none", command=_touch_cmd(tmp_path / "x")))
    assert not h.enabled


def test_command_fires_once_on_edge(tmp_path):
    marker = tmp_path / "fired"
    h = HardwareAlerter(HardwareConfig(command=_touch_cmd(marker)))
    assert h.enabled
    h.set_active(True, [])
    for _ in range(50):
        if marker.exists():
            break
        time.sleep(0.05)
    assert marker.exists()
    # edge-triggered: a second activate while already active does not re-fire
    marker.unlink()
    h.set_active(True, [])
    time.sleep(0.3)
    assert not marker.exists()
    h.set_active(False)


def test_manager_drives_hardware_on_critical(tmp_path):
    from kilovault.alarms import Alarm
    from kilovault.manager import Manager

    marker = tmp_path / "siren"
    cfg = Config()
    cfg.db_path = tmp_path / "h.db"
    cfg.transport.type = "simulator"
    cfg.retention_days = 0
    cfg.hardware.command = _touch_cmd(marker)
    m = Manager(cfg)
    try:
        # a critical alarm should activate the hardware
        m._active_alarms["AA"] = [Alarm("AA", "VOLT_HIGH", "critical", "high")]
        m._update_hardware()
        for _ in range(50):
            if marker.exists():
                break
            time.sleep(0.05)
        assert marker.exists()
        # clearing all alarms deactivates without error
        m._active_alarms["AA"] = []
        m._update_hardware()
        assert m.hardware._active is False
    finally:
        m.storage.close()


def test_warning_only_does_not_trigger_critical_mode(tmp_path):
    from kilovault.alarms import Alarm
    from kilovault.manager import Manager

    marker = tmp_path / "siren2"
    cfg = Config()
    cfg.db_path = tmp_path / "h2.db"
    cfg.transport.type = "simulator"
    cfg.retention_days = 0
    cfg.hardware.alert_on = "critical"
    cfg.hardware.command = _touch_cmd(marker)
    m = Manager(cfg)
    try:
        m._active_alarms["AA"] = [Alarm("AA", "TEMP_HIGH", "warning", "warm")]
        m._update_hardware()
        time.sleep(0.3)
        assert not marker.exists()  # warning must not trip a critical-only alert
    finally:
        m.storage.close()
