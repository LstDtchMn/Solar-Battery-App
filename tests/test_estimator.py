"""Tests for derived metrics and bank aggregation."""

import pytest

from kilovault.estimator import BatteryState, bank_summary
from kilovault.protocol import BatterySample


def make_sample(ts, current, soc=50.0, voltage=13.0, cap=100.0, cells=None):
    s = BatterySample(
        voltage=voltage, current=current, total_capacity=cap, cycles=100,
        soc=soc, temperature=22.0, status=0x100,
        cell_voltages=(cells or [3.25, 3.25, 3.25, 3.25]) + [0.0] * 12,
    )
    s.timestamp = ts
    s.address = "AA"
    return s


def test_energy_integration_charge():
    # 3600 one-second steps at +10 A, 13 V -> 10 Ah, 130 Wh charged.
    st = BatteryState(address="AA")
    for i in range(3601):
        st.update(make_sample(i, 10.0, voltage=13.0))
    assert st.ah_charged == pytest.approx(10.0, abs=0.05)
    assert st.wh_charged == pytest.approx(130.0, abs=1.0)


def test_energy_integration_discharge():
    st2 = BatteryState(address="BB")
    for i in range(61):  # 60 one-second steps
        st2.update(make_sample(i, -36.0))  # -36 A
    # 60 s of 36 A discharge = 36 * 60/3600 = 0.6 Ah
    assert st2.ah_discharged == pytest.approx(0.6, abs=0.02)


def test_large_gap_is_ignored():
    st = BatteryState(address="AA")
    st.update(make_sample(0, 10.0))
    st.update(make_sample(10_000, 10.0))  # 10000s gap (disconnect) -> ignored
    assert st.ah_charged == pytest.approx(0.0, abs=1e-9)


def test_time_to_full_and_empty():
    st = BatteryState(address="AA")
    st.capacity_ah = 100.0
    st.update(make_sample(0, 10.0, soc=50.0))  # charging
    # remaining = 50 Ah, need 50 more at 10 A -> 5 h
    assert st.time_to_full_h == pytest.approx(5.0, abs=0.01)
    assert st.time_to_empty_h is None

    st2 = BatteryState(address="BB")
    st2.capacity_ah = 100.0
    st2.update(make_sample(0, -20.0, soc=50.0))  # discharging
    # remaining = 50 Ah at 20 A -> 2.5 h
    assert st2.time_to_empty_h == pytest.approx(2.5, abs=0.01)
    assert st2.time_to_full_h is None


def test_soh_estimate_decreases_with_cycles():
    st = BatteryState(address="AA")
    st.update(make_sample(0, 0.0))
    healthy = st.soh_estimate
    st.sample.cycles = 3500
    aged = st.soh_estimate
    assert healthy > aged
    assert aged == pytest.approx(80.0, abs=0.5)


def test_soh_continues_declining_past_rated_life():
    # Must not plateau at 80% — a 7000-cycle pack should read well below 80.
    st = BatteryState(address="AA")
    st.update(make_sample(0, 0.0))
    st.sample.cycles = 7000
    assert st.soh_estimate == pytest.approx(60.0, abs=0.5)
    st.sample.cycles = 20000
    assert st.soh_estimate == 0.0  # floored, not negative


def test_time_to_full_uses_capacity_override_not_pack():
    # Override 100 Ah while the pack reports 90 Ah; at 100% SoC there is no
    # remaining time-to-full regardless of the pack's reported capacity.
    st = BatteryState(address="AA")
    st.capacity_override = 100.0
    st.update(make_sample(0, 10.0, soc=100.0, cap=90.0))
    assert st.capacity_ah == pytest.approx(100.0)
    assert st.time_to_full_h == 0.0


def test_bank_summary_parallel_totals():
    a = BatteryState(address="A")
    a.update(make_sample(0, 10.0, soc=80.0, voltage=13.2, cap=100.0))
    b = BatteryState(address="B")
    b.update(make_sample(0, -5.0, soc=60.0, voltage=13.0, cap=200.0))
    summary = bank_summary([a, b])
    assert summary["online_count"] == 2
    assert summary["total_current"] == pytest.approx(5.0)  # 10 - 5
    # capacity-weighted SoC: (80*100 + 60*200)/300 = 66.67
    assert summary["soc"] == pytest.approx(66.7, abs=0.1)
    assert summary["total_capacity_ah"] == pytest.approx(300.0)


def test_bank_summary_no_data():
    st = BatteryState(address="A")
    summary = bank_summary([st])
    assert summary["online_count"] == 0
    assert summary["battery_count"] == 1


def test_crc_failed_frame_is_not_integrated():
    st = BatteryState(address="AA")
    st.update(make_sample(0, 10.0, soc=50.0))     # good baseline
    good = st.sample
    ah_before = st.ah_charged
    bad = make_sample(1, 9999.0, soc=50.0)        # absurd current...
    bad.crc_ok = False                             # ...on a corrupt frame
    st.update(bad)
    assert st.crc_errors == 1
    assert st.frames_received == 2                 # still counted
    assert st.ah_charged == ah_before             # but never integrated
    assert st.sample is good                       # and never displayed


def test_bank_remaining_consistent_with_capacity_override():
    st = BatteryState(address="A")
    st.capacity_override = 200.0                    # user says it's a 200 Ah pack
    st.update(make_sample(0, 0.0, soc=50.0, cap=100.0))  # pack reports 100 Ah
    summary = bank_summary([st])
    assert summary["total_capacity_ah"] == pytest.approx(200.0)
    # remaining must track the chosen capacity, not the pack value (=> 100, not 50)
    assert summary["remaining_capacity_ah"] == pytest.approx(100.0)


def test_bank_ignores_impossible_temperature():
    a = BatteryState(address="A")
    sa = make_sample(0, 0.0, soc=50.0)
    sa.temperature = -273.15                        # missing sensor sentinel
    a.update(sa)
    b = BatteryState(address="B")
    sb = make_sample(0, 0.0, soc=50.0)
    sb.temperature = 24.0
    b.update(sb)
    summary = bank_summary([a, b])
    assert summary["min_temperature"] == pytest.approx(24.0)  # -273 excluded
    assert summary["max_temperature"] == pytest.approx(24.0)
