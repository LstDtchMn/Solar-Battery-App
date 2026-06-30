"""Tests for the KiloVault BLE protocol decoder/encoder.

Ground-truth values come from the reference mock peripheral
(``alexphredorg/kvbms`` ``ble_server.js``), which was validated against the real
iPhone app, and from the ESPHome decoder.
"""

import binascii
import struct

import pytest

from kilovault import protocol as P
from kilovault.protocol import BatterySample, FrameAssembler


def build_reference_frame(status: int = 0x0100) -> bytes:
    """Recreate the exact 56-byte payload from ble_server.js, then frame it.

    Mirrors ``computeSendBuffer`` precisely (little-endian fields), so it is an
    independent construction from ``encode_frame`` and a real cross-check.
    """
    buf = bytearray(54)
    o = 0

    def w16(v):
        nonlocal o
        struct.pack_into("<H", buf, o, v)
        o += 2

    def w32(v, signed=False):
        nonlocal o
        struct.pack_into("<i" if signed else "<I", buf, o, v)
        o += 4

    w16(13310)        # voltage -> 13.310 V
    w16(0)            # unknown
    w32(0, signed=True)  # current -> 0 A
    w32(102810)       # capacity -> 102.810 Ah
    w16(19)           # cycles
    w16(91)           # soc -> 91 %
    w16(2921)         # temp -> 18.95 C
    w32(status)       # status (32-bit)
    w16(3327)         # cell1 -> 3.327
    w16(3330)         # cell2 -> 3.330
    w16(3329)         # cell3 -> 3.329
    w16(3327)         # cell4 -> 3.327
    # cells 5..16 stay zero
    assert o == 22 + 8  # we wrote through cell4; remaining 12 cells are zero

    checksum = sum(buf) & 0xFFFF
    payload = bytes(buf) + struct.pack(">H", checksum)

    frame = bytearray(121)
    frame[0] = 0xB0
    frame[1:113] = binascii.hexlify(payload).upper()
    frame[113:121] = b"R" * 8
    return bytes(frame)


def test_decode_reference_values():
    sample = P.decode_frame(build_reference_frame())
    assert sample.voltage == pytest.approx(13.310, abs=1e-6)
    assert sample.current == pytest.approx(0.0, abs=1e-6)
    assert sample.total_capacity == pytest.approx(102.810, abs=1e-6)
    assert sample.cycles == 19
    assert sample.soc == pytest.approx(91.0)
    assert sample.temperature == pytest.approx(18.95, abs=1e-6)
    assert sample.crc_ok is True


def test_decode_cells_and_delta():
    sample = P.decode_frame(build_reference_frame())
    assert sample.active_cells == pytest.approx([3.327, 3.330, 3.329, 3.327])
    assert sample.min_cell == pytest.approx(3.327)
    assert sample.max_cell == pytest.approx(3.330)
    assert sample.cell_delta == pytest.approx(0.003, abs=1e-6)
    assert sample.max_cell_index == 2  # cell 2 is highest
    # cells 5..16 are zero and must be dropped from "active"
    assert len(sample.active_cells) == 4


def test_state_from_current_sign():
    base = build_reference_frame()
    # patch current field to +5 A and -5 A by re-encoding via encode_frame
    s = P.decode_frame(base)
    s.current = 5.0
    assert s.state == "charging"
    s.current = -5.0
    assert s.state == "discharging"
    s.current = 0.0
    assert s.state == "standby"


def test_power_split():
    s = P.decode_frame(build_reference_frame())
    s.voltage = 13.0
    s.current = 10.0
    assert s.power == pytest.approx(130.0)
    assert s.charging_power == pytest.approx(130.0)
    assert s.discharging_power == pytest.approx(0.0)
    s.current = -10.0
    assert s.charging_power == pytest.approx(0.0)
    assert s.discharging_power == pytest.approx(130.0)


@pytest.mark.parametrize(
    "status,expected",
    [
        (0x00000001, ["HTC"]),
        (0x00000080, ["HV"]),
        (0x00000040, ["LV"]),
        (0x00000030, ["OCD", "OCC"]),
        (0x00200000, ["SCD"]),
        (0x000000FF, ["HTC", "HTD", "LTC", "LTD", "OCD", "OCC", "LV", "HV"]),
        (0x00000100, []),  # the "normal operation" bit is not an alarm
    ],
)
def test_alarm_decoding(status, expected):
    assert P.decode_alarms(status) == expected


def test_alarm_via_sample():
    frame = build_reference_frame(status=0x00000080 | 0x00000100)
    s = P.decode_frame(frame)
    assert s.alarms == ["HV"]


