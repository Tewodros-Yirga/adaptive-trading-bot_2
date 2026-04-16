#!/usr/bin/env bash
set -euo pipefail

export DISPLAY=${DISPLAY:-:99}
export BRIDGE_PORT=${PORT:-${BRIDGE_PORT:-5555}}
export WINEPREFIX=${WINEPREFIX:-/tmp/.wine}
DERIVED_TERMINAL_EXE="${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/terminal64.exe"
export MT_TERMINAL_EXE="${MT_TERMINAL_EXE:-$DERIVED_TERMINAL_EXE}"

# If Render (or an old deploy) still provides the wrong prefix path (e.g. /root/.wine),
# force the executable path to match the actual runtime WINEPREFIX (/tmp/.wine).
if [[ "${MT_TERMINAL_EXE}" == /root/.wine/* && "${WINEPREFIX}" != /root/.wine* ]]; then
  export MT_TERMINAL_EXE="$DERIVED_TERMINAL_EXE"
fi

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

echo "Starting bridge API (uvicorn) immediately; MT5 bootstrap in background..."

PORT="${PORT:-${BRIDGE_PORT:-5555}}"

# Bootstrap Wine/MT5 in the background so Render detects the open port quickly.
/bridge/bootstrap-mt5.sh >/tmp/bootstrap-mt5.log 2>&1 &

# Start mt5linux server under Wine (best-effort).
# This is required for the Linux adapter to talk to MT5 via RPyC.
if command -v wine >/dev/null 2>&1; then
  (
    set +e
    echo "Bootstrapping mt5linux inside Wine python (best-effort)..."
    # If Wine has no Windows-Python installed, these commands will fail but we continue.
    wine python -c "import mt5linux" >/dev/null 2>&1
    if [[ $? -ne 0 ]]; then
      echo "mt5linux missing in Wine python; attempting pip install..." >&2
      wine python -m pip install --no-cache-dir --upgrade pip >/tmp/mt5linux-pip-upgrade.log 2>&1 || true
      wine python -m pip install --no-cache-dir mt5linux MetaTrader5 >/tmp/mt5linux-pip-install.log 2>&1 || true
    else
      echo "mt5linux already present in Wine python."
    fi

    # Start the RPyC server (it listens on localhost:18812 by default).
    wine python -m mt5linux >/tmp/mt5linux.log 2>&1 &
  ) >/tmp/mt5linux-bootstrap-wrapper.log 2>&1 &
fi

# Best-effort MT5 terminal launch in background.
WINE_CMD=""
if command -v wine >/dev/null 2>&1; then
  WINE_CMD="wine"
elif command -v wine64 >/dev/null 2>&1; then
  WINE_CMD="wine64"
fi

(
  if [[ -z "$WINE_CMD" ]]; then
    echo "Wine command not found (wine/wine64); skipping MT5 terminal launch." >&2
    exit 0
  fi

  for _ in $(seq 1 60); do
    if [[ -f "$MT_TERMINAL_EXE" ]]; then
      break
    fi
    sleep 5
  done

  if [[ -f "$MT_TERMINAL_EXE" ]]; then
    "$WINE_CMD" "$MT_TERMINAL_EXE" >/tmp/mt5-terminal.log 2>&1 || true
  else
    echo "MT terminal executable not found at: $MT_TERMINAL_EXE" >&2
  fi
) >/tmp/mt5-launch-wrapper.log 2>&1 &

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
