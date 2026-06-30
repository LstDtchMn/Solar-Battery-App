# KiloVault HLX+ BLE Protocol

This document captures the Bluetooth Low Energy (BLE) protocol used by KiloVault
HLX / HLX+ LiFePO4 batteries (OEM: Topband). KiloVault went out of business and
the official **HLX iT** phone app was pulled from the app stores, so this is a
community / reverse-engineered specification.

It was assembled from:

- The KiloVault HLX+ User Manual (Rev 1.1.3, 08/2021) — alarm names, data fields.
- `fancygaphtrn/esphome` `kilovault_bms_ble` component (the C++ decoder verified
  against real hardware).
- `alexphredorg/kvbms` (`ble_test.py`, `ble_server.js`) — Python client and a
  Node mock peripheral that was used to validate the status bitfield against the
  real iPhone app.

If you have a real battery and a BLE sniffer, please help refine the
`UNKNOWN`/reserved fields and the upper status bits.

---

## 1. GATT layout

| Role                  | UUID    | Notes                                            |
|-----------------------|---------|--------------------------------------------------|
| BMS service           | `0xFFE0`| Primary service advertised by the battery        |
| Status **notify**     | `0xFFE4`| Subscribe for ~1 Hz status frames (handle 0x12)  |
| Control **write**     | `0xFA02`| Write commands (handle 0x15) — largely unused    |
| Model number (string) | `0x2A24`| Standard Device Information characteristic        |
| Serial number         | `0x2A25`| Standard Device Information characteristic         |
| Firmware revision     | `0x2A26`| Standard Device Information characteristic         |

The advertised **device name** encodes the battery, e.g. `9-12V150Ah-105` or
`5-12V300Ah-019`. The battery streams status autonomously once you subscribe to
`0xFFE4`; no polling command is required for monitoring.

---

## 2. Frame framing & encoding

The battery sends one logical **status frame** roughly once per second, split
across several BLE notifications (each ≤ the negotiated MTU, typically ~20 B).

A complete frame is **121 bytes** on the wire:

```
byte 0        : 0xB0                      start marker
byte 1..112   : 112 ASCII-hex characters  -> 56 bytes of binary payload
byte 113..120 : "RRRRRRRR" (0x52 * 8)      end marker
```

The body is **ASCII-hex text**: each payload byte is sent as two hex characters
(`'0'..'9'`, `'A'..'F'`). Only the leading `0xB0` and the trailing `R`s are raw.
Because the body is restricted to hex characters and `R`, the byte `0xB0` never
occurs inside a frame, so it is a safe delimiter.

### Assembly rule

1. When a notification's **first byte is `0xB0`**, start a new frame (discard any
   partial buffer).
2. Otherwise append the notification to the current buffer.
3. When the buffer reaches 121 bytes, decode bytes `1..112` as the hex payload.

### Decoding the payload

```
hex   = frame[1:113].decode("ascii")   # 112 chars
data  = unhexlify(hex)                  # 56 bytes
body  = data[0:54]                      # 54 data bytes
crc   = data[54:56]                     # 16-bit checksum
```

---

## 3. Payload layout (little-endian)

All multi-byte integers are **little-endian**.

| Offset | Type  | Field             | Scaling / conversion          | Unit |
|-------:|-------|-------------------|-------------------------------|------|
| 0      | u16   | `total_voltage`   | × 0.001                       | V    |
| 2      | u16   | _unknown_         | (reserved / not decoded)      | —    |
| 4      | i32   | `current`         | × 0.001, **signed**           | A    |
| 8      | u32   | `total_capacity`  | × 0.001                       | Ah   |
| 12     | u16   | `cycles`          | ×1                            | —    |
| 14     | u16   | `state_of_charge` | percent (0–100), see §3.1     | %    |
| 16     | u16   | `temperature`     | × 0.1 then − 273.15 (Kelvin)  | °C   |
| 18     | u32   | `status`          | alarm bitfield, see §4         | —    |
| 22     | u16   | `cell_voltage[1]` | × 0.001                       | V    |
| 24     | u16   | `cell_voltage[2]` | × 0.001                       | V    |
| 26     | u16   | `cell_voltage[3]` | × 0.001                       | V    |
| 28     | u16   | `cell_voltage[4]` | × 0.001                       | V    |
| 30..53 | u16×12| `cell_voltage[5..16]` | × 0.001 (0 on a 4-cell HLX+)| V    |
| 54     | u16   | `checksum`        | additive, see §5              | —    |

