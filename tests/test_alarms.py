"""Tests for the alarm engine: BMS flags, thresholds and hysteresis."""

import pytest

from kilovault.alarms import AlarmEngine, Notifier
from kilovault.config import AlarmConfig
from kilovault.estimator import BatteryState
from kilovault.protocol import ALARM_BITS, BatterySample


class SilentNotifier(Notifier):
    def __init__(self):
        super().__init__(sound=False, desktop=False)
        self.fired = []

    def notify(self, alarm):
        self.fired.append(alarm)


def state_with(**kw):
    s = BatterySample(
        voltage=kw.get("voltage", 13.2),
        current=kw.get("current", 0.0),
        total_capacity=100.0,
        cycles=10,
        soc=kw.get("soc", 80.0),
        temperature=kw.get("temperature", 22.0),
        status=kw.get("status", 0x100),
        cell_voltages=kw.get("cells", [3.30, 3.30, 3.30, 3.30]) + [0.0] * 12,
    )
    s.address = "AA"
    st = BatteryState(address="AA")
    st.update(s)
    return st


def engine():
    return AlarmEngine(AlarmConfig(), storage=None, notifier=SilentNotifier())


def test_bms_flag_raises_alarm():
    eng = engine()
    st = state_with(status=0x100 | ALARM_BITS["HV"][0])
    active = eng.evaluate(st)
    codes = [a.code for a in active]
    assert "BMS_HV" in codes
    assert any(a.severity == "critical" for a in active if a.code == "BMS_HV")


def test_cell_imbalance_warning_and_critical():
    eng = engine()
    st = state_with(cells=[3.20, 3.55, 3.30, 3.30])  # delta 350 mV
    active = eng.evaluate(st)
    codes = {a.code: a.severity for a in active}
    assert codes.get("CELL_IMBALANCE") == "warning"

    st2 = state_with(cells=[3.10, 3.55, 3.30, 3.30])  # delta 450 mV
    active2 = eng.evaluate(state_with(cells=[3.10, 3.55, 3.30, 3.30]))
    codes2 = {a.code: a.severity for a in active2}
    assert codes2.get("CELL_IMBALANCE") == "critical"


def test_low_temperature_alarm():
    eng = engine()
    st = state_with(temperature=-1.0)
    codes = [a.code for a in eng.evaluate(st)]
    assert "TEMP_LOW" in codes


def test_soc_low_and_critical():
    eng = engine()
    assert "SOC_LOW" in [a.code for a in eng.evaluate(state_with(soc=12.0))]
    crit = eng.evaluate(state_with(soc=5.0))
    rec = {a.code: a.severity for a in crit}
    assert rec.get("SOC_LOW") == "critical"


def test_voltage_envelope():
    eng = engine()
    assert "VOLT_HIGH" in [a.code for a in eng.evaluate(state_with(voltage=14.8))]
    assert "VOLT_LOW" in [a.code for a in eng.evaluate(state_with(voltage=11.0))]


def test_hysteresis_fires_once_then_clears():
    notifier = SilentNotifier()
    eng = AlarmEngine(AlarmConfig(), storage=None, notifier=notifier)
    cold = state_with(temperature=-1.0)
    eng.evaluate(cold)
    eng.evaluate(state_with(temperature=-1.0))  # still cold -> no new notify
    assert len(notifier.fired) == 1  # only fired once
    assert "TEMP_LOW" in eng.active_for("AA")
    # warms up -> alarm clears
    eng.evaluate(state_with(temperature=22.0))
    assert "TEMP_LOW" not in eng.active_for("AA")


def test_flapping_condition_does_not_spam_notifications():
    notifier = SilentNotifier()
    eng = AlarmEngine(AlarmConfig(), storage=None, notifier=notifier)
    # Oscillate around the low-SoC threshold many times within repeat_seconds.
    for _ in range(6):
        eng.evaluate(state_with(soc=12.0))   # below soc_low -> active
        eng.evaluate(state_with(soc=25.0))   # recovered -> clears
    # Only the first activation should have notified.
    assert len(notifier.fired) == 1


def test_cleared_alarm_not_reported_active():
    eng = engine()
    eng.evaluate(state_with(soc=12.0))
    assert "SOC_LOW" in eng.active_for("AA")
    eng.evaluate(state_with(soc=25.0))
    assert "SOC_LOW" not in eng.active_for("AA")


def test_no_alarms_when_disabled():
    cfg = AlarmConfig(enabled=False)
    eng = AlarmEngine(cfg, storage=None, notifier=SilentNotifier())
    st = state_with(voltage=20.0, temperature=-10.0, soc=1.0)
    assert eng.evaluate(st) == []


def test_normal_state_has_no_alarms():
    eng = engine()
    st = state_with()  # all nominal
    assert eng.evaluate(st) == []
