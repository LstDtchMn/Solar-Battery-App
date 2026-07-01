"""The collector/orchestrator.

Owns the transport, the per-battery state, storage, and the alarm engine, and
fans live updates out to subscribers (the web dashboard's SSE stream). Everything
runs in a single asyncio loop; the web server reads immutable snapshots.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import time
from typing import Dict, List, Optional, Set

from .alarms import AlarmEngine, Notifier
from .config import Config, TransportConfig
from .estimator import BatteryState, bank_summary
from .protocol import BatterySample
from .storage import Storage
from .transports import build_transport
from .transports.base import ConnectionState

log = logging.getLogger(__name__)


class Manager:
    def __init__(self, config: Config):
        self.cfg = config
        self.storage = Storage(config.db_path)
        self.notifier = Notifier(config.alarms.sound, config.alarms.notify_desktop)
        self.alarms = AlarmEngine(config.alarms, self.storage, self.notifier)
        self.states: Dict[str, BatteryState] = {}
        self._subscribers: Set[asyncio.Queue] = set()
        self._last_log: Dict[str, float] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._collector_task: Optional[asyncio.Task] = None
        self._housekeeping_task: Optional[asyncio.Task] = None
        self.transport = build_transport(
            config.transport, self._on_sample, self._on_state
        )
        # Seed states from the device registry so names persist across restarts.
        for addr, dev in self.storage.get_devices().items():
            st = BatteryState(address=addr, name=dev.get("name") or addr)
            if dev.get("capacity_ah"):
                st.capacity_override = dev["capacity_ah"]
                st.capacity_ah = dev["capacity_ah"]
            self.states[addr] = st

    # ------------------------------------------------------------------
    def _state_for(self, address: str) -> BatteryState:
        st = self.states.get(address)
        if st is None:
            dev = self.storage.get_device(address) or {}
            st = BatteryState(address=address, name=dev.get("name") or address)
            if dev.get("capacity_ah"):
                st.capacity_override = dev["capacity_ah"]
                st.capacity_ah = dev["capacity_ah"]
            self.states[address] = st
        return st

    async def _on_sample(self, sample: BatterySample) -> None:
        st = self._state_for(sample.address)
        # A user-set friendly name always wins over the advertised name.
        if st.name and st.name != sample.address:
            sample.name = st.name
        st.update(sample)
        # Reconcile the sample to the user's capacity override so remaining-Ah,
        # the bank totals, the UI and the stored/exported rows all agree.
        if st.capacity_override is not None:
            sample.total_capacity = st.capacity_override

        # Alarm evaluation touches storage (event log); never let a transient
        # error there escape and kill the collector.
        try:
            active = self.alarms.evaluate(st)
        except Exception:
            log.exception("alarm evaluation failed for %s", sample.address)
            active = []

        now = sample.timestamp or time.time()
        if now - self._last_log.get(sample.address, 0) >= self.cfg.log_interval:
            try:
                self.storage.insert_sample(sample)
            except Exception:
                pass
            self._last_log[sample.address] = now

        await self._publish({
            "type": "sample",
            "address": sample.address,
            "battery": st.to_dict(),
            "alarms": [a.__dict__ for a in active],
        })

    async def _on_state(self, conn: ConnectionState) -> None:
        st = self._state_for(conn.address)
        # Preserve a user-set friendly name.
        was_connected = bool(st.connection and st.connection.connected)
        if not (st.name and st.name != conn.address):
            st.name = conn.name or conn.address
        if not conn.connected:
            st.mark_disconnected()
        st.connection = conn
        # Log connection transitions — the most useful thing during support.
        if conn.connected and not was_connected:
            log.info("Connected to %s (%s)  rssi=%s fw=%s",
                     conn.name or conn.address, conn.address, conn.rssi, conn.firmware)
        elif not conn.connected and was_connected:
            log.warning("Disconnected from %s (%s)%s", conn.name or conn.address,
                        conn.address, f": {conn.error}" if conn.error else "")
        try:
            self.storage.upsert_device(
                conn.address, conn.name, conn.model, conn.serial, conn.firmware
            )
        except Exception:
            pass
        await self._publish({
            "type": "state",
            "address": conn.address,
            "battery": st.to_dict(),
        })

    # ------------------------------------------------------------------
    async def _publish(self, message: dict) -> None:
        # A momentarily-slow SSE client must not be permanently disconnected:
        # on a full queue, drop the oldest message and enqueue the newest so
        # live data keeps flowing once the client catches up.
        for q in list(self._subscribers):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(message)
                except asyncio.QueueFull:
                    pass

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        states = list(self.states.values())
        return {
            "timestamp": time.time(),
            "transport": self.cfg.transport.type,
            "bank": bank_summary(states),
            "batteries": [st.to_dict() for st in states],
            "events": self.storage.recent_events(limit=50),
        }

    def diagnostics(self) -> dict:
        """Connection/setup health for the Diagnostics page."""
        try:
            import bleak
            bleak_ver = getattr(bleak, "__version__", "installed")
        except Exception:
            bleak_ver = None
        try:
            import serial
            serial_ver = getattr(serial, "__version__", "installed")
        except Exception:
            serial_ver = None

        import kilovault
        batteries = []
        for st in self.states.values():
            conn = st.connection
            s = st.sample
            batteries.append({
                "address": st.address,
                "name": st.name or st.address,
                "connected": bool(conn and conn.connected),
                "rssi": conn.rssi if conn else None,
                "last_seen": conn.last_seen if conn else None,
                "model": conn.model if conn else "",
                "firmware": conn.firmware if conn else "",
                "serial": conn.serial if conn else "",
                "error": conn.error if conn else "",
                "frames_received": st.frames_received,
                "crc_errors": st.crc_errors,
                "soc": s.soc if s else None,
                "voltage": s.voltage if s else None,
            })
        return {
            "version": kilovault.__version__,
            "platform": platform.platform(),
            "python": platform.python_version(),
            "transport": self.cfg.transport.type,
            "db_path": str(self.cfg.db_path),
            "bleak_version": bleak_ver,
            "pyserial_version": serial_ver,
            "battery_count": len(self.states),
            "online_count": sum(1 for st in self.states.values()
                                if st.connection and st.connection.connected),
            "batteries": batteries,
        }

    # -- control --------------------------------------------------------
    def rename(self, address: str, name: str) -> None:
        self.storage.set_device_name(address, name)
        st = self._state_for(address)
        st.name = name
        if st.sample:
            st.sample.name = name

    def set_capacity(self, address: str, capacity_ah: float) -> None:
        if not capacity_ah or capacity_ah <= 0:
            raise ValueError("capacity must be greater than 0")
        self.storage.set_device_capacity(address, capacity_ah)
        st = self._state_for(address)
        st.capacity_override = capacity_ah
        st.capacity_ah = capacity_ah

    # -- lifecycle ------------------------------------------------------
    async def start(self) -> None:
        """Launch the collector in the background (non-blocking)."""
        self._loop = asyncio.get_running_loop()
        log.info("Starting collector (transport=%s)", self.cfg.transport.type)
        self._collector_task = asyncio.ensure_future(self._run_transport())
        if self.cfg.retention_days and self.cfg.retention_days > 0:
            self._housekeeping_task = asyncio.ensure_future(self._housekeeping())

    async def _housekeeping(self) -> None:
        """Periodically prune old history so the DB doesn't grow forever."""
        while True:
            try:
                deleted = self.storage.prune(self.cfg.retention_days)
                if deleted:
                    log.info("Pruned %d history rows older than %.0f days",
                             deleted, self.cfg.retention_days)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.debug("prune failed", exc_info=True)
            await asyncio.sleep(6 * 3600)

    async def _run_transport(self) -> None:
        try:
            await self.transport.run()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Transport loop crashed (transport=%s)",
                          self.cfg.transport.type)

    async def set_transport(self, tcfg: TransportConfig) -> None:
        """Hot-swap the data source (used by the setup wizard)."""
        log.info("Switching transport %s -> %s", self.cfg.transport.type, tcfg.type)
        try:
            await self.transport.stop()
        except Exception:
            log.debug("error stopping old transport", exc_info=True)
        if self._collector_task:
            self._collector_task.cancel()
            try:
                await self._collector_task
            except (asyncio.CancelledError, Exception):
                pass
        self.cfg.transport = tcfg
        self.transport = build_transport(tcfg, self._on_sample, self._on_state)
        self._collector_task = asyncio.ensure_future(self._run_transport())
        await self._publish({"type": "transport", "transport": tcfg.type})

    async def run(self) -> None:
        """Start the collector and block until it finishes (for `monitor`)."""
        await self.start()
        if self._collector_task:
            await self._collector_task

    async def stop(self) -> None:
        for task in (self._collector_task, self._housekeeping_task):
            if task:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        try:
            await self.transport.stop()
        except Exception:
            pass
        self.storage.close()
