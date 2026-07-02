"""The collector/orchestrator.

Owns the transport, the per-battery state, storage, and the alarm engine, and
fans live updates out to subscribers (the web dashboard's SSE stream). Everything
runs in a single asyncio loop; the web server reads immutable snapshots.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import dataclasses
import logging
import platform
import time
from typing import Dict, List, Optional, Set

#: Alarm thresholds a user may override per battery.
THRESHOLD_FIELDS = (
    "cell_delta_warn", "cell_delta_critical", "temp_high", "temp_low",
    "soc_low", "soc_critical", "voltage_high", "voltage_low",
)

from .alarms import Alarm, AlarmEngine, Notifier
from .config import Config, TransportConfig
from .estimator import BatteryState, bank_summary
from .hardware import HardwareAlerter
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
        self.hardware = HardwareAlerter(config.hardware)
        self.states: Dict[str, BatteryState] = {}
        # Latest active alarms per battery, for bank-wide hardware alerting.
        self._active_alarms: Dict[str, List[Alarm]] = {}
        self._hw_last: Optional[bool] = None
        # Drive the (possibly blocking) relay/GPIO/command off the event loop.
        self._hw_executor = (
            concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="kv-hw")
            if self.hardware.enabled else None
        )
        self._subscribers: Set[asyncio.Queue] = set()
        self._last_log: Dict[str, float] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._collector_task: Optional[asyncio.Task] = None
        self._housekeeping_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        # Serialise transport hot-swaps so two concurrent wizard submits can't
        # leave an orphaned transport/collector running.
        self._transport_lock = asyncio.Lock()
        # Per-battery alarm-threshold overrides {address: {field: value}}.
        self._thresholds: Dict[str, dict] = self.storage.get_all_thresholds()
        self.transport = build_transport(
            config.transport, self._on_sample, self._on_state
        )
        # Seed states from the device registry so names, capacities and energy
        # counters persist across restarts.
        for addr, dev in self.storage.get_devices().items():
            self.states[addr] = self._new_state(addr, dev)

    # ------------------------------------------------------------------
    def _new_state(self, addr: str, dev: dict) -> BatteryState:
        st = BatteryState(address=addr, name=(dev.get("name") or addr))
        if dev.get("capacity_ah"):
            st.capacity_override = dev["capacity_ah"]
            st.capacity_ah = dev["capacity_ah"]
        cnt = self.storage.get_counters(addr)
        if cnt:
            st.wh_charged = cnt.get("wh_charged") or 0.0
            st.wh_discharged = cnt.get("wh_discharged") or 0.0
            st.ah_charged = cnt.get("ah_charged") or 0.0
            st.ah_discharged = cnt.get("ah_discharged") or 0.0
            if cnt.get("since_ts"):
                st.session_start = cnt["since_ts"]
        return st

    def _state_for(self, address: str) -> BatteryState:
        st = self.states.get(address)
        if st is None:
            st = self._new_state(address, self.storage.get_device(address) or {})
            self.states[address] = st
        return st

    async def _on_sample(self, sample: BatterySample) -> None:
        # This runs for every incoming frame on the collector task. Any escaping
        # exception would kill that task and silently take the whole monitor
        # offline (the BLE transport guards its callback, but the simulator and
        # serial bridge await this directly), so the body is fully guarded.
        try:
            st = self._state_for(sample.address)
            # A user-set friendly name always wins over the advertised name.
            if st.name and st.name != sample.address:
                sample.name = st.name
            st.update(sample)
            # Reconcile the sample to the user's capacity override so remaining-Ah,
            # the bank totals, the UI and the stored/exported rows all agree.
            if st.capacity_override is not None:
                sample.total_capacity = st.capacity_override

            # A sample proves the battery is alive: refresh connection liveness
            # here (centrally) so the stale watchdog works for every transport,
            # and clears a prior "stale" mark once data resumes.
            if st.connection is not None:
                st.connection.connected = True
                st.connection.last_seen = sample.timestamp or time.time()
                if st.connection.error and st.connection.error.startswith("no data"):
                    st.connection.error = ""

            # Alarm evaluation touches storage (event log); never let a transient
            # error there escape and kill the collector.
            try:
                active = self.alarms.evaluate(st, self._effective_alarm_cfg(sample.address))
            except Exception:
                log.exception("alarm evaluation failed for %s", sample.address)
                active = []
            self._active_alarms[sample.address] = active
            self._update_hardware()

            now = sample.timestamp or time.time()
            if now - self._last_log.get(sample.address, 0) >= self.cfg.log_interval:
                try:
                    self.storage.insert_sample(sample)
                    # Persist energy counters so they survive a restart.
                    self.storage.save_counters(
                        sample.address, st.wh_charged, st.wh_discharged,
                        st.ah_charged, st.ah_discharged, st.session_start)
                except Exception:
                    pass
                self._last_log[sample.address] = now

            await self._publish({
                "type": "sample",
                "address": sample.address,
                "battery": st.to_dict(),
                "alarms": [a.__dict__ for a in active],
            })
        except Exception:
            log.exception("error handling sample from %s",
                          getattr(sample, "address", "?"))

    async def _on_state(self, conn: ConnectionState) -> None:
        st = self._state_for(conn.address)
        # Preserve a user-set friendly name.
        was_connected = bool(st.connection and st.connection.connected)
        if not (st.name and st.name != conn.address):
            st.name = conn.name or conn.address
        if not conn.connected:
            st.mark_disconnected()
        st.connection = conn
        # Seed liveness at connect so the watchdog can detect a battery that
        # connects but never delivers a notification (last_seen would stay None
        # and the stale check would never fire).
        if conn.connected and conn.last_seen is None:
            conn.last_seen = time.time()
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
            "hardware_alerting": (self.cfg.hardware.alert_on
                                  if self.hardware.enabled else "off"),
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

    def _update_hardware(self) -> None:
        """Drive the physical alerter from the bank-wide alarm state.

        Runs only on a state transition, and off the event loop, so a slow/hung
        USB relay or command can never stall sampling or the SSE dashboard.
        """
        if not self.hardware.enabled:
            return
        all_active = [a for lst in self._active_alarms.values() for a in lst]
        if self.cfg.hardware.alert_on == "any":
            want = bool(all_active)
        else:  # "critical"
            want = any(a.severity == "critical" for a in all_active)
        if want == self._hw_last:
            return
        self._hw_last = want
        alarms = [a for a in all_active if a.severity == "critical"] or all_active
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and self._hw_executor is not None:
            loop.run_in_executor(self._hw_executor, self.hardware.set_active, want, list(alarms))
        else:  # no event loop (tests / direct call): run inline
            self.hardware.set_active(want, list(alarms))

    # -- per-battery alarm thresholds -----------------------------------
    def global_thresholds(self) -> dict:
        a = self.cfg.alarms
        return {f: getattr(a, f) for f in THRESHOLD_FIELDS}

    def get_thresholds(self, address: str) -> dict:
        return dict(self._thresholds.get(address, {}))

    def set_thresholds(self, address: str, overrides: dict) -> None:
        clean = {}
        for f in THRESHOLD_FIELDS:
            v = overrides.get(f)
            if v is None or v == "":
                continue
            try:
                clean[f] = float(v)
            except (TypeError, ValueError):
                continue
        self._thresholds[address] = clean
        self.storage.set_thresholds(address, clean)

    def _effective_alarm_cfg(self, address: str):
        ov = self._thresholds.get(address)
        if not ov:
            return self.cfg.alarms
        fields = {k: v for k, v in ov.items() if k in THRESHOLD_FIELDS}
        return dataclasses.replace(self.cfg.alarms, **fields)

    def reset_counters(self, address: Optional[str] = None) -> None:
        """Zero the energy counters for one battery (or all) and restart the
        'since' clock."""
        now = time.time()
        targets = [address] if address else list(self.states.keys())
        for addr in targets:
            st = self._state_for(addr)
            st.wh_charged = st.wh_discharged = 0.0
            st.ah_charged = st.ah_discharged = 0.0
            st.session_start = now
            try:
                self.storage.reset_counters(addr, now)
            except Exception:
                log.debug("reset_counters failed for %s", addr, exc_info=True)

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
        if self.cfg.transport.stale_after_seconds and self.cfg.transport.stale_after_seconds > 0:
            self._watchdog_task = asyncio.ensure_future(self._watchdog())

    async def _watchdog(self) -> None:
        """Mark a battery offline if it stops delivering data without a clean
        disconnect (e.g. BLE notifications silently stop)."""
        stale = self.cfg.transport.stale_after_seconds
        while True:
            try:
                now = time.time()
                for st in list(self.states.values()):
                    conn = st.connection
                    if (conn and conn.connected and conn.last_seen
                            and now - conn.last_seen > stale):
                        age = now - conn.last_seen
                        conn.connected = False
                        conn.error = f"no data for {int(age)}s"
                        st.mark_disconnected()
                        log.warning("Battery %s went stale (no data for %.0fs)",
                                    st.address, age)
                        await self._publish({"type": "state", "address": st.address,
                                             "battery": st.to_dict()})
            except asyncio.CancelledError:
                raise
            except Exception:
                log.debug("watchdog error", exc_info=True)
            await asyncio.sleep(min(5.0, max(1.0, stale / 3.0)))

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
        # Serialise so two near-simultaneous switches can't each build a new
        # transport and leave the other's transport/collector orphaned.
        async with self._transport_lock:
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
        for task in (self._collector_task, self._housekeeping_task, self._watchdog_task):
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
        try:
            self.hardware.close()
        except Exception:
            pass
        if self._hw_executor is not None:
            self._hw_executor.shutdown(wait=False)
        self.storage.close()
