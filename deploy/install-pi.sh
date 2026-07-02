#!/usr/bin/env bash
# One-shot installer for the KiloVault cabin box (Raspberry Pi 3/4/5).
#
# It installs the monitor as an always-on service, drops in a cabin config with
# a freshly generated access token, and (optionally) sets up the touchscreen
# kiosk. Safe to re-run: it never overwrites an existing config.toml.
#
#   curl -O .../install-pi.sh   # or clone the repo
#   sudo bash install-pi.sh                 # interactive
#   sudo bash install-pi.sh --kiosk         # unattended, set up the kiosk too
#   sudo bash install-pi.sh --no-kiosk      # unattended, service only
#
set -euo pipefail

# --- Options (support unattended installs / re-runs) --------------------------
KIOSK_CHOICE=""          # "y" / "n" forces non-interactive; "" prompts
for arg in "$@"; do
  case "$arg" in
    --kiosk)    KIOSK_CHOICE="y" ;;
    --no-kiosk) KIOSK_CHOICE="n" ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//; 1d'; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

# --- Resolve who we're installing for ----------------------------------------
# When run under sudo, install for the login user (usually "pi"), not root.
KV_USER="${SUDO_USER:-$(id -un)}"
if [ "$KV_USER" = "root" ]; then
  KV_USER="pi"
  echo "Note: no SUDO_USER; defaulting the service user to 'pi'."
fi
KV_HOME="$(getent passwd "$KV_USER" | cut -d: -f6)"
[ -z "$KV_HOME" ] && KV_HOME="/home/$KV_USER"
DATA_DIR="$KV_HOME/kilovault"
CONFIG="$DATA_DIR/config.toml"

# Where this script (and the repo) live, so we can copy deploy assets.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=================================================================="
echo "  KiloVault HLX+ Monitor — cabin box installer"
echo "  User:        $KV_USER"
echo "  Data folder: $DATA_DIR"
echo "  Source repo: $REPO_DIR"
echo "=================================================================="

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo:  sudo bash install-pi.sh" >&2
  exit 1
fi

# --- System packages ----------------------------------------------------------
echo "--> Installing system packages (Python, Bluetooth, pip)…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
  python3 python3-pip python3-venv bluez curl

# --- Python package ------------------------------------------------------------
echo "--> Installing the monitor and its Bluetooth support…"
# --break-system-packages: Bookworm marks the system env externally-managed;
# this is a dedicated appliance, so a global install is fine and simplest.
PIP_FLAGS="--break-system-packages"
if ! pip3 install $PIP_FLAGS --help >/dev/null 2>&1; then PIP_FLAGS=""; fi
pip3 install $PIP_FLAGS "$REPO_DIR"        # installs the kilovault package
pip3 install $PIP_FLAGS bleak              # BLE transport (optional dependency)

# --- Data dir + config --------------------------------------------------------
echo "--> Setting up $DATA_DIR…"
install -d -o "$KV_USER" -g "$KV_USER" "$DATA_DIR"

if [ -f "$CONFIG" ]; then
  echo "    config.toml already exists — leaving it untouched."
else
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
  sed \
    -e "s#/home/pi/kilovault#$DATA_DIR#g" \
    -e "s#CHANGE-ME-cabin#$TOKEN#g" \
    "$SCRIPT_DIR/config.example.toml" > "$CONFIG"
  chown "$KV_USER:$KV_USER" "$CONFIG"
  echo "    Wrote $CONFIG with a fresh access token."
fi

# --- Bluetooth + hardware group access ---------------------------------------
# BLE scanning wants the bluetooth group; relay/GPIO alerts want dialout/gpio.
for grp in bluetooth dialout gpio; do
  if getent group "$grp" >/dev/null 2>&1; then
    usermod -aG "$grp" "$KV_USER" || true
  fi
done

# --- systemd service ----------------------------------------------------------
echo "--> Installing the kilovault service…"
sed \
  -e "s#^User=pi#User=$KV_USER#" \
  -e "s#^Group=pi#Group=$KV_USER#" \
  -e "s#/home/pi/kilovault#$DATA_DIR#g" \
  "$SCRIPT_DIR/kilovault.service" > /etc/systemd/system/kilovault.service
systemctl daemon-reload
systemctl enable kilovault.service
systemctl restart kilovault.service

# --- Optional: touchscreen kiosk ---------------------------------------------
if [ -n "$KIOSK_CHOICE" ]; then
  KIOSK="$KIOSK_CHOICE"
else
  echo
  read -r -p "Set up the full-screen touchscreen kiosk on this Pi? [y/N] " KIOSK || KIOSK="n"
fi
if [ "${KIOSK,,}" = "y" ]; then
  echo "--> Installing Chromium + kiosk autostart…"
  apt-get install -y --no-install-recommends chromium-browser unclutter x11-xserver-utils \
    || apt-get install -y --no-install-recommends chromium unclutter x11-xserver-utils || true
  chmod +x "$SCRIPT_DIR/kiosk.sh"
  AUTOSTART="$KV_HOME/.config/autostart"
  install -d -o "$KV_USER" -g "$KV_USER" "$AUTOSTART"
  # Point the kiosk at the real config + kiosk.sh location.
  sed "s#^Exec=.*#Exec=env KV_CONFIG=$CONFIG $SCRIPT_DIR/kiosk.sh#" \
    "$SCRIPT_DIR/kilovault-kiosk.desktop" > "$AUTOSTART/kilovault-kiosk.desktop"
  chown "$KV_USER:$KV_USER" "$AUTOSTART/kilovault-kiosk.desktop"
  # Stop the screen from blanking. raspi-config's switch is the one reliable
  # way that works under both X11 and Wayland (labwc/wayfire) on Pi OS.
  if command -v raspi-config >/dev/null 2>&1; then
    raspi-config nonint do_blanking 1 || true
  fi
  echo "    Kiosk will start on the next desktop login. Reboot to try it."
fi

# --- Done: print the URLs -----------------------------------------------------
PORT="$(python3 -c "
try:
    import tomllib
    print(tomllib.load(open('$CONFIG','rb')).get('web',{}).get('port',8765))
except Exception:
    print(8765)
")"
TOKEN="$(python3 -c "
try:
    import tomllib
    print(tomllib.load(open('$CONFIG','rb')).get('web',{}).get('token',''))
except Exception:
    print('')
")"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
QS=""; [ -n "$TOKEN" ] && QS="?token=$TOKEN"

echo
echo "=================================================================="
echo "  ✅ Installed. The monitor is running as a service."
echo
echo "  On this Pi:        http://localhost:$PORT/$QS"
echo "  On your iPhone:    http://$IP:$PORT/$QS"
echo "    (same Wi-Fi; in Safari tap Share → Add to Home Screen)"
echo
echo "  Service control:"
echo "    systemctl status kilovault      # is it running?"
echo "    journalctl -u kilovault -f      # live log"
echo "    systemctl restart kilovault     # restart it"
echo
echo "  Config + data:     $DATA_DIR"
echo "=================================================================="
