"""KiloVault HLX+ BLE protocol: frame assembly, decoding and encoding.

This module is pure (no I/O, no async) so it can be unit-tested in isolation and
reused by every transport (BLE, ESP32 serial bridge, simulator). See
``docs/PROTOCOL.md`` for the full specification this implements.

The battery streams ~1 Hz status frames over the notify characteristic
``0xFFE4``. Each frame is 121 bytes on the wire::

    byte 0        : 0xB0                       start marker
    byte 1..112   : 112 ASCII-hex characters   -> 56 bytes of payload
    byte 113..120 : "RRRRRRRR"                  end marker

The 56-byte payload is 54 little-endian data bytes followed by a 16-bit checksum.
"""

from __future__ import annotations

import binascii
import struct
from dataclasses import dataclass, field
from typing import Callable, List, Optional

# ---------------------------------------------------------------------------
# GATT / framing constants
# ---------------------------------------------------------------------------

#: 16-bit GATT UUIDs. ``bleak`` matches these against the 128-bit base UUID.
SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ffe4-0000-1000-8000-00805f9b34fb"
CONTROL_UUID = "0000fa02-0000-1000-8000-00805f9b34fb"
MODEL_UUID = "00002a24-0000-1000-8000-00805f9b34fb"
SERIAL_UUID = "00002a25-0000-1000-8000-00805f9b34fb"
FIRMWARE_UUID = "00002a26-0000-1000-8000-00805f9b34fb"

#: Short forms accepted by some stacks / used in logs.
SERVICE_UUID_SHORT = "FFE0"
NOTIFY_UUID_SHORT = "FFE4"

START_BYTE = 0xB0
END_BYTE = 0x52  # 'R'
FRAME_LENGTH = 121  # total bytes of a complete wire frame
HEX_REGION = slice(1, 113)  # 112 ASCII-hex characters
PAYLOAD_BYTES = 56  # after unhexlify
DATA_BYTES = 54  # payload minus the 2 checksum bytes
MAX_CELLS = 16
HLX_CELLS = 4  # an HLX+ is a 4S pack

# Byte offsets inside the 54-byte little-endian body.
_OFF_VOLTAGE = 0
_OFF_UNKNOWN = 2
_OFF_CURRENT = 4
_OFF_CAPACITY = 8
_OFF_CYCLES = 12
_OFF_SOC = 14
_OFF_TEMP = 16
_OFF_STATUS = 18
_OFF_CELLS = 22
_OFF_CHECKSUM = 54

# Current dead-band (A) below which the pack is considered idle/standby.
STANDBY_CURRENT_A = 0.1

# Kelvin offset used by the pack's temperature encoding.
_KELVIN = 273.15


# ---------------------------------------------------------------------------
# Alarm bits (status word, offset 18). See docs/PROTOCOL.md §4.
# ---------------------------------------------------------------------------

#: ``code -> (mask, human description)``. Order matches the manual's screen.
ALARM_BITS = {
    "HTC": (0x00000001, "High-temperature charging"),
    "HTD": (0x00000002, "High-temperature discharging"),
    "LTC": (0x00000004, "Low-temperature charging"),
    "LTD": (0x00000008, "Low-temperature discharging"),
    "OCD": (0x00000010, "Over-current discharging"),
    "OCC": (0x00000020, "Over-current charging"),
    "LV": (0x00000040, "Low voltage"),
    "HV": (0x00000080, "High voltage"),
    "SCD": (0x00200000, "Short-circuit discharge"),
}


def decode_alarms(status: int) -> List[str]:
    """Return the list of active alarm codes encoded in ``status``."""
    return [code for code, (mask, _desc) in ALARM_BITS.items() if status & mask]


# ---------------------------------------------------------------------------
# Decoded sample
# ---------------------------------------------------------------------------


