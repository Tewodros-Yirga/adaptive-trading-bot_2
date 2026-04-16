#!/usr/bin/env bash
set -euo pipefail

export DISPLAY=${DISPLAY:-:99}
export BRIDGE_PORT=${PORT:-${BRIDGE_PORT:-5555}}
export MT_TERMINAL_EXE=${MT_TERMINAL_EXE:-/root/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe}
export WINEPREFIX=${WINEPREFIX:-/root/.wine}

required_vars=("MT_LOGIN" "MT_PASSWORD" "MT_SERVER" "MT_BRIDGE_SECRET")
for key in "${required_vars[@]}"; do
  if [[ -z "${!key:-}" ]]; then
    echo "Missing required env var: ${key}" >&2
    exit 1
  fi
done

# Start virtual display first so Wine can initialize safely.
echo "Starting Xvfb display server on ${DISPLAY}..."
Xvfb "${DISPLAY}" -screen 0 1280x720x24 >/tmp/xvfb.log 2>&1 &
XVFB_PID=$!
sleep 2
if ! kill -0 "${XVFB_PID}" 2>/dev/null; then
  echo "Failed to start Xvfb. Check /tmp/xvfb.log" >&2
  exit 1
fi

echo "Preparing Wine prefix..."
WINEBOOT_CMD=""
if command -v wineboot >/dev/null 2>&1; then
  WINEBOOT_CMD="wineboot"
elif command -v wine64boot >/dev/null 2>&1; then
  WINEBOOT_CMD="wine64boot"
elif command -v wineboot64 >/dev/null 2>&1; then
  WINEBOOT_CMD="wineboot64"
fi

if [[ -z "$WINEBOOT_CMD" ]]; then
  echo "wineboot command not found inside container; ensure Wine is installed in Docker image." >&2
  exit 1
fi

"$WINEBOOT_CMD" --init || true

if [[ -n "${MT5_INSTALLER_URL:-}" && ! -f "$MT_TERMINAL_EXE" ]]; then
  echo "Downloading MT5 installer from MT5_INSTALLER_URL..."
  mkdir -p /tmp/mt5
  curl -L "$MT5_INSTALLER_URL" -o /tmp/mt5/mt5setup.exe
  echo "Running MT5 installer via Wine..."
  WINE_CMD=""
  if command -v wine >/dev/null 2>&1; then
    WINE_CMD="wine"
  elif command -v wine64 >/dev/null 2>&1; then
    WINE_CMD="wine64"
  fi

  if [[ -z "$WINE_CMD" ]]; then
    echo "wine command not found inside container; ensure Wine is installed in Docker image." >&2
    exit 1
  fi

  "$WINE_CMD" /tmp/mt5/mt5setup.exe /silent || true
fi

if [[ ! -f "$MT_TERMINAL_EXE" ]]; then
  echo "Warning: MT terminal executable not found at: $MT_TERMINAL_EXE"
  echo "Set MT_TERMINAL_EXE or MT5_INSTALLER_URL correctly."
fi

echo "Starting supervisord (mt5 + bridge api)..."
exec /usr/bin/supervisord -c /bridge/supervisord.conf
