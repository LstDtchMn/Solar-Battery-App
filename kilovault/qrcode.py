"""A tiny, dependency-free QR Code generator (byte mode) for offline use.

Just enough to turn the dashboard's phone URL into a QR the user can scan with
an iPhone — no `qrcode`/`Pillow`/`segno`, no network, pure standard library so
it satisfies the project's "no new runtime dependencies in the core" rule.

Supports versions 1–10 at error-correction level M (auto-picks the smallest that
fits), which covers any LAN URL + token we generate. Output is a boolean matrix
or a self-contained SVG string.

Reference: ISO/IEC 18004. Validated bit-for-bit against `segno` in the tests.
"""

from __future__ import annotations

from typing import List

# --- Galois field GF(256) for Reed–Solomon, primitive polynomial 0x11d --------
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(degree: int) -> List[int]:
    """Reed–Solomon generator polynomial of the given degree."""
    poly = [1]
    for i in range(degree):
        # multiply poly by (x - alpha^i)
        nxt = [0] * (len(poly) + 1)
        for j, c in enumerate(poly):
            nxt[j] ^= c
            nxt[j + 1] ^= _gf_mul(c, _EXP[i])
        poly = nxt
    return poly


def _rs_ec(data: List[int], ec_len: int) -> List[int]:
    """Compute `ec_len` error-correction codewords for `data`."""
    gen = _rs_generator(ec_len)
    rem = [0] * ec_len
    for d in data:
        factor = d ^ rem[0]
        rem = rem[1:] + [0]
        for i in range(ec_len):
            rem[i] ^= _gf_mul(gen[i + 1], factor)
    return rem


# --- Per-version tables (error-correction level M) ----------------------------
# (ec codewords per block, [(num_blocks, data_codewords_per_block), ...])
_ECC_M = {
    1: (10, [(1, 16)]),
    2: (16, [(1, 28)]),
    3: (26, [(1, 44)]),
    4: (18, [(2, 32)]),
    5: (24, [(2, 43)]),
    6: (16, [(4, 27)]),
    7: (18, [(4, 31)]),
    8: (22, [(2, 38), (2, 39)]),
    9: (22, [(3, 36), (2, 37)]),
    10: (26, [(4, 43), (1, 44)]),
}

# Alignment-pattern centre coordinates per version.
_ALIGN = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
    7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}


def _total_data_codewords(version: int) -> int:
    _, blocks = _ECC_M[version]
    return sum(n * cw for n, cw in blocks)


def _capacity_bytes(version: int) -> int:
    """Max UTF-8 payload bytes in byte mode at level M for this version."""
    data_bits = _total_data_codewords(version) * 8
    count_bits = 8 if version <= 9 else 16
    return (data_bits - 4 - count_bits) // 8


def _choose_version(n_bytes: int) -> int:
    for v in range(1, 11):
        if n_bytes <= _capacity_bytes(v):
            return v
    raise ValueError(
        f"data too long for QR versions 1–10 ({n_bytes} bytes; max "
        f"{_capacity_bytes(10)})")


# --- Bit buffer ---------------------------------------------------------------
class _Bits:
    def __init__(self):
        self.bits: List[int] = []

    def put(self, value: int, length: int) -> None:
        for i in range(length - 1, -1, -1):
            self.bits.append((value >> i) & 1)


# Remainder bits appended to the final message for certain versions
# (ISO/IEC 18004 §7.6). Only versions in our 1–10 range that are non-zero:
_REMAINDER = {2: 7, 3: 7, 4: 7, 5: 7, 6: 7}


def _encode_data(payload: bytes, version: int) -> List[int]:
    total_cw = _total_data_codewords(version)
    total_bits = total_cw * 8
    b = _Bits()
    b.put(0b0100, 4)                                  # byte mode
    b.put(len(payload), 8 if version <= 9 else 16)    # char count
    for byte in payload:
        b.put(byte, 8)
    # Terminator: up to 4 zero bits (fewer if near capacity).
    b.bits.extend([0] * min(4, total_bits - len(b.bits)))
    # Pad bits to the next codeword boundary. This mirrors the reference
    # encoder (segno) exactly so output is validated bit-for-bit against it.
    b.bits.extend([0] * (8 - (len(b.bits) % 8)))
    del b.bits[total_bits:]  # never overshoot capacity
    # Pad codewords 0xEC / 0x11, alternating, until full.
    pad = [0xEC, 0x11]
    i = 0
    codewords = [int("".join(map(str, b.bits[j:j + 8])), 2)
                 for j in range(0, len(b.bits), 8)]
    while len(codewords) < total_cw:
        codewords.append(pad[i % 2])
        i += 1
    return codewords


