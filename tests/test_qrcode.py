"""Tests for the dependency-free QR encoder (kilovault/qrcode.py).

The encoder was validated bit-for-bit against the reference `segno` library for
all versions 1–10 and all 8 masks during development; `segno` is NOT a project
dependency, so here we lock in that validated output with a golden matrix plus
structural invariants that any correct QR symbol must satisfy.
"""

import pytest

from kilovault import qrcode as qr


# Golden output for a fixed URL (auto version + auto mask), captured from the
# segno-validated encoder. Guards against regressions in encoding/placement.
GOLDEN_INPUT = "http://cabin.local:8765/?token=TESTTOKEN123"
GOLDEN_ROWS = [
    "111111101100001111001101001111111",
    "100000100111100011010110101000001",
    "101110100001110100011010101011101",
    "101110101000001101101100001011101",
    "101110101000001000111100101011101",
    "100000101001000011010010101000001",
    "111111101010101010101010101111111",
    "000000001000100111000111000000000",
    "100010111010001001110110111111001",
    "100111011001010110001001110101101",
    "111101101011100101000111010001010",
    "111000000000001011100100110000010",
    "111111111101111100101001101001001",
    "110110000000100010010110111100100",
    "000010101101011011110001100000110",
    "010000001101010001111100011100000",
    "101111110101101101100110101011110",
    "011000011001001111101101010001111",
    "100100101001111100100001111001010",
    "110110000000001000010011001000000",
    "110100100110000110000011011001001",
    "110001010001110001111001100101110",
    "000101111010000100101011000001010",
    "000010000001110111100100010000011",
    "111001110111001001110110111111110",
    "000000001110001110001000100010101",
    "111111101010110111000110101011010",
    "100000100011100101101100100011010",
    "101110101101100000101000111111000",
    "101110100100011011010010001011110",
    "101110100010111010110111001111000",
    "100000100011111001111100100100000",
    "111111101001110111001110101010101",
]


def test_golden_matrix():
    m = qr.matrix(GOLDEN_INPUT)
    got = ["".join(str(c) for c in row) for row in m]
    assert got == GOLDEN_ROWS


def _finder_ok(m, r, c):
    n = len(m)
    # outer 7x7 ring dark, one-module light gap, dark 3x3 core
    for i in range(7):
        if not (m[r][c + i] and m[r + 6][c + i] and m[r + i][c] and m[r + i][c + 6]):
            return False
    return m[r + 1][c + 1] == 0 and m[r + 3][c + 3] == 1


@pytest.mark.parametrize("data,version", [
    ("x", 1), ("kilovault", 1), ("http://192.168.0.2:8765/?token=abcdefgh", 0),
    ("A" * 100, 0), ("A" * 200, 0),
])
def test_structure(data, version):
    m = qr.matrix(data, version)
    n = len(m)
    ver = version or qr._choose_version(len(data.encode()))
    assert n == ver * 4 + 17                     # correct symbol size
    assert all(len(row) == n for row in m)        # square
    assert all(v in (0, 1) for row in m for v in row)
    assert _finder_ok(m, 0, 0)                    # top-left finder
    assert _finder_ok(m, 0, n - 7)                # top-right finder
    assert _finder_ok(m, n - 7, 0)                # bottom-left finder
    # timing pattern alternates on row/col 6
    assert m[6][8] == 1 and m[6][9] == 0
    # quiet-zone corners are light (matrix has no border; check via svg instead)


def test_version_selection_grows_with_length():
    assert qr._choose_version(len(b"short")) < qr._choose_version(150)


def test_too_long_raises():
    with pytest.raises(ValueError):
        qr.matrix("A" * 400)  # exceeds version-10 byte capacity at level M


def test_svg_is_wellformed_and_offline():
    svg = qr.svg(GOLDEN_INPUT, scale=6, border=4)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "<rect" in svg
    # fully self-contained: no external references
    assert "http://" not in svg.split(">", 1)[1]  # ignore the xmlns on the root
    assert "url(" not in svg and "<image" not in svg