The HLX+ is a 4S pack (4 cells in series, ~3.2 V each → ~12.8 V nominal). Cells
5–16 are present in the frame layout but read 0 on these batteries.

### 3.1 State of charge

The verified ESPHome decoder publishes the raw `state_of_charge` word directly as
a percentage (e.g. `91` → 91 %), which matches the manual and the app's "SOC 82%"
display. This decoder therefore treats the raw value as a whole percent. As a
safety net, if the raw value exceeds 100 it is interpreted as centi-percent
(value ÷ 100), so both encodings decode sensibly.

### 3.2 Current sign convention

`current` is signed. **Positive = charging, negative = discharging** (this matches
the ESPHome component, which derives `charging_power = max(0, V·I)` and
`discharging_power = |min(0, V·I)|`). Operating state is derived from current with
a small dead-band: `|I| < 0.1 A` → *standby*.

---

## 4. Status / alarm bitfield (offset 18, u32)

The low byte holds the eight protection alarms shown on the manual's "Battery
Information" screen. Bit 21 is short-circuit. These were confirmed by driving a
mock BLE peripheral (`ble_server.js`) and watching the official iPhone app light
up each indicator.

| Bit  | Mask        | Alarm | Meaning                       |
|-----:|-------------|-------|-------------------------------|
| 0    | `0x00000001`| HTC   | High-temperature charging     |
| 1    | `0x00000002`| HTD   | High-temperature discharging  |
| 2    | `0x00000004`| LTC   | Low-temperature charging      |
| 3    | `0x00000008`| LTD   | Low-temperature discharging   |
| 4    | `0x00000010`| OCD   | Over-current discharging      |
| 5    | `0x00000020`| OCC   | Over-current charging         |
| 6    | `0x00000040`| LV    | Low voltage (cell/pack)       |
| 7    | `0x00000080`| HV    | High voltage (cell/pack)      |
| 21   | `0x00200000`| SCD   | Short-circuit discharge       |

A `status` whose low 16 bits are `0` is treated by the ESPHome decoder as an
"empty/standby" frame and skipped. Real packs report a non-zero status word
(e.g. `0x0100`) during normal operation; the upper bits (a normal pack often
reads `0x100`, and values like `0x0D14`/`0x0D18` have been observed) are not yet
fully decoded and are preserved as the raw `status` value for later analysis.

---

## 5. Checksum

The verified (real-hardware) ESPHome algorithm:

```
checksum = sum(body[0:54]) & 0xFFFF            # add all 54 data bytes
remote   = (data[54] << 8) | data[55]          # stored big-endian
valid    = checksum == remote
```

> Note: `ble_server.js` (a mock peripheral, not real hardware) computes/stores the
> checksum slightly differently, so do not treat it as authoritative. This monitor
> validates with the ESPHome algorithm but, by default, **does not drop** frames on
> mismatch — it flags `crc_ok = False` and keeps the data, because a remote
> off-grid install should never lose monitoring over a checksum edge case. Strict
> dropping is available via configuration.

---

## 6. Control / write commands (offset, partially known)

The control characteristic is `0xFA02`. The ESPHome component sketches a 9-byte
command frame but does not actually use it for monitoring:

```
[0] start-of-frame
[1] 0x16            device address
[2] function
[3] data length
[4] value
[5] checksum low  ]  checksum = sum(frame[1:5]) & 0xFFFF
[6] checksum high ]
[7] 0x52 'R'        end
[8] 0x52 'R'        end
```

The manual's app gates "Battery Information" behind PIN `1234` and "Rename Device"
behind PIN `5678`, but the on-wire rename/command format is not documented here.
**Monitoring needs no writes** — the battery streams status as soon as you
subscribe — so this monitor is read-only by default and never sends control
writes to the pack.
