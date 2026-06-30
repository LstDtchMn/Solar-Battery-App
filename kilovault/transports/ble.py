"""BLE transport using ``bleak`` (cross-platform: Windows, Linux, macOS).

Discovers HLX+ batteries, connects to each (concurrently), subscribes to the
``0xFFE4`` status notifications and streams decoded samples. Each battery has an
independent supervisor that reconnects automatically — important for an
unattended off-grid install.

``bleak`` is imported lazily so the rest of the program (simulator, serial
bridge, dashboard) works even where bleak isn't installed.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import time
from typing import Dict, List, Optional, Set

from ..protocol import (
    FrameAssembler,
    NOTIFY_UUID,
    SERVICE_UUID,
    SERVICE_UUID_SHORT,
    MODEL_UUID,
    SERIAL_UUID,
    FIRMWARE_UUID,
)
from .base import ConnectionState, DiscoveredBattery, Transport

# Device names look like "12V150Ah-102", "9-12V150Ah-105", "5-12V300Ah-019".
_NAME_RE = re.compile(r"\d+\s*V\s*\d+\s*Ah", re.IGNORECASE)
# Strip control characters and HTML-significant characters from advertised
# names (defense-in-depth: a name is attacker-controlled radio data).
_NAME_CLEAN_RE = re.compile(r"[\x00-\x1f<>&\"'\\]")


def _clean_name(name: str) -> str:
    return _NAME_CLEAN_RE.sub("", (name or ""))[:48].strip()


log = logging.getLogger(__name__)


async def quick_scan(timeout: float = 5.0) -> dict:
    """A standalone scan for the 'Run Bluetooth test' button.

    Returns ``{ok, count, devices, error}``. Never raises.
    """
    try:
        from bleak import BleakScanner
    except Exception as exc:  # bleak not installed
        return {"ok": False, "count": 0, "devices": [],
                "error": f"Bluetooth support not installed ({exc}). "
                         f"Run: pip install bleak"}

    found: Dict[str, dict] = {}

    def cb(device, adv):
        name = adv.local_name or device.name or ""
        is_hlx = _looks_like_hlx(name, getattr(adv, "service_uuids", None))
        found[device.address] = {
            "address": device.address,
            "name": _clean_name(name) or device.address,
            "rssi": getattr(adv, "rssi", None),
            "is_hlx": is_hlx,
        }

    try:
        scanner = BleakScanner(detection_callback=cb)
        await scanner.start()
        await asyncio.sleep(timeout)
        await scanner.stop()
    except Exception as exc:
        log.warning("Bluetooth test scan failed: %s", exc)
        return {"ok": False, "count": 0, "devices": [],
                "error": f"{type(exc).__name__}: {exc}"}

    devices = sorted(found.values(), key=lambda d: (not d["is_hlx"], d["name"]))
    hlx = [d for d in devices if d["is_hlx"]]
    return {"ok": True, "count": len(hlx), "devices": devices,
            "total_seen": len(devices), "error": ""}


def _looks_like_hlx(name: Optional[str], service_uuids) -> bool:
    uuids = {str(u).lower() for u in (service_uuids or [])}
    if SERVICE_UUID.lower() in uuids or SERVICE_UUID_SHORT.lower() in uuids:
        return True
    if name and (_NAME_RE.search(name) or "kilovault" in name.lower()):
        return True
    return False


class BleTransport(Transport):
    def __init__(
        self,
        on_sample,
        on_state=None,
        addresses: Optional[List[str]] = None,
        scan_timeout: float = 8.0,
        reconnect_seconds: float = 5.0,
    ):
        super().__init__(on_sample, on_state)
        self._addresses = [a.upper() for a in (addresses or [])]
        self.scan_timeout = scan_timeout
        self.reconnect_seconds = reconnect_seconds
        self._tasks: Dict[str, asyncio.Task] = {}
        self._states: Dict[str, ConnectionState] = {}
        # Strong references to in-flight emit tasks so they are never GC'd
        # mid-flight (an asyncio gotcha with create_task/ensure_future).
        self._pending: Set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    async def discover(self, timeout: Optional[float] = None) -> List[DiscoveredBattery]:
        from bleak import BleakScanner

        timeout = timeout or self.scan_timeout
        found: Dict[str, DiscoveredBattery] = {}

        def cb(device, adv):
            name = adv.local_name or device.name or ""
            if _looks_like_hlx(name, getattr(adv, "service_uuids", None)):
                found[device.address] = DiscoveredBattery(
                    address=device.address,
                    name=_clean_name(name) or device.address,
                    rssi=getattr(adv, "rssi", None),
                )

        scanner = BleakScanner(detection_callback=cb)
        await scanner.start()
        await asyncio.sleep(timeout)
        await scanner.stop()
        return list(found.values())

    # ------------------------------------------------------------------
    async def run(self) -> None:
        self._running = True
        targets = self._addresses
        if not targets:
            discovered = await self.discover()
            targets = [d.address for d in discovered]
            for d in discovered:
                self._states[d.address] = ConnectionState(
                    address=d.address, name=d.name, rssi=d.rssi
                )
        if not targets:
            # Nothing found; keep scanning periodically so a battery that powers
            # on later still gets picked up.
            while self._running:
                await asyncio.sleep(self.reconnect_seconds)
                discovered = await self.discover()
                for d in discovered:
                    if d.address not in self._tasks:
                        self._start_supervisor(d.address, d.name)
            return

        for addr in targets:
            name = self._states.get(addr, ConnectionState(address=addr)).name
            self._start_supervisor(addr, name)

        # Keep running until stopped.
        while self._running:
            await asyncio.sleep(1.0)

    def _start_supervisor(self, address: str, name: str = "") -> None:
        if address in self._tasks and not self._tasks[address].done():
            return
        self._states.setdefault(address, ConnectionState(address=address, name=name))
        self._tasks[address] = asyncio.ensure_future(self._supervise(address))

    async def stop(self) -> None:
        self._running = False
        for task in list(self._tasks.values()):
            task.cancel()
        for task in list(self._tasks.values()):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()

    # ------------------------------------------------------------------
    async def _supervise(self, address: str) -> None:
        """Connect/maintain one battery, reconnecting forever until stopped."""
        from bleak import BleakClient
        from bleak.exc import BleakError

        state = self._states[address]
        while self._running:
            try:
                log.debug("Connecting to %s", address)
                await self._connect_once(address, state)
            except asyncio.CancelledError:
                raise
            except BleakError as exc:
                state.connected = False
                state.error = str(exc)
                log.warning("BLE error for %s: %s", address, exc)
                await self._emit_state(state)
            except Exception as exc:  # noqa: BLE001 - keep the supervisor alive
                state.connected = False
                state.error = f"{type(exc).__name__}: {exc}"
                log.warning("Connection error for %s: %s", address, state.error)
                await self._emit_state(state)
            if not self._running:
                break
            log.debug("Reconnecting to %s in %.1fs", address, self.reconnect_seconds)
            await asyncio.sleep(self.reconnect_seconds)

    async def _connect_once(self, address: str, state: ConnectionState) -> None:
        from bleak import BleakClient

        assembler = FrameAssembler()
        loop = asyncio.get_running_loop()

        def notify_cb(_char, data: bytearray):
            for sample in assembler.feed(bytes(data)):
                sample.address = address
                sample.name = state.name or address
                sample.timestamp = time.time()
                state.last_seen = sample.timestamp
                # bleak may invoke this off the loop thread on some backends;
                # schedule the async emit thread-safely and keep a strong ref.
                self._schedule_emit(sample, loop)

        async with BleakClient(address) as client:
            state.connected = True
            state.error = ""
            await self._read_device_info(client, state)
            await self._emit_state(state)

            await client.start_notify(NOTIFY_UUID, notify_cb)
            try:
                while self._running and client.is_connected:
                    await asyncio.sleep(1.0)
            finally:
                try:
                    await client.stop_notify(NOTIFY_UUID)
                except Exception:
                    pass
        state.connected = False
        await self._emit_state(state)

    def _schedule_emit(self, sample, loop: asyncio.AbstractEventLoop) -> None:
        """Schedule ``self._emit(sample)`` on ``loop``, thread-safe and tracked."""

        def _spawn():
            task = loop.create_task(self._emit_safe(sample))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            _spawn()
        else:
            loop.call_soon_threadsafe(_spawn)

    async def _emit_safe(self, sample) -> None:
        # A transient storage/alarm/publish error must never silently vanish
        # into an orphaned task; log it and keep the collector alive.
        try:
            await self._emit(sample)
        except Exception as exc:  # noqa: BLE001
            print(f"[kilovault] error handling sample from {sample.address}: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)

    async def _read_device_info(self, client, state: ConnectionState) -> None:
        async def read(uuid):
            try:
                raw = await client.read_gatt_char(uuid)
                return raw.decode("utf-8", "replace").strip("\x00 ").strip()
            except Exception:
                return ""

        state.model = await read(MODEL_UUID) or state.model
        state.serial = await read(SERIAL_UUID) or state.serial
        state.firmware = await read(FIRMWARE_UUID) or state.firmware
