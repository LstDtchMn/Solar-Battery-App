"""Transport abstraction: a source of :class:`BatterySample` objects.

A transport discovers batteries and streams decoded samples. The three concrete
transports — BLE (the PC's own Bluetooth), an ESP32 serial bridge, and a
hardware-free simulator — all present the same interface so the rest of the app
is oblivious to where the data comes from.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional

from ..protocol import BatterySample


@dataclass
class DiscoveredBattery:
    address: str
    name: str
    rssi: Optional[int] = None


@dataclass
class ConnectionState:
    address: str
    name: str = ""
    connected: bool = False
    rssi: Optional[int] = None
    last_seen: Optional[float] = None
    model: str = ""
    serial: str = ""
    firmware: str = ""
    error: str = ""


# A coroutine the transport calls for each decoded sample.
SampleCallback = Callable[[BatterySample], Awaitable[None]]
# Called when a battery's connection state changes.
StateCallback = Callable[[ConnectionState], Awaitable[None]]


class Transport:
    """Base class. Subclasses implement :meth:`discover` and :meth:`run`."""

    def __init__(
        self,
        on_sample: SampleCallback,
        on_state: Optional[StateCallback] = None,
    ):
        self._on_sample = on_sample
        self._on_state = on_state
        self._running = False

    async def discover(self, timeout: float = 8.0) -> List[DiscoveredBattery]:
        raise NotImplementedError

    async def run(self) -> None:
        """Stream samples until :meth:`stop` is called. Long-running."""
        raise NotImplementedError

    async def stop(self) -> None:
        self._running = False

    # -- helpers for subclasses ------------------------------------------
    async def _emit(self, sample: BatterySample) -> None:
        if sample.timestamp is None:
            sample.timestamp = time.time()
        await self._on_sample(sample)

    async def _emit_state(self, state: ConnectionState) -> None:
        if self._on_state is not None:
            await self._on_state(state)
