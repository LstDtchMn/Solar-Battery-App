"""Tests for SQLite storage: samples, devices, events and export."""

import time

import pytest

from kilovault.protocol import BatterySample
from kilovault.storage import Storage


def make_sample(addr="AA", ts=None, soc=80.0):
    s = BatterySample(
        voltage=13.2, current=5.0, total_capacity=100.0, cycles=10,
        soc=soc, temperature=22.0, status=0x100,
        cell_voltages=[3.30, 3.31, 3.29, 3.30] + [0.0] * 12,
    )
    s.address = addr
    s.name = "Test"
    s.timestamp = ts or time.time()
    return s


@pytest.fixture
def store(tmp_path):
    st = Storage(tmp_path / "t.db")
    yield st
    st.close()


def test_insert_and_history(store):
    base = time.time()
    for i in range(5):
        store.insert_sample(make_sample(ts=base + i, soc=80 + i))
    rows = store.history("AA", limit=10)
    assert len(rows) == 5
    # returned in ascending ts order
    assert rows[0]["ts"] <= rows[-1]["ts"]
    assert "voltage" in rows[0]


def test_latest(store):
    base = time.time()
    store.insert_sample(make_sample(ts=base, soc=50))
    store.insert_sample(make_sample(ts=base + 5, soc=55))
    latest = store.latest("AA")
    assert latest["soc"] == pytest.approx(55)


def test_device_registry_and_rename(store):
    store.upsert_device("AA", name="12V100Ah-1", model="HLX+", serial="123")
    dev = store.get_device("AA")
    assert dev["name"] == "12V100Ah-1"
    assert dev["model"] == "HLX+"
    store.set_device_name("AA", "Cabin Bank")
    assert store.get_device("AA")["name"] == "Cabin Bank"
    store.set_device_capacity("AA", 150.0)
    assert store.get_device("AA")["capacity_ah"] == pytest.approx(150.0)


def test_device_upsert_preserves_name(store):
    store.set_device_name("AA", "My Battery")
    # a later upsert with an empty/auto name must not clobber the user's name
    store.upsert_device("AA", name="AA", model="HLX+")
    assert store.get_device("AA")["name"] == "My Battery"


def test_events_lifecycle(store):
    eid = store.raise_event("AA", "TEMP_LOW", "warning", "cold")
    events = store.recent_events("AA")
    assert len(events) == 1
    assert events[0]["cleared_ts"] is None
    store.clear_event(eid)
    assert store.recent_events("AA")[0]["cleared_ts"] is not None


def test_export_csv(store, tmp_path):
    base = time.time()
    for i in range(3):
        store.insert_sample(make_sample(ts=base + i))
    out = tmp_path / "export.csv"
    n = store.export_csv(out)
    assert n == 3
    text = out.read_text()
    assert "voltage" in text.splitlines()[0]
    assert len(text.splitlines()) == 4  # header + 3 rows


def test_history_time_window(store):
    now = time.time()
    store.insert_sample(make_sample(ts=now - 10000))
    store.insert_sample(make_sample(ts=now))
    recent = store.history("AA", since=now - 100)
    assert len(recent) == 1


def test_counters_persist_and_reset(store):
    assert store.get_counters("AA") is None
    store.save_counters("AA", 120.0, 30.0, 9.0, 2.0, 1000.0)
    c = store.get_counters("AA")
    assert c["wh_charged"] == pytest.approx(120.0)
    assert c["ah_discharged"] == pytest.approx(2.0)
    assert c["since_ts"] == pytest.approx(1000.0)
    # upsert overwrites
    store.save_counters("AA", 200.0, 30.0, 15.0, 2.0, 1000.0)
    assert store.get_counters("AA")["wh_charged"] == pytest.approx(200.0)
    # reset zeroes and stamps a new since
    store.reset_counters("AA", 2000.0)
    c = store.get_counters("AA")
    assert c["wh_charged"] == 0 and c["since_ts"] == pytest.approx(2000.0)


def test_thresholds_roundtrip(store):
    assert store.get_thresholds("AA") == {}
    store.set_thresholds("AA", {"soc_low": 25.0, "temp_high": 40.0})
    assert store.get_thresholds("AA") == {"soc_low": 25.0, "temp_high": 40.0}
    store.set_thresholds("BB", {"voltage_low": 11.0})
    allt = store.get_all_thresholds()
    assert allt["AA"]["soc_low"] == 25.0 and allt["BB"]["voltage_low"] == 11.0
    # clearing overrides
    store.set_thresholds("AA", {})
    assert store.get_thresholds("AA") == {}


def test_daily_summary(store):
    base = time.time() - 3600
    for i in range(60):
        s = make_sample(ts=base + i * 60, soc=50 + (i % 10))
        s.voltage = 13.0 + (i % 5) * 0.1
        s.current = 10.0 if i % 2 == 0 else -8.0
        s.temperature = 20.0 + (i % 4)
        store.insert_sample(s)
    days = store.daily_summary("AA", days=7)
    assert days  # at least one day
    d = days[0]
    assert d["n"] == 60
    assert d["min_v"] <= d["max_v"]
    assert d["min_soc"] <= d["max_soc"]
    assert "wh_charged" in d and "wh_discharged" in d
    assert d["wh_charged"] >= 0 and d["wh_discharged"] >= 0


def test_stats(store):
    base = time.time()
    for i, v in enumerate([12.0, 13.0, 14.0]):
        s = make_sample(ts=base + i)
        s.voltage = v
        store.insert_sample(s)
    st = store.stats("AA")
    assert st["min_v"] == pytest.approx(12.0)
    assert st["max_v"] == pytest.approx(14.0)
    assert st["n"] == 3