def _interleave(codewords: List[int], version: int) -> List[int]:
    ec_len, blocks = _ECC_M[version]
    data_blocks: List[List[int]] = []
    ec_blocks: List[List[int]] = []
    pos = 0
    for num, cw in blocks:
        for _ in range(num):
            chunk = codewords[pos:pos + cw]
            pos += cw
            data_blocks.append(chunk)
            ec_blocks.append(_rs_ec(chunk, ec_len))
    result: List[int] = []
    maxdata = max(len(b) for b in data_blocks)
    for i in range(maxdata):
        for blk in data_blocks:
            if i < len(blk):
                result.append(blk[i])
    for i in range(ec_len):
        for blk in ec_blocks:
            result.append(blk[i])
    return result


# --- Matrix construction ------------------------------------------------------
def _new_matrix(size: int):
    return [[None] * size for _ in range(size)]


def _place_finder(m, r, c) -> None:
    for dr in range(-1, 8):
        for dc in range(-1, 8):
            rr, cc = r + dr, c + dc
            if 0 <= rr < len(m) and 0 <= cc < len(m):
                inner = 0 <= dr <= 6 and 0 <= dc <= 6
                ring = dr in (0, 6) or dc in (0, 6)
                core = 2 <= dr <= 4 and 2 <= dc <= 4
                m[rr][cc] = 1 if (inner and (ring or core)) else 0


def _build_function_patterns(version: int):
    size = version * 4 + 17
    m = _new_matrix(size)
    # finders + separators
    _place_finder(m, 0, 0)
    _place_finder(m, 0, size - 7)
    _place_finder(m, size - 7, 0)
    # timing patterns
    for i in range(size):
        if m[6][i] is None:
            m[6][i] = 1 if i % 2 == 0 else 0
        if m[i][6] is None:
            m[i][6] = 1 if i % 2 == 0 else 0
    # alignment patterns — skip only the three that overlap the finders
    # (top-left, top-right, bottom-left corners); the ones sitting on the
    # timing line ARE placed and overwrite the timing modules beneath them.
    centers = _ALIGN[version]
    last = centers[-1] if centers else 0
    skip = {(6, 6), (6, last), (last, 6)}
    for r in centers:
        for c in centers:
            if (r, c) in skip:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    ring = max(abs(dr), abs(dc))
                    m[r + dr][c + dc] = 1 if ring != 1 else 0
    # dark module
    m[size - 8][8] = 1
    return m


def _reserve_format_bits(m):
    """Mark the format/version information cells as reserved (value -1) so data
    isn't placed there; they are filled in later."""
    size = len(m)
    for i in range(9):
        if m[8][i] is None:
            m[8][i] = -1
        if m[i][8] is None:
            m[i][8] = -1
    for i in range(8):
        if m[8][size - 1 - i] is None:
            m[8][size - 1 - i] = -1
        if m[size - 1 - i][8] is None:
            m[size - 1 - i][8] = -1
    if size >= 45:  # version >= 7: version info blocks
        for i in range(6):
            for j in range(3):
                m[size - 11 + j][i] = -1
                m[i][size - 11 + j] = -1


def _place_data(m, bits: List[int]) -> None:
    size = len(m)
    idx = 0
    upward = True
    col = size - 1
    while col > 0:
        if col == 6:  # skip the vertical timing column
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if m[row][c] is None:
                    bit = bits[idx] if idx < len(bits) else 0
                    m[row][c] = bit
                    idx += 1
        upward = not upward
        col -= 2


_MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _is_function(fm, r, c) -> bool:
    return fm[r][c] is not None


def _apply_mask(data_matrix, fm, mask_idx: int):
    size = len(data_matrix)
    fn = _MASKS[mask_idx]
    out = [row[:] for row in data_matrix]
    for r in range(size):
        for c in range(size):
            if not _is_function(fm, r, c) and fn(r, c):
                out[r][c] ^= 1
    return out


def _bch_format(fmt: int) -> int:
    g = 0b10100110111
    code = fmt << 10
    for i in range(4, -1, -1):
        if code & (1 << (i + 10)):
            code ^= g << i
    return ((fmt << 10) | code) ^ 0b101010000010010


def _bch_version(ver: int) -> int:
    g = 0b1111100100101
    code = ver << 12
    for i in range(5, -1, -1):
        if code & (1 << (i + 12)):
            code ^= g << i
    return (ver << 12) | code


