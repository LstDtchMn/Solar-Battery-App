# Usage & configuration

## Installing

```bash
# core only (simulator, dashboard, logging, alarms) — no pip packages needed
python -m kilovault.cli serve --simulate

# with real Bluetooth hardware
pip install bleak

# with an ESP32 serial bridge
pip install pyserial

# everything + tests, installed as the `kvmon` command
pip install -e ".[all,dev]"
```

Python 3.11 or newer is required (the config loader uses the stdlib `tomllib`).

## Commands

| Command | What it does |
|---|---|
| `kvmon serve` | Run the web dashboard and start logging. |
| `kvmon serve --simulate` | Same, with two fake batteries (no hardware). |
| `kvmon serve --serial COM3` | Read from an ESP32 bridge on a serial port. |
| `kvmon serve --host 0.0.0.0` | Make the dashboard reachable across the LAN. |
| `kvmon scan` | List nearby HLX+ batteries (BLE). |
| `kvmon monitor` | Headless console monitor (no web UI). |
| `kvmon export out.csv` | Export logged history to CSV. |
| `kvmon init-config` | Write a documented `config.toml`. |

Run any command with `python -m kilovault.cli <cmd>` if you haven't `pip install`ed.

## The dashboard

- **Live** — the whole bank at a glance plus a card per battery: SoC ring,
  voltage, current (green = charging, orange = discharging), power, temperature,
  cycles, per-cell bars (highest cell green, lowest orange), cell-Δ, State of
  Health, time-to-full/empty, signal strength and firmware. Click the ✎ to
  rename a battery.
- **History** — pick a battery and a time window and view voltage, current, SoC,
  temperature and cell-imbalance charts from the local database. **Export CSV**
  downloads the selected window.
- **Events** — the alarm log (raised/cleared timestamps). The original app
  never saved these.

The dashboard is plain HTML/CSS/JS served locally with no external assets, so it
works with no internet and is safe to expose only on your LAN.

## Configuration (`config.toml`)

Generate it with `kvmon init-config`, then edit. All keys are optional.

```toml
[app]
log_interval = 10           # seconds between rows saved to history

[transport]
type = "ble"                # "ble" | "serial" | "simulator"
scan_timeout = 8.0
# addresses = ["AA:BB:CC:DD:EE:FF"]   # pin to specific batteries
reconnect_seconds = 5.0
# serial_port = "COM3"      # when type = "serial"

[web]
host = "127.0.0.1"          # "0.0.0.0" to allow phones/tablets on the LAN
port = 8765
open_browser = false

[alarms]
enabled = true
cell_delta_warn = 0.30      # V — the manual says keep cells within 300 mV
cell_delta_critical = 0.40
temp_high = 45.0
temp_low = 2.0
soc_low = 15.0
soc_critical = 8.0
voltage_high = 14.6
voltage_low = 11.5
notify_desktop = true
sound = true
```

Friendly names and per-battery capacities are **not** in the config file — they
are edited from the dashboard and stored in the database, so they survive
restarts and aren't overwritten by what the battery advertises.

## Recommended thresholds (from the manual)

The KiloVault manual specifies, per HLX+ in a single string:

- Absorb / Bulk voltage **14.1 V**, float (if forced) **≈13.4–13.6 V**.
- Re-bulk around **12.75 V** (~80% depth of discharge).
- Keep cells within **300 mV** of each other (the `cell_delta_warn` default).
- Avoid charging near/below **0 °C** — the BMS may shut the pack down. The
  `temp_low` alarm warns you before that happens.

## Running unattended

For an always-on cabin PC, run `kvmon serve --host 0.0.0.0` at boot:

- **Windows**: Task Scheduler → "At log on" → `pythonw -m kilovault.cli serve --host 0.0.0.0`.
- **Linux**: a small systemd unit running the same command.

The collector auto-reconnects to each battery, so it survives the batteries
sleeping and waking.

## Packaging a Windows .exe  <a id="packaging"></a>

```bat
pip install pyinstaller bleak pyserial
pyinstaller --onefile --name KiloVaultMonitor ^
    --add-data "kilovault/server/static;kilovault/server/static" ^
    run.py
```

The result is a single `KiloVaultMonitor.exe`. Run e.g.
`KiloVaultMonitor.exe serve --open`. (On Linux/macOS use `:` instead of `;` in
`--add-data`.)

## Troubleshooting

- **`scan` finds nothing** — the batteries sleep when idle. Apply a load or
  charger to wake them, make sure the PC's Bluetooth is on, and stay within
  range. On Linux, BLE scanning may need to run as root or with the right
  capabilities on `bluetoothd`.
- **Connects but no data** — confirm it's an HLX/HLX+ (service `FFE0`). Other
  KiloVault models use different protocols.
- **Values look slightly off vs a meter** — the manual notes up to ~0.3 V
  difference mid-cycle between the BMS reading and the terminals; they converge
  at the ends of the range.
- **Dashboard not reachable from a phone** — start with `--host 0.0.0.0` and
  open the PC's firewall for the chosen port.
