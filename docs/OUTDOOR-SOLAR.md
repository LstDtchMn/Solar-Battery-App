# Outdoor solar-powered cabin node

How to build the [Raspberry Pi cabin box](CABIN.md) as a **self-powered outdoor
unit** — its own small solar panel and battery — so it runs forever, unattended,
with no mains power and no internet.

> **The software side is already done.** Auto-start on boot, auto-reconnect to
> the batteries, the WiFi hotspot + captive portal, the stale watchdog, and fully
> offline operation are all built in (see [CABIN.md](CABIN.md)). This guide is
> only about **power and weatherproofing** — the hardware around the Pi.

---

## Do you even need its own solar?

You're already monitoring a big battery bank. Two ways to power the Pi:

| Option | How | Trade-off |
|---|---|---|
| **Tap the main bank** | A 12 V→5 V buck converter off the bank you monitor | Simplest. But the Pi is a small parasitic load on the pack it's measuring, and it dies when you disconnect the bank for service. |
| **Its own solar + battery** *(this guide)* | A tiny independent panel + LiFePO4 | Fully independent — survives bank servicing, doesn't skew the SoC reading, mount it anywhere. Best for a permanent unattended node. |

---

## Go headless outdoors

Nobody stands outside reading a screen — you check the pack on your **phone** over
the Pi's WiFi hotspot. Running **headless** (no touchscreen) roughly **halves the
power draw** and removes the most heat- and glare-sensitive part. Keep the
touchscreen for an *indoor* unit.

### Power budget (this drives all the sizing)

| Config | Draw | Daily energy |
|---|---|---|
| **Headless Pi 4** (recommended outdoors) | ~4 W | **~100 Wh/day** |
| Headless Pi Zero 2 W (ultra-low-power) | ~1.5 W | ~40 Wh/day |
| With always-on 7″ touchscreen | ~6.5 W | ~160 Wh/day |