@dataclass
class BatterySample:
    """A single decoded status frame from one battery.

    Voltages are in volts, current in amps (+charge/-discharge), capacity in
    amp-hours, temperature in °C, state-of-charge in percent.
    """

    voltage: float
    current: float
    total_capacity: float
    cycles: int
    soc: float
    temperature: float
    status: int
    cell_voltages: List[float]
    crc_ok: bool = True
    raw_unknown: int = 0
    #: epoch seconds; filled in by the transport/manager, not the decoder.
    timestamp: Optional[float] = None
    #: stable battery identity (BLE MAC / address), filled in by the transport.
    address: Optional[str] = None
    name: Optional[str] = None

    # -- derived helpers ----------------------------------------------------

    @property
    def power(self) -> float:
        """Instantaneous power in watts (+charging / -discharging)."""
        return self.voltage * self.current

    @property
    def charging_power(self) -> float:
        return max(0.0, self.power)

    @property
    def discharging_power(self) -> float:
        return abs(min(0.0, self.power))

    @property
    def remaining_capacity(self) -> float:
        """Estimated remaining amp-hours from SoC × total capacity."""
        return self.total_capacity * (self.soc / 100.0)

    @property
    def active_cells(self) -> List[float]:
        """Cell voltages with trailing zero (absent) cells removed."""
        cells = [c for c in self.cell_voltages if c > 0.05]
        return cells or self.cell_voltages[:HLX_CELLS]

    @property
    def min_cell(self) -> float:
        cells = self.active_cells
        return min(cells) if cells else 0.0

    @property
    def max_cell(self) -> float:
        cells = self.active_cells
        return max(cells) if cells else 0.0

    @property
    def cell_delta(self) -> float:
        """Spread between the highest and lowest cell (volts)."""
        cells = self.active_cells
        return (max(cells) - min(cells)) if cells else 0.0

    @property
    def min_cell_index(self) -> int:
        cells = self.active_cells
        return (cells.index(min(cells)) + 1) if cells else 0

    @property
    def max_cell_index(self) -> int:
        cells = self.active_cells
        return (cells.index(max(cells)) + 1) if cells else 0

    @property
    def alarms(self) -> List[str]:
        return decode_alarms(self.status)

    @property
    def state(self) -> str:
        """``charging`` / ``discharging`` / ``standby`` from current sign."""
        if self.current > STANDBY_CURRENT_A:
            return "charging"
        if self.current < -STANDBY_CURRENT_A:
            return "discharging"
        return "standby"

    def to_dict(self) -> dict:
        return {
            "address": self.address,
            "name": self.name,
            "timestamp": self.timestamp,
            "voltage": round(self.voltage, 3),
            "current": round(self.current, 3),
            "power": round(self.power, 2),
            "charging_power": round(self.charging_power, 2),
            "discharging_power": round(self.discharging_power, 2),
            "total_capacity": round(self.total_capacity, 3),
            "remaining_capacity": round(self.remaining_capacity, 3),
            "cycles": self.cycles,
            "soc": round(self.soc, 1),
            "temperature": round(self.temperature, 1),
            "status": self.status,
            "state": self.state,
            "alarms": self.alarms,
            "crc_ok": self.crc_ok,
            "cell_voltages": [round(c, 3) for c in self.active_cells],
            "min_cell": round(self.min_cell, 3),
            "max_cell": round(self.max_cell, 3),
            "cell_delta": round(self.cell_delta, 3),
            "min_cell_index": self.min_cell_index,
            "max_cell_index": self.max_cell_index,
        }


class ProtocolError(ValueError):
    """Raised when a frame cannot be decoded."""


# ---------------------------------------------------------------------------
# Checksum
# ---------------------------------------------------------------------------


def compute_checksum(body: bytes) -> int:
    """Additive checksum over the 54 data bytes (ESPHome algorithm)."""
    return sum(body[:DATA_BYTES]) & 0xFFFF


def _stored_checksum(payload: bytes) -> int:
    """The pack stores the checksum big-endian in the last two payload bytes."""
    return (payload[_OFF_CHECKSUM] << 8) | payload[_OFF_CHECKSUM + 1]


# ---------------------------------------------------------------------------
# Decode
# ---------------------------------------------------------------------------


def decode_payload(payload: bytes) -> BatterySample:
    """Decode the 56-byte binary payload (already un-hexlified) into a sample.

    Raises :class:`ProtocolError` if the payload is the wrong size. A checksum
    mismatch is *not* fatal — it is reported via ``BatterySample.crc_ok``.
    """
    if len(payload) < PAYLOAD_BYTES:
        raise ProtocolError(
            f"payload too short: {len(payload)} < {PAYLOAD_BYTES} bytes"
        )

    body = payload[:DATA_BYTES]
    crc_ok = compute_checksum(body) == _stored_checksum(payload)

    voltage = struct.unpack_from("<H", body, _OFF_VOLTAGE)[0] * 0.001
    unknown = struct.unpack_from("<H", body, _OFF_UNKNOWN)[0]
    current = struct.unpack_from("<i", body, _OFF_CURRENT)[0] * 0.001
    capacity = struct.unpack_from("<I", body, _OFF_CAPACITY)[0] * 0.001
    cycles = struct.unpack_from("<H", body, _OFF_CYCLES)[0]

    soc_raw = struct.unpack_from("<H", body, _OFF_SOC)[0]
    # Verified hardware reports whole percent; guard against centi-percent.
    soc = soc_raw / 100.0 if soc_raw > 100 else float(soc_raw)

    temp_raw = struct.unpack_from("<H", body, _OFF_TEMP)[0]
    temperature = temp_raw * 0.1 - _KELVIN

    status = struct.unpack_from("<I", body, _OFF_STATUS)[0]

    cells = [
        struct.unpack_from("<H", body, _OFF_CELLS + 2 * i)[0] * 0.001
        for i in range(MAX_CELLS)
    ]

    return BatterySample(
        voltage=voltage,
        current=current,
        total_capacity=capacity,
        cycles=cycles,
        soc=soc,
        temperature=temperature,
        status=status,
        cell_voltages=cells,
        crc_ok=crc_ok,
        raw_unknown=unknown,
    )


