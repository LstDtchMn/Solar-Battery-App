"""Tests for config validation/clamping."""

from kilovault.config import AlarmConfig, Config, WebConfig


def test_validate_clamps_and_warns():
    c = Config()
    c.log_interval = -5
    c.retention_days = 100000
    c.web = WebConfig(port=99999)
    w = c.validate()
    assert w  # warnings produced
    assert c.log_interval == 0.1          # clamped, kept as float (not truncated to 0)
    assert c.web.port == 65535 and isinstance(c.web.port, int)
    assert c.retention_days == 36500


def test_validate_fixes_inverted_thresholds():
    c = Config()
    c.alarms = AlarmConfig(voltage_low=15, voltage_high=12,
                           soc_low=10, soc_critical=20,
                           cell_delta_warn=0.5, cell_delta_critical=0.3)
    c.validate()
    assert c.alarms.voltage_low < c.alarms.voltage_high
    assert c.alarms.soc_critical <= c.alarms.soc_low
    assert c.alarms.cell_delta_warn <= c.alarms.cell_delta_critical


def test_validate_rejects_bad_transport():
    c = Config()
    c.transport.type = "bogus"
    c.validate()
    assert c.transport.type == "ble"


def test_validate_ok_config_no_warnings():
    assert Config().validate() == []


def test_validate_non_numeric_does_not_crash():
    c = Config()
    c.web.port = "notaport"     # bad string must not crash validate()
    c.log_interval = "5"        # in-range string should normalize to float
    w = c.validate()
    assert w  # warned
    assert isinstance(c.web.port, int) and c.web.port == 8765
    assert c.log_interval == 5.0 and isinstance(c.log_interval, float)
