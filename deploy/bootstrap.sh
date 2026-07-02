#!/usr/bin/env bash
# One-line Raspberry Pi installer. Downloads the monitor and runs the full
# cabin-box setup — the Pi equivalent of double-clicking the Windows .exe.
#
#   curl -fsSL https://raw.githubusercontent.com/LstDtchMn/Solar-Battery-App/main/deploy/bootstrap.sh | sudo bash
#
# Pass installer flags through after `-s --`, e.g. unattended kiosk + hotspot:
#   curl -fsSL <url>/bootstrap.sh | sudo bash -s -- --kiosk --hotspot
#
set -euo pipefail

REPO_URL="https://github.com/LstDtchMn/Solar-Battery-App.git"
BRANCH="${KV_BRANCH:-main}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo. Copy-paste the whole line, including 'sudo bash'." >&2
  exit 1
fi

# Install for the invoking login user (usually 'pi'), not root.
KV_USER="${SUDO_USER:-pi}"
[ "$KV_USER" = "root" ] && KV_USER="pi"
KV_HOME="$(getent passwd "$KV_USER" | cut -d: -f6)"
[ -z "$KV_HOME" ] && KV_HOME="/home/$KV_USER"
DEST="$KV_HOME/Solar-Battery-App"

echo "=================================================================="
echo "  KiloVault HLX+ Monitor — Raspberry Pi one-line installer"
echo "  User: $KV_USER    Into: $DEST    Branch: $BRANCH"
echo "=================================================================="

echo "--> Making sure git is installed…"
export DEBIAN_FRONTEND=noninteractive
if ! command -v git >/dev/null 2>&1; then
  apt-get update -qq && apt-get install -y --no-install-recommends git
fi

# Clone fresh, or update in place if re-run. Owned by the login user.
if [ -d "$DEST/.git" ]; then
  echo "--> Updating existing copy…"
  sudo -u "$KV_USER" git -C "$DEST" fetch --depth 1 origin "$BRANCH"
  sudo -u "$KV_USER" git -C "$DEST" checkout "$BRANCH"
  sudo -u "$KV_USER" git -C "$DEST" reset --hard "origin/$BRANCH"
else
  echo "--> Downloading the monitor…"
  sudo -u "$KV_USER" git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$DEST"
fi

echo "--> Running the cabin-box installer…"
chmod +x "$DEST/deploy/install-pi.sh"
exec bash "$DEST/deploy/install-pi.sh" "$@"
