#!/usr/bin/env bash
# Update the cabin box to the latest code (needs internet for this one step).
# Pulls the repo, reinstalls the package, and restarts the service. Your
# config.toml and history are left untouched.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo:  sudo bash update.sh" >&2
  exit 1
fi

echo "--> Pulling latest code…"
git -C "$REPO_DIR" pull --ff-only

echo "--> Reinstalling the package…"
PIP_FLAGS="--break-system-packages"
pip3 install $PIP_FLAGS "$REPO_DIR" 2>/dev/null || pip3 install "$REPO_DIR"

echo "--> Restarting the service…"
systemctl restart kilovault.service
sleep 2
systemctl --no-pager --lines=0 status kilovault.service || true

echo "✅ Updated. Reboot if you want the kiosk to reload too:  sudo reboot"