Everything below is sized for the **headless Pi 4 at ~100 Wh/day** with **~3
cloudy-day autonomy**. Scale up if you add a screen or live somewhere with dark
winters (see [Sizing](#sizing-it-for-your-location)).

---

## System diagram

```
   ☀  Solar panel  ──▶  MPPT charge      ──┬──▶  12 V LiFePO4
      (50–100 W)        controller         │     (the Pi's own battery)
                        (LiFePO4 profile,   │
                         low-temp cutoff)   └──▶  12 V→5 V buck ──USB-C──▶  Raspberry Pi 4
                                                                            │  ├─ DS3231 RTC (I²C) — keeps time offline
                                                                            │  ├─ UPS HAT — clean shutdown on low battery
                                                                            │  └─ Bluetooth ~~▶ KiloVault bank
                                                              Pi WiFi hotspot ~~▶  📱 your phone
```

---

## Shopping list

Prices are rough US figures and vary a lot by brand and sales — treat them as
ballpark, not quotes. **Verify current prices before buying** (a live price check
for this guide was interrupted; the picks below are proven, widely-available
parts, but confirm the model and price at purchase).

### Compute + power conversion

| Part | Recommended pick | ~US price | Notes |
|---|---|---|---|
| **Computer** | Raspberry Pi 4 Model B (2 GB) | ~$45 | Best reliability running BLE **and** the WiFi hotspot at once, at low draw. Pi 5 works but draws more (size up the solar). Pi Zero 2 W (~$15, ~1.5 W) is the ultra-low-power option, but its single radio doing BLE + hotspot is less reliable. |
| **5 V supply** | 12 V→5 V buck, **≥5 A, wide input (8–22 V), USB-C out** (DROK / generic auto buck, or Pololu D24V50F5) | ~$10–15 | The part that prevents SD corruption. Give it headroom above the Pi's ~3 A and make sure it holds 5 V when the battery sags. Don't use a bargain car-USB adapter. |
| **Clock** | DS3231 RTC module | ~$5–8 | Keeps time with no internet. Wires to I²C (SDA/SCL/3V3/GND); needs a CR2032 coin cell. *(Skip if you use a PiJuice — it has an RTC built in.)* |

### Solar power

| Part | Recommended pick | ~US price | Notes |
|---|---|---|---|
| **Panel** | 50–100 W, 12 V **monocrystalline** (Renogy 100 W, Newpowa 100 W, or a 50 W) | ~$45–90 | 100 W gives a big winter/cloud margin for a ~4 W load — cheap insurance. Rigid mono lasts longest. |
| **Charge controller** | **MPPT, ~10 A, LiFePO4 profile** — Victron SmartSolar **75/10** (premium) or EPEVER **Tracer 1210AN** (budget) | ~$40–90 | MPPT beats PWM by ~20–30% (worth it in winter). For the **low-temp charge cutoff** you need a battery temperature reading: Victron via a **Smart Battery Sense** (~$30), EPEVER via its **RTS temp sensor** (~$10). |

### Battery + protection + enclosure

| Part | Recommended pick | ~US price | Notes |
|---|---|---|---|
| **Battery** | 12 V **LiFePO4, 20–30 Ah** (LiTime, Renogy) | ~$75–110 | ~3 cloudy-day autonomy at 100 Wh/day. Has its own BMS. |
| **Cold-climate battery** | Self-heating LiFePO4 (LiTime / Dakota Lithium) | ~$250+ | Mostly sold at 50–100 Ah, so pricey for a small node. For a small unit, the practical cold answer is usually **MPPT low-temp cutoff + insulation** instead (see below). |
| **Clean shutdown** | Pi **UPS HAT** — Geekworm X1200-series or Waveshare UPS HAT; **or PiJuice HAT** | ~$30–70 | Buffers power so the Pi shuts down gracefully when the 12 V battery hits cutoff (protects the SD/SSD). **PiJuice (~$70) bundles UPS + clean-shutdown + RTC in one board** — tidy, and replaces the DS3231. |
| **Enclosure** | IP65+ **ABS/polycarbonate** waterproof project box + **breather/Gore vent** | ~$20–35 | Must be **plastic, not metal**, or it blocks BLE + WiFi. The vent stops condensation. Size it for the Pi + buck + HAT + wiring. |
| **Storage (optional)** | Small **USB SSD** (Kingston A400 120 GB + USB-SATA adapter, or a portable SSD) | ~$20–30 | Far more durable than an SD card for 24/7 logging. Pi 4/5 boot from USB natively. |

### Two builds to start from

| | **Budget** (~$220–260) | **Bombproof** (~$380–420) |
|---|---|---|
| Computer | Pi 4 (2 GB) | Pi 4 (2 GB) |
| Charge controller | EPEVER Tracer 10 A + RTS sensor | Victron SmartSolar 75/10 + Smart Battery Sense |
| Panel | 50 W | 100 W |
| Battery | 12 V 20 Ah LiFePO4 | 12 V 30 Ah LiFePO4 |
| Shutdown + clock | DS3231 RTC + rely on watchdog | PiJuice HAT (UPS + shutdown + RTC) |
| Storage | good SD card | USB SSD |
| Cold weather | insulate the battery | self-heating battery or insulated box |

Both run the identical software. Start budget; add the bombproof pieces if the
site is harsh (deep cold, long dark winters, hard to reach).

> **Prices were not live-verified** for this guide (the price-check step was
> interrupted). They're realistic ballparks for well-known parts — confirm before
> ordering. I can pull a live, itemized cart with current prices if you want.

---

## Sizing it for your location

The numbers above assume ~100 Wh/day and modest winter sun. To adapt:

- **Battery** = daily Wh × days of autonomy ÷ 0.8 (don't drain LiFePO4 flat).
  100 Wh/day × 3 days ÷ 0.8 ≈ **375 Wh** → a 12 V ~30 Ah LiFePO4.
- **Solar panel** ≈ daily Wh ÷ (winter peak-sun-hours × 0.7 system efficiency).
  100 ÷ (3.5 × 0.7) ≈ **40 W** on paper — then **over-panel to 50–100 W**.
  Over-paneling is the cheapest insurance against clouds and short winter days;
  the MPPT just tapers off once the battery is full.
- **Peak-sun-hours** is the real variable. Sunny low latitude ≈ 5 h; far north
  in December ≈ 1–2 h. If your winters are dark, double the panel and battery
  rather than gamble — or plan to top the battery up occasionally.

Tell me your **latitude / climate** and I'll tighten these to your site.

---

## The four things that actually bite outdoor solar Pi builds

1. **Cold-weather charging.** LiFePO4 **must not charge below ~0 °C (32 °F)** or
   it's permanently damaged. Use an MPPT with a **low-temperature charge cutoff**,
   a **self-heating LiFePO4** battery, or keep the battery in an insulated box.
   Your monitor already warns on a cold pack via the low-temperature alarm — the
   same rule applies to the Pi's own battery.
2. **Timekeeping.** No internet means no NTP, so a plain Pi **resets its clock on
   every reboot** and your history/daily-summary timestamps drift. A **DS3231
   RTC** module (~$5) keeps time offline. Don't skip it.
3. **Brownout SD corruption.** The single most common failure. A **supercap/UPS
   HAT** that signals a **graceful shutdown** on low battery, plus the app's
   **hardware watchdog** (in [CABIN.md](CABIN.md#8-auto-reboot-if-it-ever-freezes-hardware-watchdog)),
   is what survives years unattended. Booting from a **USB SSD** instead of an SD
   card helps a lot too.
4. **RF-blocking enclosure.** A **metal** box kills BLE-to-the-batteries and
   WiFi-to-your-phone. Use **plastic / polycarbonate / fiberglass**, or run
   external antennas. Add a breather/Gore vent so condensation doesn't build up.

---

## Cold climates: read this

If it freezes where the box lives, you have three real options, best first:

1. **Self-heating LiFePO4 battery** — has an internal pad that warms the cells
   before charging. Simplest reliable answer for sub-freezing sites.
2. **MPPT with a low-temp cutoff + a battery temp sensor** — it simply won't
   charge when the battery is too cold (the battery still *discharges* fine cold,
   so the Pi keeps running; it just won't top up until it warms). Pair with a
   bigger battery to ride through cold snaps.
3. **Insulate the battery** in a foam box (it self-warms a little from its own
   charge/discharge). Cheapest, least reliable in deep cold.

The Pi itself is happy well below freezing; it's the **battery charging** that's
the constraint.

---

## Placement: put the sun and the batteries where they each want to be

You don't have to co-locate the Pi with the battery bank. If the best sun is
away from the batteries, put an **ESP32 BLE bridge** (see
[HARDWARE.md](HARDWARE.md)) right next to the KiloVault bank and run it to the Pi
over a longer USB/serial cable. The Pi + its solar can then live wherever the
sun is best, and the radio sits next to the pack for a solid Bluetooth link.

- Mount the **solar panel** facing the equator (south in the northern hemisphere),
  tilted roughly at your latitude, clear of shade.
- Keep the **Pi and battery out of direct sun** (heat) — shade or a vented,
  light-coloured enclosure.
- Keep the **hotspot antenna** unobstructed toward where you'll stand with your
  phone.

---

## What you are *not* building

The software is done. Once powered and weatherproofed, the box:

- boots straight into the monitor and reconnects to the batteries on its own,
- broadcasts its own WiFi and **auto-opens the dashboard** on your phone,
- logs history, raises alarms, and can drive a siren — all **offline**,
- reboots itself if the OS ever hangs (watchdog).

You only own the **power and the box**. See [CABIN.md](CABIN.md) for the software
install (one line: the Pi bootstrap installer).
