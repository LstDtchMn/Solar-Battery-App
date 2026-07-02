"""Tests for display/kiosk settings and the advertised phone host."""

from kilovault.config import Config
from kilovault.manager import Manager
from kilovault.server.app import DashboardServer


def _server(tmp_path, **web):
    cfg = Config()
    cfg.db_path = tmp_path / "h.db"
    cfg.transport.type = "simulator"
    cfg.retention_days = 0
    for k, v in web.items():
        setattr(cfg.web, k, v)
    return DashboardServer(Manager(cfg), host=cfg.web.host)


def test_display_defaults_and_persist(tmp_path):
    s = _server(tmp_path, host="0.0.0.0")
    try:
        d = s._display_settings()
        assert d == {"preset": "bank", "focus_address": "",
                     "font_scale": 1.0, "theme": "dark"}
        saved = s._save_display({"preset": "soc", "font_scale": 1.6,
                                 "theme": "light", "focus_address": "AA"})
        assert saved["preset"] == "soc" and saved["theme"] == "light"
        # persisted across a fresh read
        assert s._display_settings()["preset"] == "soc"
        assert s._display_settings()["focus_address"] == "AA"
    finally:
        s.manager.storage.close()


def test_display_validation_clamps(tmp_path):
    s = _server(tmp_path, host="0.0.0.0")
    try:
        saved = s._save_display({"preset": "bogus", "font_scale": 99,
                                 "theme": "neon"})
        assert saved["preset"] == "bank"       # invalid preset -> previous/default
        assert saved["theme"] == "dark"         # invalid theme -> previous/default
        assert saved["font_scale"] == 2.5       # clamped to max
        saved2 = s._save_display({"font_scale": 0.1})
        assert saved2["font_scale"] == 0.8      # clamped to min
    finally:
        s.manager.storage.close()


def test_phone_url_uses_advertised_host(tmp_path):
    s = _server(tmp_path, host="0.0.0.0", advertised_host="10.42.0.1")
    try:
        info = s._connect_info()
        assert info["url"].startswith("http://10.42.0.1:")
        assert "token=" in info["url"]
    finally:
        s.manager.storage.close()
