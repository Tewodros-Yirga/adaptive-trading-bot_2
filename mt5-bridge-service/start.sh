#!/usr/bin/env bash
set -euo pipefail

export DISPLAY=${DISPLAY:-:99}
export BRIDGE_PORT=${PORT:-${BRIDGE_PORT:-5555}}
export WINEPREFIX=${WINEPREFIX:-/tmp/.wine}
export MT_TERMINAL_EXE=${MT_TERMINAL_EXE:-${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/terminal64.exe}

mkdir -p "${WINEPREFIX}" /tmp/mt5 /tmp/supervisor

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

echo "Starting supervisord (bootstrap + mt5 + bridge api)..."
exec /usr/bin/supervisord -c /bridge/supervisord.conf
