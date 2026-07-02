# Changelog

All notable changes to the KiloVault HLX+ Monitor.

## 1.2.0

Cabin-box release: a standalone Raspberry Pi touchscreen deployment, phone
access by QR code, and a broad security/robustness/data-correctness pass driven
by an adversarial bug hunt.

### Added
- **Raspberry Pi cabin box.** One-command installer (`deploy/install-pi.sh`,
  interactive or `--kiosk`/`--no-kiosk`), a systemd service (auto-start on boot,
  restart on failure), a full-screen Chromium kiosk that reads the port/token
  from config, waits for the server, disables screen blanking (X11 + Wayland),
  and relaunches the browser if it ever crashes. Convenience updater
  (`deploy/update.sh`). Full walkthrough in `docs/CABIN.md` (SD imaging, screen
  rotation, watchdog, SD-card longevity, siren alerts, troubleshooting).
- **View on your phone.** A 📱 Phone button shows a scannable QR code linking to
  the tokenized dashboard, so an iPhone can open it without typing a long URL.
- **Offline QR generator** (`kilovault/qrcode.py`): a dependency-free, pure
  standard-library QR encoder (byte mode, versions 1–10), validated bit-for-bit
  against the reference `segno` library.
- **Installable web app (PWA):** manifest, icons, and iOS meta tags so
  "Add to Home Screen" gives a full-screen app icon; a persistent access token
  keeps a phone's saved link working across restarts.

### Security
- Fixed an HTTP **response-header injection** in CSV export: the `address` query
  param (percent-decoded, so CRLF passed through) flowed unsanitized into the
  `Content-Disposition` filename. All header values are now CRLF-stripped and the
  filename is sanitized.
- Bounded request intake: a read timeout on the request line/headers/body and
  caps on header count/size (slow-loris / memory-exhaustion protection over the
  LAN).

### Fixed — robustness
- The collector no longer dies silently: `_on_sample` is fully guarded so one bad
  frame or transient error can't take the whole monitor offline (previously the
  simulator/serial paths were exposed).
- `set_transport` is serialized so two concurrent wizard submits can't leave an
  orphaned transport + collector running.
- The stale watchdog now detects a battery that connects but never sends data
  (`last_seen` is seeded at connect).
- Read-heavy history/summary/events queries run off the event loop so a large
  query can't freeze live SSE feeds.
- Export/diagnostics temp files are always cleaned up, even on error.

### Fixed — data correctness
- CRC-failed frames are counted for diagnostics but never integrated into the
  energy counters or displayed/persisted (a corrupt current no longer poisons
  the Ah/Wh totals or the bank min/max).
- Bank remaining-Ah is derived from the same override-aware capacity as the total
  and weighted SoC, so all three agree with a custom capacity.
- History downsampling: orders by row number (works with any column set), uses a
  correct ceiling step, keeps both chart endpoints, and no longer returns empty
  at small point counts.
- `min_cell_index`/`max_cell_index` report the physical cell number even when an
  early cell is dead.
- Impossible temperatures (e.g. a missing sensor decoding to ~-273 °C) are
  excluded from the bank min/max.
- Threshold overrides tolerate malformed stored JSON.

### Fixed — dashboard
- SSE reconnects after the stream closes (no more permanent "reconnecting…").
- The fallback poll refreshes the battery cards + selector so they can't freeze
  on stale values if the stream stalls.
- History/summary guard against out-of-order responses (wrong-battery data) and
  the resize handler is debounced.
- Rename / threshold saves handle failures instead of failing silently.
- Assorted NaN/undefined and double-escaping fixes in the cards, SoC ring gauge,
  and diagnostics.

## 1.1.0

- Persistent, resettable energy counters (Ah/Wh in/out).
- Per-battery alarm thresholds.
- Daily/period summaries.
- Physical hardware alerting (siren/light) via USB relay, GPIO, or command.
- Hardening: CI tests, config validation, stale watchdog, history downsampling.

## 1.0.x

- Initial release: offline BLE monitor for KiloVault HLX/HLX+ with dashboard,
  history, alarms, setup wizard, diagnostics, ESP32 bridge, and a one-file
  Windows build.
