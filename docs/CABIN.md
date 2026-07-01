# Cabin Box: a standalone KiloVault monitor with a touchscreen

This guide turns a **Raspberry Pi** into a always-on, low-power appliance that
sits by your batteries, shows a full-screen dashboard on a small touchscreen,
and lets you check the pack from your **iPhone** over the cabin Wi‑Fi — all with
**no internet**.

You do this once. After that the box just runs: plug it in, it boots straight
into the dashboard, and reconnects to the batteries on its own.

```
   ┌─────────────┐   Bluetooth    ┌──────────────────────┐
   │ KiloVault    │ ~~~~~~~~~~~~~> │  Raspberry Pi         │
   │ HLX+ bank    │               │  • runs 24/7 (5 W)    │
   └─────────────┘               │  • touchscreen kiosk  │
                                  │  • cabin Wi‑Fi        │
                                  └──────────┬───────────┘
                                             │  Wi‑Fi (no internet needed)
                                             v
                                   📱 iPhone “Add to Home Screen”
```

---

## 1. What to buy

| Part | Notes |
|------|-------|
| **Raspberry Pi 4** (2 GB is plenty) or **Pi 5** / **Pi 3B+** | Pi 4/5 recommended. All have built‑in Bluetooth + Wi‑Fi. |
| **Official Pi power supply** | Use the real one — undervoltage causes weird crashes. |
| **microSD card, 32 GB, decent brand** | SanDisk/Samsung. A cheap card is the #1 cause of trouble. |
| **Small touchscreen** | The official **Raspberry Pi 7″ touchscreen**, or any HDMI mini‑monitor. Optional — you can run headless and only use your phone. |
| **Case** | Anything that fits the Pi + screen. |

