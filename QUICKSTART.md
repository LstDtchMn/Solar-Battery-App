# Quick Start — KiloVault HLX+ Monitor

A friendly, no-jargon guide to seeing your batteries on screen. Two ways to do it
— pick **A** (easiest) or **B**.

---

## A. The easy way (Windows, no Python) ⭐

1. **Download** `KiloVaultMonitor.exe` (from the project's Releases page, or build
   it once with the instructions in `docs/USAGE.md`).
2. **Double-click** it.
   - Windows may show a blue "Windows protected your PC" box for new programs.
     Click **More info → Run anyway**. (It's safe; it's just not signed.)
3. A black window opens and your web browser pops up with the dashboard.
   - **Keep the black window open** while you're monitoring. Closing it stops the app.
4. The **Setup Wizard** appears the first time. Follow it (see below).

That's it. Your data and a log file are saved in a folder called
**"KiloVault Monitor"** inside your user folder.

---

## B. From source (Windows / Mac / Linux)

1. Install **Python 3.11+** from <https://www.python.org/downloads/>.
   On Windows, tick **"Add Python to PATH"** during install.
2. In the project folder:
   - Windows: double-click **`windows/Install (run me first).bat`**, then
     **`windows/Start KiloVault Monitor.bat`**.
   - Mac/Linux:
     ```bash
     pip install bleak pyserial
     python -m kilovault.cli serve --open
     ```
3. Your browser opens the dashboard.

**Want to try it with no batteries at all?** Add `--simulate`:
```bash
python -m kilovault.cli serve --simulate --open
```

---

## The Setup Wizard (first run)

A little window walks you through three choices:

| Choice | When to pick it |
|---|---|
| 📶 **This PC's Bluetooth** | Your PC is near the batteries and has Bluetooth. |
| 🔌 **ESP32 USB adapter** | Your PC has no Bluetooth, or the batteries are far away. |
| 🧪 **Just show me a demo** | You want to see how it looks, with pretend batteries. |

The wizard checks everything is ready and tells you in plain language if something
is missing. You can reopen it any time with the **⚙ Setup** button (top right).

> **Important:** KiloVault batteries **go to sleep when idle**. If nothing shows
> up, apply a load (turn something on) or a charger to wake them, then wait a few
> seconds.

---

## Reading the dashboard

- The big numbers at the top are your **whole battery bank** at a glance.
- Each card below is **one battery**: how full it is (SoC), voltage, current
  (green = charging, orange = in use), temperature, and the 4 cells inside.
- See a little **ⓘ** next to a label? **Hover or tap it** for a plain-language
  explanation. The **? Help** button (top right) has a full glossary.
- **History** tab: charts over time. **Events** tab: a log of any alarms.

### View it on your phone (same Wi-Fi)

Start it so other devices can reach it:
```bash
python -m kilovault.cli serve --lan
```
Then on your phone's browser, go to `http://<your-PC's-IP>:8765`.

---

## If something's not working

1. Open the **Diagnostics** tab.
2. Click **Run Bluetooth test** — it tells you if Bluetooth is working and what
   it can see.
3. Read **Common problems & fixes** there.
4. Still stuck? Click **Download diagnostics (.zip)** and email it for help. The
   file contains the log, your settings and system info — and **no passwords**.

The most common fix: **wake the batteries** (apply a load or charger) and make
sure the PC (or ESP32) is **within Bluetooth range**.
