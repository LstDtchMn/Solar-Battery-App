"""Tests for the in-app settings editor (live-editable, persisted config)."""

from kilovault.config import Config
from kilovault.manager import Manager


def _mgr(tmp_path):
    cfg = Config()
    cfg.db_path = tmp_path / "h.db"
    cfg.transport.type = "simulator"
    cfg.retention_days = 0
    return Manager(cfg)


def test_app_settings_apply_and_clamp(tmp_path):
    m = _mgr(tmp_path)
    try:
        saved = m.set_app_settings({"soc_low": 22, "alert_on": "any",
                                    "log_interval": 45, "retention_days": 200})
        assert saved["soc_low"] == 22
        assert saved["alert_on"] == "any"
        assert m.cfg.log_interval == 45          # applied live
        assert m.cfg.alarms.soc_low == 22
        # invalid values are clamped/ignored, not crashed on
        s2 = m.set_app_settings({"log_interval": 99999, "alert_on": "bogus"})
        assert s2["log_interval"] == 3600        # clamped to max
        assert s2["alert_on"] == "any"           # bogus ignored -> unchanged
    finally:
        m.storage.close()


def test_app_settings_persist_across_restart(tmp_path):
    m = _mgr(tmp_path)
    try:
        m.set_app_settings({"soc_low": 33, "log_interval": 30})
    finally:
        m.storage.close()
    # a fresh Manager on the same DB should re-apply the saved settings
    m2 = _mgr(tmp_path)
    try:
        assert m2.cfg.alarms.soc_low == 33
        assert m2.cfg.log_interval == 30
    finally:
        m2.storage.close()