def _place_format(m, mask_idx: int) -> None:
    size = len(m)
    fmt = (0b00 << 3) | mask_idx           # level M = 00
    bits = _bch_format(fmt)
    seq = [(bits >> i) & 1 for i in range(14, -1, -1)]  # MSB first, 15 bits
    # around top-left finder
    coords1 = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7),
               (8, 8), (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
    for bit, (r, c) in zip(seq, coords1):
        m[r][c] = bit
    # split copy along right + bottom edges
    coords2 = [(size - 1, 8), (size - 2, 8), (size - 3, 8), (size - 4, 8),
               (size - 5, 8), (size - 6, 8), (size - 7, 8),
               (8, size - 8), (8, size - 7), (8, size - 6), (8, size - 5),
               (8, size - 4), (8, size - 3), (8, size - 2), (8, size - 1)]
    for bit, (r, c) in zip(seq, coords2):
        m[r][c] = bit


def _place_version(m, version: int) -> None:
    if version < 7:
        return
    size = len(m)
    bits = _bch_version(version)
    seq = [(bits >> i) & 1 for i in range(18)]  # LSB first
    k = 0
    for i in range(6):
        for j in range(3):
            m[size - 11 + j][i] = seq[k]
            m[i][size - 11 + j] = seq[k]
            k += 1


def _penalty(m) -> int:
    size = len(m)
    score = 0
    # rule 1: runs of 5+ same-colour in rows/cols
    for line in list(m) + [list(col) for col in zip(*m)]:
        run = 1
        for i in range(1, size):
            if line[i] == line[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
        if run >= 5:
            score += 3 + (run - 5)
    # rule 2: 2x2 blocks
    for r in range(size - 1):
        for c in range(size - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    # rule 3: finder-like patterns
    pat1 = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    pat2 = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1]
    for line in list(m) + [list(col) for col in zip(*m)]:
        for i in range(size - 10):
            seg = line[i:i + 11]
            if seg == pat1 or seg == pat2:
                score += 40
    # rule 4: dark-module proportion vs 50%
    dark = sum(sum(row) for row in m)
    total = size * size
    percent = dark * 100 / total
    prev5 = int(percent) - int(percent) % 5
    next5 = prev5 + 5
    score += min(abs(prev5 - 50) // 5, abs(next5 - 50) // 5) * 10
    return score


def _matrix_for_mask(base, data_only, ver: int, mask_idx: int):
    cand = [row[:] for row in base]
    for r in range(len(cand)):
        for c in range(len(cand)):
            if data_only[r][c] is not None:
                bit = data_only[r][c]
                if _MASKS[mask_idx](r, c):
                    bit ^= 1
                cand[r][c] = bit
    _place_format(cand, mask_idx)
    _place_version(cand, ver)
    return cand


def matrix(data: str, version: int = 0, mask: int = -1) -> List[List[int]]:
    """Return the QR code as a list of rows of 0/1 ints. version=0 auto-picks;
    mask=-1 auto-selects the lowest-penalty mask (else forces the given mask)."""
    payload = data.encode("utf-8")
    ver = version or _choose_version(len(payload))
    if len(payload) > _capacity_bytes(ver):
        raise ValueError("data too long for the chosen QR version")
    codewords = _encode_data(payload, ver)
    final = _interleave(codewords, ver)
    bits: List[int] = []
    for cw in final:
        for i in range(7, -1, -1):
            bits.append((cw >> i) & 1)
    bits.extend([0] * _REMAINDER.get(ver, 0))  # ISO/IEC 18004 §7.6 remainder

    fm = _build_function_patterns(ver)
    _reserve_format_bits(fm)
    layout = [row[:] for row in fm]
    _place_data(layout, bits)
    # normalise: function cells keep their value; reserved (-1) -> 0 for now
    base = [[0 if v in (None, -1) else v for v in row] for row in fm]
    data_only = [[layout[r][c] if fm[r][c] is None else None
                  for c in range(len(fm))] for r in range(len(fm))]

    if mask >= 0:
        return _matrix_for_mask(base, data_only, ver, mask)
    best = None
    best_score = None
    for mask_idx in range(8):
        cand = _matrix_for_mask(base, data_only, ver, mask_idx)
        s = _penalty(cand)
        if best_score is None or s < best_score:
            best_score = s
            best = cand
    return best


def svg(data: str, version: int = 0, scale: int = 8, border: int = 4,
        dark: str = "#0e1116", light: str = "#ffffff") -> str:
    """Render the QR code as a standalone SVG string (crisp at any size)."""
    m = matrix(data, version)
    n = len(m)
    dim = (n + border * 2) * scale
    rects = []
    for r in range(n):
        for c in range(n):
            if m[r][c]:
                x = (c + border) * scale
                y = (r + border) * scale
                rects.append(f'<rect x="{x}" y="{y}" width="{scale}" '
                             f'height="{scale}"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{dim}" height="{dim}" '
        f'viewBox="0 0 {dim} {dim}" shape-rendering="crispEdges">'
        f'<rect width="{dim}" height="{dim}" fill="{light}"/>'
        f'<g fill="{dark}">{"".join(rects)}</g></svg>'
    )
