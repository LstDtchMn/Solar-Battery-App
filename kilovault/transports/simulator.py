"""Hardware-free simulator transport.

Generates realistic HLX+ telemetry so the whole application — dashboard,
logging, alarms, exports — can be exercised with no battery and no Bluetooth.
It models a daily solar charge/discharge cycle, cell imbalance, temperature
drift and the occasional alarm.

It reuses :func:`kilovault.protocol.encode_frame` and the real
:class:`FrameAssembler`, so the bytes it produces are decoded by exactly the
same path a real battery uses — the simulator is a faithful stand-in, not a
shortcut.
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import List

from ..protocol import BatterySample, FrameAssembler, encode_frame, ALARM_BITS
from .base import ConnectionState, DiscoveredBattery, Transport


class _SimBattery:
    def __init__(self, index: int, capacity_ah: float = 100.0):
        self.index = index
        self.address = f"SIM:00:00:00:00:{index:02X}"
        self.name = f"SIM-12V{int(capacity_ah)}Ah-{index:03d}"
        self.capacity_ah = capacity_ah
        self.soc = 60.0 + 10.0 * index
        self.cycles = 12 + index
        self.temperature = 21.0 + index
        # Slight per-cell offsets so balance analytics have something to show.
        self._cell_bias = [0.000, 0.004, -0.003, 0.002]
        self._t0 = time.time()

    def sample(self) -> BatterySample:
        t = time.time() - self._t0
        # A ~120 s "day": charge for the first half, discharge the second half.
        day = 120.0
        phase = (t % day) / day
        if phase < 0.5:
            current = 18.0 * math.sin(phase * 2 * math.pi)  # charging (+)
        else:
            current = -14.0 * math.sin((phase - 0.5) * 2 * math.pi)  # load (-)

        # Integrate current into SoC (Ah counting against capacity).
        dt = 1.0
        self.soc += (current * dt / 3600.0) / self.capacity_ah * 100.0
        self.soc = max(5.0, min(100.0, self.soc))

        # Resting cell voltage from a simple SoC->V curve (LiFePO4 is flat).
        base = 3.20 + 0.0035 * (self.soc - 50.0) / 50.0 * 20.0
        base = max(3.05, min(3.45, base))
        # IR drop / rise under load.
        base += current * 0.0008
        cells = [round(base + b, 3) for b in self._cell_bias]
        voltage = sum(cells)

        self.temperature += 0.05 * math.sin(t / 30.0)

        status = 0x0100  # normal-operation bit
        # Fire a low-temp alarm occasionally on the last simulated battery.
        if self.index == 1 and int(t) % 90 in (80, 81, 82):
            status |= ALARM_BITS["LTC"][0]

        s = BatterySample(
            voltage=round(voltage, 3),
            current=round(current, 3),
            total_capacity=self.capacity_ah,
            cycles=self.cycles,
            soc=round(self.soc, 1),
            temperature=round(self.temperature, 1),
            status=status,
            cell_voltages=cells + [0.0] * 12,
        )
        s.address = self.address
        s.name = self.name
        return s


class SimulatorTransport(Transport):
    def __init__(self, on_sample, on_state=None, count: int = 2, interval: float = 1.0):
        super().__init__(on_sample, on_state)
        self.interval = interval
        caps = [100.0, 150.0, 300.0]
        self._batteries: List[_SimBattery] = [
            _SimBattery(i, caps[i % len(caps)]) for i in range(count)
        ]

    async def discover(self, timeout: float = 8.0) -> List[DiscoveredBattery]:
        return [
            DiscoveredBattery(address=b.address, name=b.name, rssi=-50 - b.index)
            for b in self._batteries
        ]

    async def run(self) -> None:
        self._running = True
        for b in self._batteries:
            await self._emit_state(
                ConnectionState(
                    address=b.address, name=b.name, connected=True,
                    rssi=-50 - b.index, last_seen=time.time(),
                    model="HLX+ (simulated)", serial=f"SIM{b.index:04d}",
                    firmware="sim-1.0",
                )
            )
        # Each simulated battery has its own assembler, exactly like real BLE.
        assemblers = {b.address: FrameAssembler() for b in self._batteries}
        while self._running:
            now = time.time()
            for b in self._batteries:
                raw = b.sample()
                frame = encode_frame(raw)
                for decoded in assemblers[b.address].feed(frame):
                    decoded.address = b.address
                    decoded.name = b.name
                    decoded.timestamp = now
                    await self._emit(decoded)
            await asyncio.sleep(self.interval)
