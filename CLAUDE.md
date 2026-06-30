# CLAUDE.md

Guidance for working in this repository.

## What this is

An offline monitor for **KiloVault HLX/HLX+** LiFePO4 batteries over Bluetooth
Low Energy. The vendor is defunct and its app is gone; this re-implements and
extends it. Target: a Windows PC (also Linux/macOS), optionally via an ESP32 BLE
bridge. **Must work with no internet.**

## Layout

- `kilovault/protocol.py` — pure BLE frame assembly/decode/encode. The linchpin;
  fully unit-tested against reference values. Change with care and keep
  `docs/PROTOCOL.md` in sync.
- `kilovault/transports/` — `ble.py` (bleak), `serial_bridge.py` (ESP32),
  `simulator.py` (no hardware). All implement `transports/base.py:Transport`.
- `kilovault/storage.py` — SQLite (history, device registry, events). Thread-safe.
- `kilovault/estimator.py` — derived metrics + bank aggregation (pure).
- `kilovault/alarms.py` — BMS-flag + threshold alarms, hysteresis, notifier.
- `kilovault/manager.py` — collector wiring transport→state→storage→alarms→SSE.
- `kilovault/server/` — dependency-free asyncio HTTP + SSE dashboard; assets in
  `server/static/` (vanilla JS, hand-rolled canvas charts — no CDNs).
- `kilovault/cli.py` — `kvmon` entry point.
- `firmware/esp32_bridge/` — NimBLE firmware forwarding raw frames over serial.

## Hard rules

- **No new runtime dependencies in the core.** The dashboard/simulator/logging/
  alarms must run on the Python 3.11 standard library alone. `bleak` and
  `pyserial` are optional, lazily imported in their transports only.
- **No external network calls, ever.** No CDNs/fonts/telemetry in the web assets.
- **Read-only to the BMS.** Only subscribe to notifications; never write to the pack.
- Keep `protocol.py` pure (no I/O) so it stays testable.

## Dev workflow

```bash
pip install pytest                 # tests need only pytest
python -m pytest -q                # 43 tests, ~0.2s
python -m kilovault.cli serve --simulate --open   # try the whole app, no hardware
```

When changing the protocol, validate the round-trip and the reference-frame test
in `tests/test_protocol.py` (ground-truth values come from the `ble_server.js`
mock that was validated against the real phone app).
