# Hardware setups

You have two ways to get the batteries' Bluetooth signal into the PC.

## Option A — the PC's own Bluetooth (simplest)

```
[ HLX+ batteries ] ~~BLE~~> [ Windows PC running kvmon serve ]
```

- Any PC with Bluetooth 4.0 / BLE works. If yours has none, a ~$10 USB BLE
  dongle (CSR8510 / Realtek RTL8761B based) is enough.
- BLE range is short indoors (a few metres through walls). Keep the PC within
  range of the bank, or use Option B.
- No extra hardware to build. Just `pip install bleak` and `kvmon serve`.

## Option B — ESP32 bridge (range / no-Bluetooth PCs)

```
[ HLX+ batteries ] ~~BLE~~> [ ESP32 ] --USB serial--> [ PC running kvmon serve --serial COMx ]
```

Put the ESP32 next to the battery bank (BLE line-of-sight is rated ~100 m) and
run a USB cable to the PC. The ESP32 only relays raw frames; the PC decodes
them, so there is a single source of truth for the protocol.

### Bill of materials

- 1× ESP32 dev board (e.g. ESP32-DevKitC, WROOM-32). Any classic ESP32 with
  Bluetooth works; ESP32-C3/S3 also work with NimBLE.
- 1× USB cable to the PC.
- Optional: a USB power supply if you run it standalone with the WiFi variant.

### Flashing

See [`../firmware/esp32_bridge/README.md`](../firmware/esp32_bridge/README.md).
In short:

```bash
pip install platformio
cd firmware/esp32_bridge
pio run -t upload
```

Then on the PC:

```bash
kvmon serve --serial COM3        # Windows
kvmon serve --serial /dev/ttyUSB0  # Linux
```

### How many batteries per ESP32?

The firmware connects to up to **3** batteries at once (the NimBLE default).
For a bigger bank:

- raise `CONFIG_BT_NIMBLE_MAX_CONNECTIONS` in `platformio.ini` (board limits
  apply — classic ESP32 tops out around 9), **or**
- run one ESP32 per group of batteries, each on its own USB port, and start one
  `kvmon` per port (each with its own `--port` and `--db`), **or**
- combine them in Home Assistant via an ESPHome Bluetooth proxy if you already
  run HA (the protocol is also implemented as an ESPHome component upstream).

### Optional: WiFi instead of USB

The same line protocol (`S`/`F`/`D` lines) works over a TCP socket. If you adapt
the firmware to host a TCP server on port 3333 and print the same lines to
connected clients, point the monitor at it via `config.toml`:

```toml
[transport]
type = "serial"
serial_port = "192.168.1.50:3333"   # host:port enables TCP mode
```

(The Python side already supports `host:port` in `serial_port`.) This lets the
ESP32 sit anywhere on the cabin's local network with no cable to the PC — still
fully offline, no cloud.

## Physical alerts (siren / light) for an unattended cabin

An on-screen alarm is useless if nobody is watching. The monitor can drive a
physical alert when an alarm fires — all local, no internet. Configure it under
`[hardware]` in `config.toml` (run `kvmon init-config` for a template):

- **USB serial relay** (works on Windows and Linux): a cheap USB relay board
  switches a 12 V siren or light. Set `serial_relay_port` and the hex byte
  commands `serial_relay_on` / `serial_relay_off` for your board.
- **Run any command**: `command = "..."` runs when an alert starts (the active
  alarm codes are passed in the `KV_ALARMS` environment variable) — e.g. play a
  sound file, or toggle a smart plug on the LAN.
- **Raspberry Pi GPIO**: set `gpio_pin` (needs `pip install gpiozero`) to drive a
  relay/buzzer directly from a Pi's header.

`alert_on` chooses when it triggers: `critical` (default), `any` alarm, or
`none`. The alert is edge-triggered — it activates when an alarm appears and
releases when all clear, so it never chatters. The Diagnostics page shows whether
hardware alerting is active.

## Safety

- This tool is **read-only**: it subscribes to status notifications and never
  writes to the BMS.
- It does not replace proper instrumentation. Before working on the system,
  confirm voltages with a real meter, as the KiloVault manual advises.
- Mind the temperature guidance in the manual — don't charge a cold pack; the
  monitor's low-temperature alarm is there to warn you, not to protect the pack.
