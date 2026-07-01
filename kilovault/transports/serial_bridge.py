"""ESP32 serial/TCP bridge transport.

For PCs without Bluetooth, or to place the radio next to the battery bank and run
a USB cable (or WiFi) to the PC, an ESP32 connects to the battery over BLE and
forwards frames to this program. The bridge speaks a tiny line protocol so the
*same* Python decoder stays authoritative — the ESP32 never interprets the data:

    F <address> <242-hex>\\n   a complete raw 121-byte status frame (hex-encoded)
    S <address> <name...>\\n   battery connected / name announcement
    D <address>\\n             battery disconnected
    # <text>\\n                log line (ignored)

See ``firmware/esp32_bridge`` for the matching firmware. The same protocol works
over a USB serial port or a raw TCP socket (``host:port``).
"""

from __future__ import annotations

import asyncio
import binascii
import threading
import time
from typing import List, Optional

from ..protocol import FrameAssembler, FRAME_LENGTH, decode_frame, ProtocolError
from .base import ConnectionState, DiscoveredBattery, Transport


class SerialBridgeTransport(Transport):
    def __init__(
        self,
        on_sample,
        on_state=None,
        port: str = "",
        baud: int = 115200,
        tcp: Optional[str] = None,
    ):
        super().__init__(on_sample, on_state)
        self.port = port
        self.baud = baud
        self.tcp = tcp  # "host:port" alternative to a serial port
        self._states = {}
        self._queue: "asyncio.Queue[bytes]" = asyncio.Queue()
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()

    async def discover(self, timeout: float = 8.0) -> List[DiscoveredBattery]:
        # The bridge announces batteries via "S" lines as they connect; we
        # surface whatever we've seen so far.
        return [
            DiscoveredBattery(address=s.address, name=s.name)
            for s in self._states.values()
        ]

    async def run(self) -> None:
        self._running = True
        loop = asyncio.get_event_loop()
        if self.tcp:
            await self._run_tcp(loop)
        else:
            self._start_serial_thread(loop)
            await self._consume()

    # -- TCP ------------------------------------------------------------
    async def _run_tcp(self, loop) -> None:
        host, _, port = self.tcp.partition(":")
        reader, writer = await asyncio.open_connection(host, int(port or 3333))
        try:
            while self._running:
                line = await reader.readline()
                if not line:
                    break
                await self._handle_line(line.decode("ascii", "replace").strip())
        finally:
            writer.close()

    # -- Serial ---------------------------------------------------------
    def _start_serial_thread(self, loop) -> None:
        import serial  # pyserial; optional dependency

        def reader():
            try:
                ser = serial.Serial(self.port, self.baud, timeout=1)
            except Exception as exc:  # surface the error to the consumer
                loop.call_soon_threadsafe(
                    self._queue.put_nowait, f"# ERROR {exc}".encode()
                )
                return
            with ser:
                while not self._stop_flag.is_set():
                    try:
                        line = ser.readline()
                    except Exception:
                        break
                    if line:
                        loop.call_soon_threadsafe(self._queue.put_nowait, line)

        self._reader_thread = threading.Thread(target=reader, daemon=True)
        self._reader_thread.start()

    async def _consume(self) -> None:
        while self._running:
            try:
                line = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            await self._handle_line(line.decode("ascii", "replace").strip())

    # -- Line handling --------------------------------------------------
    async def _handle_line(self, line: str) -> None:
        if not line or line.startswith("#"):
            return
        tag, _, rest = line.partition(" ")
        if tag == "F":
            addr, _, hexframe = rest.partition(" ")
            await self._handle_frame(addr, hexframe)
        elif tag == "S":
            from .ble import _clean_name  # pure helper; does not import bleak
            addr, _, name = rest.partition(" ")
            st = self._states.setdefault(addr, ConnectionState(address=addr))
            st.name = _clean_name(name) or st.name
            st.connected = True
            st.last_seen = time.time()
            await self._emit_state(st)
        elif tag == "D":
            st = self._states.get(rest)
            if st:
                st.connected = False
                await self._emit_state(st)

    async def _handle_frame(self, address: str, hexframe: str) -> None:
        try:
            raw = binascii.unhexlify(hexframe)
        except (binascii.Error, ValueError):
            return
        if len(raw) < FRAME_LENGTH:
            return
        try:
            sample = decode_frame(raw)
        except ProtocolError:
            return
        sample.address = address
        st = self._states.setdefault(address, ConnectionState(address=address))
        sample.name = st.name or address
        sample.timestamp = time.time()
        st.connected = True
        st.last_seen = sample.timestamp
        await self._emit(sample)

    async def stop(self) -> None:
        self._running = False
        self._stop_flag.set()
        # Join the reader thread so the serial port + thread are fully released
        # before returning (a hot-swap onto the same port could otherwise collide).
        t = self._reader_thread
        if t is not None and t.is_alive():
            try:
                await asyncio.get_running_loop().run_in_executor(None, t.join, 2.0)
            except Exception:
                pass
        self._reader_thread = None