def decode_frame(frame: bytes) -> BatterySample:
    """Decode a complete 121-byte wire frame into a :class:`BatterySample`."""
    if len(frame) < FRAME_LENGTH:
        raise ProtocolError(
            f"frame too short: {len(frame)} < {FRAME_LENGTH} bytes"
        )
    if frame[0] != START_BYTE:
        raise ProtocolError(f"bad start byte: 0x{frame[0]:02X} != 0x{START_BYTE:02X}")

    hex_text = bytes(frame[HEX_REGION])
    try:
        payload = binascii.unhexlify(hex_text)
    except (binascii.Error, ValueError) as exc:
        raise ProtocolError(f"payload is not valid hex: {exc}") from exc

    return decode_payload(payload)


# ---------------------------------------------------------------------------
# Frame assembly (stream of BLE notifications -> complete frames)
# ---------------------------------------------------------------------------


class FrameAssembler:
    """Reassembles fragmented BLE notifications into complete status frames.

    Feed each notification's bytes to :meth:`feed`; it yields zero or more
    fully-decoded :class:`BatterySample` objects. A new frame begins whenever a
    chunk starts with the ``0xB0`` marker.
    """

    def __init__(self, on_error: Optional[Callable[[Exception], None]] = None):
        self._buf = bytearray()
        self._on_error = on_error

    def reset(self) -> None:
        self._buf.clear()

    def feed(self, chunk: bytes) -> List[BatterySample]:
        out: List[BatterySample] = []
        if not chunk:
            return out

        # A chunk beginning with the start marker resets the working buffer.
        if chunk[0] == START_BYTE:
            self._buf.clear()
        self._buf.extend(chunk)

        # Drain complete frames. The buffer is always re-synced to a start
        # marker *before* slicing, so leading/inter-frame garbage (whatever the
        # chunk boundaries are) is discarded rather than corrupting a frame.
        while self._buf:
            if self._buf[0] != START_BYTE:
                marker = self._buf.find(START_BYTE)
                if marker < 0:
                    self._buf.clear()
                    break
                del self._buf[:marker]
                continue
            if len(self._buf) < FRAME_LENGTH:
                break  # a frame has started; wait for the rest
            frame = bytes(self._buf[:FRAME_LENGTH])
            try:
                out.append(decode_frame(frame))
                del self._buf[:FRAME_LENGTH]
            except ProtocolError as exc:
                if self._on_error:
                    self._on_error(exc)
                # Spurious marker / corrupt frame: skip just this marker byte
                # and resync to the next one.
                del self._buf[:1]

        # Bound memory if we never see a complete frame (e.g. wrong device).
        if len(self._buf) > 4 * FRAME_LENGTH:
            marker = self._buf.rfind(START_BYTE)
            if marker > 0:
                del self._buf[:marker]
            elif marker < 0:
                self._buf.clear()
        return out


# ---------------------------------------------------------------------------
# Encode (used by the simulator transport and the test-suite)
# ---------------------------------------------------------------------------


def encode_frame(sample: BatterySample) -> bytes:
    """Encode a :class:`BatterySample` into a 121-byte wire frame.

    This mirrors the real pack's framing so the simulator produces bytes that
    round-trip through :func:`decode_frame`.
    """
    body = bytearray(DATA_BYTES)

    struct.pack_into("<H", body, _OFF_VOLTAGE, int(round(sample.voltage * 1000)))
    struct.pack_into("<H", body, _OFF_UNKNOWN, sample.raw_unknown & 0xFFFF)
    struct.pack_into("<i", body, _OFF_CURRENT, int(round(sample.current * 1000)))
    struct.pack_into("<I", body, _OFF_CAPACITY, int(round(sample.total_capacity * 1000)))
    struct.pack_into("<H", body, _OFF_CYCLES, sample.cycles & 0xFFFF)
    struct.pack_into("<H", body, _OFF_SOC, int(round(sample.soc)) & 0xFFFF)
    struct.pack_into("<H", body, _OFF_TEMP, int(round((sample.temperature + _KELVIN) / 0.1)) & 0xFFFF)
    struct.pack_into("<I", body, _OFF_STATUS, sample.status & 0xFFFFFFFF)

    cells = list(sample.cell_voltages) + [0.0] * MAX_CELLS
    for i in range(MAX_CELLS):
        struct.pack_into("<H", body, _OFF_CELLS + 2 * i, int(round(cells[i] * 1000)) & 0xFFFF)

    checksum = compute_checksum(body)
    payload = bytes(body) + struct.pack(">H", checksum)  # stored big-endian

    frame = bytearray(FRAME_LENGTH)
    frame[0] = START_BYTE
    frame[HEX_REGION] = binascii.hexlify(payload).upper()
    frame[113:121] = bytes([END_BYTE]) * 8
    return bytes(frame)
