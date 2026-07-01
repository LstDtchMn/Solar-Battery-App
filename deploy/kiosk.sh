#!/usr/bin/env bash
# KiloVault cabin kiosk: open the dashboard full-screen on the Pi's touchscreen.
#
# It reads the port + token straight out of config.toml, waits for the monitor
# service to come up, disables screen-blanking, and launches Chromium in kiosk
# mode. Run by deploy/kilovault-kiosk.desktop (autostart) or by hand for testing.
set -u

CONFIG="${KV_CONFIG:-/home/pi/kilovault/config.toml}"

# --- Read port + token from the TOML using Python's own parser (no jq/awk) ----
read_cfg() {
  python3 - "$CONFIG" "$1" "$2" <<'PY'
import sys
try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None
path, section, key = sys.argv[1], sys.argv[2], sys.argv[3]
val = ""
if tomllib:
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        val = data.get(section, {}).get(key, "")
    except Exception:
        val = ""
print(val if val is not None else "")
PY
}

PORT="$(read_cfg web port)"
TOKEN="$(read_cfg web token)"
[ -z "$PORT" ] && PORT=8765

# If no fixed token is set in config, the server persists an auto-generated one
# in the data dir. Fall back to that so the kiosk still authenticates.
if [ -z "$TOKEN" ]; then
  DATA_DIR="$(read_cfg app data_dir)"
  [ -z "$DATA_DIR" ] && DATA_DIR="/home/pi/kilovault"
  if [ -f "$DATA_DIR/.web_token" ]; then
    TOKEN="$(tr -d '[:space:]' < "$DATA_DIR/.web_token")"
  fi
fi

QS=""
[ -n "$TOKEN" ] && QS="?token=${TOKEN}"
URL="http://localhost:${PORT}/${QS}"

# --- Wait for the monitor to answer before we open the browser ----------------
echo "kiosk: waiting for the monitor on port ${PORT}…"
for _ in $(seq 1 60); do
  if curl -fsS "http://localhost:${PORT}/api/snapshot${QS}" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

# --- Keep the screen awake (best-effort; works on X11, harmless elsewhere) -----
if [ -n "${DISPLAY:-}" ] && command -v xset >/dev/null 2>&1; then
  xset s off || true
  xset -dpms || true
  xset s noblank || true
fi
# Hide the mouse cursor on an idle touchscreen if unclutter is present.
if [ -n "${DISPLAY:-}" ] && command -v unclutter >/dev/null 2>&1; then
  unclutter -idle 1 -root &
fi

# --- Find Chromium ------------------------------------------------------------
BROWSER=""
for cand in chromium-browser chromium chromium-browser-privacy; do
  if command -v "$cand" >/dev/null 2>&1; then BROWSER="$cand"; break; fi
done
if [ -z "$BROWSER" ]; then
  echo "kiosk: Chromium not found. Install it with:  sudo apt install -y chromium-browser" >&2
  exit 1
fi

# A clean, throwaway profile so a wedged tab never bricks the kiosk.
PROFILE="${HOME}/.kilovault-kiosk"
mkdir -p "$PROFILE"

echo "kiosk: launching ${BROWSER} → ${URL}"
exec "$BROWSER" \
  --kiosk "$URL" \
  --user-data-dir="$PROFILE" \
  --start-fullscreen \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-features=Translate,TranslateUI \
  --check-for-update-interval=31536000 \
  --overscroll-history-navigation=0 \
  --autoplay-policy=no-user-gesture-required \
  --touch-events=enabled \
  --password-store=basic \
  --disable-pinch