def test_roundtrip_encode_decode():
    original = BatterySample(
        voltage=13.245,
        current=-12.5,
        total_capacity=100.0,
        cycles=42,
        soc=87.0,
        temperature=24.3,
        status=0x0100,
        cell_voltages=[3.310, 3.312, 3.309, 3.311] + [0.0] * 12,
    )
    frame = P.encode_frame(original)
    assert len(frame) == P.FRAME_LENGTH
    decoded = P.decode_frame(frame)
    assert decoded.voltage == pytest.approx(13.245, abs=1e-3)
    assert decoded.current == pytest.approx(-12.5, abs=1e-3)
    assert decoded.cycles == 42
    assert decoded.soc == pytest.approx(87.0)
    assert decoded.temperature == pytest.approx(24.3, abs=0.05)
    assert decoded.active_cells == pytest.approx([3.310, 3.312, 3.309, 3.311])
    assert decoded.crc_ok is True


def test_soc_centipercent_guard():
    s = BatterySample(
        voltage=13.0, current=0.0, total_capacity=100.0, cycles=0,
        soc=91.0, temperature=20.0, status=0x100,
        cell_voltages=[3.25] * 4 + [0.0] * 12,
    )
    # Manually craft a frame where SoC raw = 9100 (centi-percent encoding)
    frame = bytearray(P.encode_frame(s))
    payload = bytearray(binascii.unhexlify(bytes(frame[1:113])))
    struct.pack_into("<H", payload, P._OFF_SOC, 9100)
    # fix checksum
    chk = sum(payload[:54]) & 0xFFFF
    struct.pack_into(">H", payload, 54, chk)
    frame[1:113] = binascii.hexlify(bytes(payload)).upper()
    decoded = P.decode_frame(bytes(frame))
    assert decoded.soc == pytest.approx(91.0)


def test_checksum_mismatch_is_not_fatal():
    frame = bytearray(build_reference_frame())
    # Corrupt one hex digit of a data byte (not the checksum bytes).
    frame[2] = ord("F") if frame[2] != ord("F") else ord("0")
    sample = P.decode_frame(bytes(frame))
    assert sample.crc_ok is False  # flagged but still decoded


def test_assembler_fragmented_stream():
    frame = build_reference_frame()
    asm = FrameAssembler()
    out = []
    # feed in 20-byte BLE-sized chunks
    for i in range(0, len(frame), 20):
        out.extend(asm.feed(frame[i : i + 20]))
    assert len(out) == 1
    assert out[0].voltage == pytest.approx(13.310, abs=1e-6)


def test_assembler_back_to_back_frames():
    a = build_reference_frame(status=0x0100)
    b = build_reference_frame(status=0x0180)
    asm = FrameAssembler()
    out = asm.feed(a + b)
    assert len(out) == 2
    assert out[1].alarms == ["HV"]


def test_assembler_resyncs_after_garbage():
    frame = build_reference_frame()
    asm = FrameAssembler()
    asm.feed(b"\x01\x02\x03garbage")  # no marker -> ignored / bounded
    out = asm.feed(frame)
    assert len(out) == 1


def test_assembler_leading_garbage_same_chunk():
    # A single chunk: one junk byte followed by a full valid frame. The junk is
    # not the start marker, so the assembler must resync and still decode it.
    frame = build_reference_frame()
    asm = FrameAssembler()
    out = asm.feed(b"\x99" + frame)
    assert len(out) == 1
    assert out[0].voltage == pytest.approx(13.310, abs=1e-6)


def test_assembler_interframe_garbage_not_chunk_aligned():
    frame = build_reference_frame()
    asm = FrameAssembler()
    assert len(asm.feed(frame)) == 1
    # Junk that does not start the chunk must not drop the following frame.
    out = asm.feed(b"\x99\x99" + frame)
    assert len(out) == 1


def test_assembler_corrupt_frame_then_good_frame():
    good = build_reference_frame()
    corrupt = bytearray(build_reference_frame())
    corrupt[5] = ord("Z")  # invalid hex char -> decode fails
    asm = FrameAssembler()
    out = asm.feed(bytes(corrupt) + good)
    # the corrupt frame is skipped, the good one still comes through
    assert len(out) == 1
    assert out[0].voltage == pytest.approx(13.310, abs=1e-6)


def test_bad_start_byte_raises():
    frame = bytearray(build_reference_frame())
    frame[0] = 0x00
    with pytest.raises(P.ProtocolError):
        P.decode_frame(bytes(frame))


def test_short_frame_raises():
    with pytest.raises(P.ProtocolError):
        P.decode_frame(b"\xb0" + b"00" * 10)
