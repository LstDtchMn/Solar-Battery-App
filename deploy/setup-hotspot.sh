#!/usr/bin/env bash
# Turn the Raspberry Pi into its own Wi-Fi network (access point) so a phone can
# connect directly to the monitor in a cabin with no router and no internet.
#
# The phone joins the Pi's Wi-Fi (SSID below), then opens the dashboard at the
# Pi's address (10.42.0.1 by default). Uses NetworkManager's built-in AP +
# DHCP (no hostapd/dnsmasq to configure). Raspberry Pi OS Bookworm uses
# NetworkManager by default.
#
#   sudo bash setup-hotspot.sh                       # defaults
#   sudo bash setup-hotspot.sh "MyCabin" "mypassword"
#
set -euo pipefail

SSID="${1:-KiloVault-Cabin}"
PASSWORD="${2:-}"
IFACE="${KV_WIFI_IFACE:-wlan0}"
AP_IP="${KV_AP_IP:-10.42.0.1}"
CON="kilovault-hotspot"
CONFIG="${KV_CONFIG:-/home/pi/kilovault/config.toml}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo:  sudo bash setup-hotspot.sh" >&2
  exit 1
fi

if ! command -v nmcli >/dev/null 2>&1; then
  echo "NetworkManager (nmcli) is required but not found." >&2
  echo "On Raspberry Pi OS Bookworm it's the default. Install with:" >&2
  echo "  sudo apt install -y network-manager" >&2
  exit 1
fi

# WPA2 needs an 8+ character password; generate a friendly one if none given.
if [ -z "$PASSWORD" ]; then
  PASSWORD="cabin$(tr -dc 0-9 </dev/urandom | head -c 4)"
  echo "No password given — generated one: $PASSWORD"
fi
if [ "${#PASSWORD}" -lt 8 ]; then
  echo "Wi-Fi password must be at least 8 characters." >&2
  exit 1
fi

# AP mode won't start without a regulatory country set. Warn if it's missing.
COUNTRY="$(raspi-config nonint get_wifi_country 2>/dev/null || true)"
if [ -z "$COUNTRY" ]; then
  echo "!! Wi-Fi country is not set. The access point may not start."
  echo "   Set it once with:  sudo raspi-config  →  Localisation  →  WLAN Country"
  echo "   (or: sudo raspi-config nonint do_wifi_country US   — use your own code)"
fi

echo "--> Creating Wi-Fi hotspot '$SSID' on $IFACE (AP IP $AP_IP)…"
# Replace any previous run cleanly.
nmcli connection delete "$CON" >/dev/null 2>&1 || true

nmcli connection add type wifi ifname "$IFACE" con-name "$CON" \
  autoconnect yes ssid "$SSID"
nmcli connection modify "$CON" \
  802-11-wireless.mode ap \
  802-11-wireless.band bg \
  ipv4.method shared \
  ipv4.addresses "$AP_IP/24" \
  wifi-sec.key-mgmt wpa-psk \
  wifi-sec.psk "$PASSWORD"

nmcli connection up "$CON"

# Point the monitor at the hotspot address so the phone URL + QR are correct.
if [ -f "$CONFIG" ]; then
  echo "--> Updating $CONFIG (host + advertised_host)…"
  # bind to all interfaces so the phone can reach it
  if grep -qE '^[[:space:]]*host[[:space:]]*=' "$CONFIG"; then
    sed -i 's#^[[:space:]]*host[[:space:]]*=.*#host = "0.0.0.0"#' "$CONFIG"
  fi
  # advertise the AP IP in the phone URL/QR
  if grep -qE '^[[:space:]]*advertised_host[[:space:]]*=' "$CONFIG"; then
    sed -i "s#^[[:space:]]*advertised_host[[:space:]]*=.*#advertised_host = \"$AP_IP\"#" "$CONFIG"
  elif grep -qE '^[[:space:]]*\[web\]' "$CONFIG"; then
    sed -i "/^[[:space:]]*\[web\]/a advertised_host = \"$AP_IP\"" "$CONFIG"
  fi
  systemctl restart kilovault 2>/dev/null || true
else
  echo "!! $CONFIG not found — run deploy/install-pi.sh first, then re-run this."
fi

echo
echo "=================================================================="
echo "  ✅ Wi-Fi hotspot is up."
echo
echo "  On your phone, join this Wi-Fi network:"
echo "    Name (SSID):  $SSID"
echo "    Password:     $PASSWORD"
echo
echo "  Then open the dashboard (or tap the 📱 Phone button to scan a QR):"
echo "    http://$AP_IP:8765/"
echo
echo "  The hotspot starts automatically on every boot."
echo "  To remove it later:  sudo nmcli connection delete $CON"
echo "=================================================================="