If your batteries are more than ~10 m / a wall away from where the Pi sits,
Bluetooth may be flaky. In that case put an **ESP32 BLE bridge** next to the
batteries and wire/USB it to the Pi (see [§12](#12-esp32-bridge-if-bluetooth-wont-reach)).

---

## 2. Flash the SD card

1. On any computer, install **Raspberry Pi Imager** from raspberrypi.com.
2. Insert the microSD card.
3. In Imager:
   - **Device:** your Pi model.
   - **Operating System:** *Raspberry Pi OS (64‑bit)* — the full version *with
     desktop* (the kiosk needs a desktop).
   - **Storage:** your SD card.
4. Click the **gear / “Edit Settings”** and set, before writing:
   - **Hostname:** `cabin` (so you can reach it at `cabin.local`).
   - **Username:** keep **`pi`** (these scripts assume it) and set a password.
   - **Wi‑Fi:** your cabin router’s name + password.
   - **Enable SSH** (under Services) — lets you set it up from your laptop.
5. Write the card, then put it in the Pi and power on.

The very first boot takes a couple of minutes.

---

## 3. Install the monitor (one command)

You need a terminal on the Pi. Two ways:

- **From your laptop over Wi‑Fi (easiest):** open Terminal / PowerShell and run
  `ssh pi@cabin.local` (use the password you set).
- **Directly on the Pi:** plug in a keyboard, open the **Terminal** app.

Then copy‑paste these lines (they need the internet **only for this one‑time
install** — do it at home or wherever you have a connection; the box never needs
internet again afterward):

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/LstDtchMn/Solar-Battery-App.git ~/Solar-Battery-App
cd ~/Solar-Battery-App
sudo bash deploy/install-pi.sh
```

The installer will:

- install Python + Bluetooth support,
- install the monitor and enable it as a **service** that starts on every boot
  and restarts itself if it ever stops,
- create `~/kilovault/config.toml` with a **private access token** (random, so
  neighbours on the Wi‑Fi can’t read your pack),
- offer to set up the **touchscreen kiosk** — answer **`y`** if this Pi has the
  screen attached.

When it finishes it prints two links — one for this Pi, one for your phone.
**Take a photo of that screen**, or copy the phone link somewhere.

> Re‑running the installer is safe. It never overwrites your `config.toml`, so
> your token and settings stick.

---

## 4. Wake the batteries and confirm

KiloVault batteries **sleep when idle** and go silent on Bluetooth. Apply a load
or a charger (turn something on) so they wake up.

Check it’s working:

```bash
systemctl status kilovault      # should say "active (running)"
journalctl -u kilovault -f      # live log — Ctrl+C to stop watching
```

Within a minute or two you should see `Connected to …` lines. If not, jump to
[Troubleshooting](#13-troubleshooting).

---

## 5. The touchscreen kiosk

If you answered **`y`** to the kiosk question, **reboot** (`sudo reboot`). The Pi
boots into the desktop and then straight into the full‑screen dashboard — no
mouse, no menus, just the battery display. Touch works for the tabs (Live /
History / Events / Diagnostics).

To turn a normal Pi desktop into a kiosk later, or to test it by hand:

```bash
~/Solar-Battery-App/deploy/kiosk.sh
```

Press **Alt+F4** (with a keyboard) to leave the kiosk during setup.

---

## 6. View it on your iPhone

1. Make sure your iPhone is on the **same Wi‑Fi** as the Pi (your cabin router —
   no internet required, they just need to be on the same network).
2. Open **Safari** and go to the **phone link** the installer printed. It looks
   like:

   ```
   http://cabin.local:8765/?token=xxxxxxxx
   ```

   If `cabin.local` doesn’t load, use the numeric address instead
   (`http://192.168.x.x:8765/?token=…` — the installer printed the exact one).
3. Tap the **Share** button (the square with an ↑) → **Add to Home Screen**.

Now you have a **KiloVault app icon** on your phone. It opens full‑screen, and
because the token is saved in the link, it keeps working across reboots — you
won’t have to type anything again.

> **Why the token?** Binding to the whole Wi‑Fi means any device on it could
> otherwise read your data. The token in the link is the password. Keep the link
> to yourself; anyone you send it to can view the pack.

To find the link again later, on the Pi run:

```bash
grep -E 'token|port' ~/kilovault/config.toml
hostname -I          # the Pi's IP address
```

---

## 7. Make the SD card last

An SD card that’s written to constantly will eventually wear out. The cabin
config already softens this (`log_interval = 30`, so it saves a row every 30 s
instead of every 10 s). Two more easy wins:

- **Buy a good card** (see the shopping list). This matters more than anything.
- **Back up your history** occasionally by copying `~/kilovault/kilovault_history.db`
  to a USB stick, or use the dashboard’s **Export CSV** button.

For a truly set‑and‑forget box you can boot the Pi from a **USB SSD** instead of
an SD card — far more durable. That’s optional and beyond this guide.

---

## 8. Auto‑reboot if it ever freezes (hardware watchdog)

The Pi has a built‑in watchdog that reboots it if the whole system locks up
(rare, but nice insurance for an unattended cabin). Enable it:

```bash
echo 'dtparam=watchdog=on' | sudo tee -a /boot/firmware/config.txt
sudo apt install -y watchdog
sudo systemctl enable --now watchdog
sudo reboot
```

The monitor **service** already restarts itself if just the app stops
([`Restart=always`](../deploy/kilovault.service)); the watchdog covers the case
where the entire OS hangs.

---

## 9. Power: it just comes back

There’s nothing to do here — it’s the point of the appliance. On power loss and
restore (a common thing at an off‑grid cabin), the Pi boots, the service starts,
the kiosk opens, and Bluetooth reconnects. No login, no clicking.

Because it runs off the same 12 V system it’s monitoring, power the Pi from a
good **12 V→5 V USB buck converter** rated for your Pi (3 A for a Pi 4/5). Don’t
use a bargain car‑USB adapter; undervoltage is the top cause of SD corruption.

---

## 10. Optional: siren or light on a critical alarm

The box can shout when something’s actually wrong (pack too cold to charge, cell
imbalance, very low state of charge). Edit `~/kilovault/config.toml`, in the
`[hardware]` section, and pick **one**:

```toml
[hardware]
alert_on = "critical"          # or "any" for warnings too, "none" to disable

# A) A USB relay board (cheapest, no soldering):
serial_relay_port = "/dev/ttyUSB1"
serial_relay_on   = "A0 01 01 A2"   # bytes to close the relay (check your board)
serial_relay_off  = "A0 01 00 A1"

# B) …or drive a relay straight off the Pi's GPIO header:
# gpio_pin = 17
```

Then `sudo systemctl restart kilovault`. Full wiring notes are in
[docs/HARDWARE.md](HARDWARE.md). The monitor is **read‑only to the batteries** —
it never writes to the pack; the relay just switches your own siren/light.

---

## 11. Updating later

When there’s a new version, and you have internet again:

```bash
cd ~/Solar-Battery-App
git pull
sudo bash deploy/install-pi.sh      # keeps your config; updates the code
sudo systemctl restart kilovault
```

---

## 12. ESP32 bridge (if Bluetooth won’t reach)

If the Pi is too far from the bank for reliable Bluetooth, flash the included
**ESP32 firmware** (`firmware/esp32_bridge/`), place the ESP32 next to the
batteries, and connect it to the Pi by USB. Then in `~/kilovault/config.toml`:

```toml
[transport]
type = "serial"
serial_port = "/dev/ttyUSB0"
```

`sudo systemctl restart kilovault`. Everything else — dashboard, phone, kiosk —
is identical.

---

## 13. Troubleshooting

**The dashboard won’t load on my phone.**
- Phone and Pi on the *same* Wi‑Fi? (Not cellular, not a guest network.)
- Try the numeric IP link instead of `cabin.local`.
- Is the service up? `systemctl status kilovault`.

**It loads but says “No batteries connected.”**
- Wake the batteries: turn on a load or charger. They sleep when idle.
- Check the log: `journalctl -u kilovault -f`.
- Run the built‑in test from the **Diagnostics** tab → **Run Bluetooth test**.

**Bluetooth scan fails / permission denied.**
- The installer adds `pi` to the `bluetooth` group. Log out/in or reboot once.
- `sudo systemctl restart bluetooth` then restart the monitor.

**The kiosk screen is blank or shows a browser error.**
- Give the service a few seconds after boot; the kiosk waits up to 2 minutes.
- Run `~/Solar-Battery-App/deploy/kiosk.sh` from a terminal to see the error.
- Make sure you installed *Raspberry Pi OS with desktop*, not Lite.

**I need to send you a report.**
- On the Diagnostics tab tap **Download diagnostics (.zip)** and email it. It
  contains logs and settings but **no passwords**.

**Where’s everything?**
- Config: `~/kilovault/config.toml`
- History database + log: `~/kilovault/`
- Service definition: `/etc/systemd/system/kilovault.service`
- Handy commands: `systemctl {status,restart,stop} kilovault`,
  `journalctl -u kilovault -f`

---

## What the installer set up (for the curious)

- **`~/kilovault/config.toml`** — your settings; a copy of
  [`deploy/config.example.toml`](../deploy/config.example.toml) with a generated
  token and your paths.
- **`kilovault.service`** — a systemd service running
  `python3 -m kilovault.cli -c ~/kilovault/config.toml serve`, set to start on
  boot and restart on failure. See [`deploy/kilovault.service`](../deploy/kilovault.service).
- **Kiosk autostart** — a `.desktop` file in `~/.config/autostart/` that runs
  [`deploy/kiosk.sh`](../deploy/kiosk.sh), which reads the port + token from your
  config, waits for the server, and opens Chromium full‑screen.

Everything runs on the Python **standard library** plus `bleak` for Bluetooth.
No internet, no cloud, no accounts.
