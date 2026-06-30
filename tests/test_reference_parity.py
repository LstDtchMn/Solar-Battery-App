"""Parity test against the authoritative ESPHome reference decoder.

This project's ``protocol.decode_frame`` uses ``struct`` over the little-endian
payload, whereas the upstream reference
(``fancygaphtrn/esphome`` ``kilovault_bms_ble``) decodes the ASCII-hex frame with
nibble-level bit-twiddling (``asciiToInt`` + ``get_16bit``/``get_32bit``). Both
must agree.

We re-implement the ESPHome algorithm here and assert field-for-field agreement
across many random frames, so any future change to the decoder that drifts from
the validated reference is caught. The single known difference is documented:
ESPHome signs a negative current with ``- 4294967295`` (off by one count);
``struct '<i'`` is exact, so the two may differ by at most 1 mA on negatives.
"""

import random

import pytest

from kilovault.protocol import BatterySample, decode_frame, encode_frame


# --- faithful re-implementation of the ESPHome C++ decoder ------------------
def _ascii_to_int(c: int) -> int:
    if 48 <= c <= 57:
        return c - 48
    if 65 <= c <= 70:
        return c - 65 + 10
    if 97 <= c <= 102:
        return c - 97 + 10
    return c


def esphome_decode(frame: bytes) -> dict:
    data = [_ascii_to_int(b) for b in frame]

    def g8(i):
        return (data[i] << 4) | data[i + 1]

    def g16(i):
        return (data[i + 2] << 12) | (data[i + 3] << 8) | (data[i] << 4) | data[i + 1]

    def g32(i):
        return (g16(i + 4) << 16) | g16(i + 0)

    frame_size = 110
    remote_crc = (g8(frame_size - 1) << 8) + g8(frame_size + 1)
    crc = 0
    for i in range(1, frame_size - 2, 2):
        crc = (crc + g8(i)) & 0xFFFF

    current = g32(9)
    if current > 2147483647:
        current = current - 4294967295  # ESPHome's (off-by-one) signing

    return {
        "voltage": g16(1) * 0.001,
        "current": current * 0.001,
        "capacity": g32(17) * 0.001,
        "cycles": g16(25),
        "soc": g16(29),
        "temp": g16(33) * 0.1 - 273.15,
        "status": g16(37),
        "afestatus": g16(41),
        "cells": [round(g16(41 + i * 4) * 0.001, 3) for i in range(1, 5)],
        "crc_ok": crc == remote_crc,
    }


def _random_sample(rng: random.Random) -> BatterySample:
    return BatterySample(
        voltage=round(rng.uniform(10, 15), 3),
        current=round(rng.uniform(-200, 200), 3),
        total_capacity=round(rng.uniform(50, 320), 3),
        cycles=rng.randint(0, 9000),
        soc=rng.randint(0, 100),
        temperature=round(rng.uniform(-20, 60), 1),
        status=rng.choice([0x100, 0x180, 0x140, 0x120, 0x200100]),
        cell_voltages=[round(rng.uniform(2.9, 3.6), 3) for _ in range(4)] + [0.0] * 12,
    )


def test_decoder_matches_esphome_reference():
    rng = random.Random(7)
    for _ in range(1000):
        frame = encode_frame(_random_sample(rng))
        mine = decode_frame(frame)
        ref = esphome_decode(frame)

        assert mine.voltage == pytest.approx(ref["voltage"], abs=1e-9)
        assert mine.total_capacity == pytest.approx(ref["capacity"], abs=1e-9)
        assert mine.cycles == ref["cycles"]
        assert mine.soc == pytest.approx(ref["soc"], abs=1e-9)
        assert mine.temperature == pytest.approx(ref["temp"], abs=1e-9)
        # The 32-bit status splits into ESPHome's status (low 16) + afestatus (high 16).
        assert (mine.status & 0xFFFF) == ref["status"]
        assert ((mine.status >> 16) & 0xFFFF) == ref["afestatus"]
        assert [round(c, 3) for c in mine.active_cells] == ref["cells"]
        assert mine.crc_ok == ref["crc_ok"]
        # Current agrees except for ESPHome's <=1 mA signing off-by-one.
        assert abs(mine.current - ref["current"]) <= 0.001 + 1e-9


def test_reference_alarm_bits_round_trip():
    # The status/afestatus split must preserve the documented alarm bits,
    # including the high-word short-circuit bit (0x200000).
    s = BatterySample(
        voltage=13.0, current=-5.0, total_capacity=100.0, cycles=1, soc=50.0,
        temperature=20.0, status=0x00200080,  # HV (low) + SCD (high word)
        cell_voltages=[3.25] * 4 + [0.0] * 12,
    )
    ref = esphome_decode(encode_frame(s))
    assert ref["status"] == 0x0080      # HV
    assert ref["afestatus"] == 0x0020   # 0x200000 >> 16
    mine = decode_frame(encode_frame(s))
    assert set(mine.alarms) == {"HV", "SCD"}
