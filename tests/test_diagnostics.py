"""Tests for the diagnostics bundle and the manager's diagnostics/hot-swap."""

import asyncio
import zipfile

import pytest

from kilovault import diagnostics
from kilovault.config import Config, TransportConfig
from kilovault.manager import Manager


@pytest.fixture
def cfg(tmp_path):
    c = Config()
    c.data_dir = tmp_path
    c.db_path = tmp_path / "h.db"
    c.transport = TransportConfig(type="simulator", sim_batteries=1)
    return c


def test_collect_info_has_core_fields(cfg):
    info = diagnostics.collect_info(cfg)
    assert info["transport"] == "simulator"
    assert "platform" in info and "python" in info
    assert "app_version" in info
    # serial ports key always present (may be empty)
    assert isinstance(info["serial_ports"], list)


def test_build_zip_contains_log_and_info(cfg, tmp_path):
    # create a log file so it is bundled
    (tmp_path / "kilovault.log").write_text("hello log\n")
    out = diagnostics.build_zip(cfg, tmp_path / "diag.zip")
    assert out.exists()
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert "system_info.json" in names
        assert "kilovault.log" in names


def test_manager_diagnostics_and_hot_swap(cfg):
    async def run():
        m = Manager(cfg)
        await m.start()
        await asyncio.sleep(2.0)
        diag = m.diagnostics()
        assert diag["transport"] == "simulator"
        assert diag["battery_count"] >= 1
        assert "batteries" in diag and diag["batteries"]
        b0 = diag["batteries"][0]
        assert b0["frames_received"] > 0
        # hot-swap to a 2-battery simulator
        await m.set_transport(TransportConfig(type="simulator", sim_batteries=2))
        await asyncio.sleep(2.0)
        assert m.diagnostics()["battery_count"] >= 2
        await m.stop()

    asyncio.run(run())
