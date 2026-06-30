# ESP32 → PC bridge

Use an ESP32 when:

- your PC has no Bluetooth, or
- the batteries are too far from the PC for the PC's own radio, or
- you want a small, always-on dongle next to the battery bank.

The ESP32 connects to the batteries over BLE and forwards their raw status
frames to the PC over USB serial (no decoding on the ESP32 — the PC does that).

## Flash it

1. Install [PlatformIO](https://platformio.org/) (`pip install platformio`).
2. Plug in the ESP32 and run:

   ```
   cd firmware/esp32_bridge
   pio run -t upload
   ```

3. (Optional) Watch the raw line protocol: `pio device monitor`. You should see
   lines like `S AA:BB:.. 12V150Ah-102` and a stream of `F AA:BB:.. B0...R...`.

## Use it from the monitor

Find the serial port (Windows: Device Manager → Ports, e.g. `COM3`; Linux/macOS:
`/dev/ttyUSB0` or `/dev/tty.usbserial-*`), then:

```
kvmon serve --serial COM3
```

or set it in `config.toml`:

```toml
[transport]
type = "serial"
serial_port = "COM3"
serial_baud = 115200
```

## Line protocol

| Line                | Meaning                                            |
|---------------------|----------------------------------------------------|
| `S <mac> <name>`    | battery connected                                  |
| `F <mac> <hex>`     | one complete raw 121-byte frame, hex-encoded       |
| `D <mac>`           | battery disconnected                               |
| `# <text>`          | log line (ignored by the PC)                        |

Up to 3 batteries can be bridged at once (raise
`CONFIG_BT_NIMBLE_MAX_CONNECTIONS` in `platformio.ini` if your board allows
more). For larger banks, run one ESP32 per group, each on its own serial port,
and start one `kvmon` per port — or extend the firmware with the WiFi TCP
variant described in `docs/HARDWARE.md`.
