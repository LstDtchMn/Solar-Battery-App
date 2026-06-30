"""The collector/orchestrator.

Owns the transport, the per-battery state, storage, and the alarm engine, and
fans live updates out to subscribers (the web dashboard's SSE stream). Everything
runs in a single asyncio loop; the web server reads immutable snapshots.
"""

from __future__ import annotations

import asyncio
import time
from typing import Dict, List, Optional, Set

from .alarms import AlarmEngine, Notifier
from .config import Config
from .estimator import BatteryState, bank_summary
from .protocol import BatterySample
from .storage import Storage
from .transports import build_transport
from .transports.base import ConnectionState


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

        # Alarm evaluation touches storage (event log); never let a transient
        # error there escape and kill the collector.
        try:
            active = self.alarms.evaluate(st)
        except Exception:
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
        if not (st.name and st.name != conn.address):
            st.name = conn.name or conn.address
        if not conn.connected:
            st.mark_disconnected()
        st.connection = conn
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

    # -- control --------------------------------------------------------
    def rename(self, address: str, name: str) -> None:
        self.storage.set_device_name(address, name)
        st = self._state_for(address)
        st.name = name
        if st.sample:
            st.sample.name = name

    def set_capacity(self, address: str, capacity_ah: float) -> None:
        self.storage.set_device_capacity(address, capacity_ah)
        st = self._state_for(address)
        st.capacity_override = capacity_ah
        st.capacity_ah = capacity_ah

    # ------------------------------------------------------------------
    async def run(self) -> None:
        self._loop = asyncio.get_event_loop()
        await self.transport.run()

    async def stop(self) -> None:
        await self.transport.stop()
        self.storage.close()
