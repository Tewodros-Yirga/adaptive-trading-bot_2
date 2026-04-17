#!/usr/bin/env bash
set -euo pipefail

export DISPLAY=${DISPLAY:-:99}
export BRIDGE_PORT=${PORT:-${BRIDGE_PORT:-5555}}
export HOME="${HOME:-/home/wineuser}"
export WINEPREFIX="${WINEPREFIX:-${HOME}/.wineprefix}"
DERIVED_TERMINAL_EXE="${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/terminal64.exe"
export MT_TERMINAL_EXE="${MT_TERMINAL_EXE:-$DERIVED_TERMINAL_EXE}"
export PYTHON_WIN_INSTALLER_URL=${PYTHON_WIN_INSTALLER_URL:-https://www.python.org/ftp/python/3.9.13/python-3.9.13.exe}

# Keep logs and bootstrap downloads out of /tmp (Render eviction limit).
# Use $HOME because Render may restrict writes to /opt and /bridge.
export MT5_WORKDIR=${MT5_WORKDIR:-${HOME}/.mt5-work}
export LOGDIR=${LOGDIR:-${HOME}/.mt5-bridge-logs}
mkdir -p "${WINEPREFIX}" "${MT5_WORKDIR}" "${LOGDIR}"

# Render free-tier limits /tmp to ~2GB. Some Wine/MT5 output is redirected to
# `/tmp/mt5-launch-wrapper.log`, so symlink it into our persistent LOGDIR.
rm -f /tmp/mt5-launch-wrapper.log >/dev/null 2>&1 || true
ln -sf "${LOGDIR}/mt5-launch-wrapper.log" /tmp/mt5-launch-wrapper.log >/dev/null 2>&1 || true

# If Render (or an old deploy) still provides the wrong prefix path,
# force the executable path to match the actual runtime WINEPREFIX.
if [[ ("${MT_TERMINAL_EXE}" == /root/.wine/* || "${MT_TERMINAL_EXE}" == /tmp/.wine/* || "${MT_TERMINAL_EXE}" == /bridge/.wine/*) && "${WINEPREFIX}" != /root/.wine* && "${WINEPREFIX}" != /tmp/.wine* && "${WINEPREFIX}" != /bridge/.wine* ]]; then
  export MT_TERMINAL_EXE="$DERIVED_TERMINAL_EXE"
fi

mkdir -p "${WINEPREFIX}"

required_vars=("MT_LOGIN" "MT_PASSWORD" "MT_SERVER" "MT_BRIDGE_SECRET")
for key in "${required_vars[@]}"; do
  if [[ -z "${!key:-}" ]]; then
    echo "Missing required env var: ${key}" >&2
    exit 1
  fi
done

# Start virtual display first so Wine can initialize safely.
echo "Starting Xvfb display server on ${DISPLAY}..."
Xvfb "${DISPLAY}" -screen 0 1280x720x24 >"${LOGDIR}/xvfb.log" 2>&1 &
XVFB_PID=$!
sleep 2
if ! kill -0 "${XVFB_PID}" 2>/dev/null; then
  echo "Failed to start Xvfb. Check ${LOGDIR}/xvfb.log" >&2
  exit 1
fi

echo "Starting bridge API (uvicorn) immediately; MT5 bootstrap in background..."

PORT="${PORT:-${BRIDGE_PORT:-5555}}"

# Bootstrap Wine/MT5 in the background so Render detects the open port quickly.
/bridge/bootstrap-mt5.sh >"${LOGDIR}/bootstrap-mt5.log" 2>&1 &

# Start mt5linux server under Wine (best-effort).
# This is required for the Linux adapter to talk to MT5 via RPyC.
if command -v wine >/dev/null 2>&1; then
  (
    set +e
    echo "Starting mt5linux via `wine python` (best-effort)..."

    # Wait until Wine python can import stdlib and mt5linux.
    for _ in $(seq 1 180); do
      if wine python -c "import encodings; import mt5linux" >/dev/null 2>&1; then
        break
      fi

      # If python exists but mt5linux isn't installed yet, try installing (best-effort).
      if wine python -c "import encodings" >/dev/null 2>&1; then
        wine python -m pip install --upgrade --no-cache-dir pip >/dev/null 2>&1 || true
        wine python -m pip install --no-cache-dir mt5linux MetaTrader5 python-dateutil >/dev/null 2>&1 || true
      fi
      sleep 2
    done

    if ! wine python -c "import encodings; import mt5linux" >/dev/null 2>&1; then
      echo "Wine python / mt5linux not working yet. Check ${LOGDIR}/bootstrap-mt5.log" >&2
    else
      echo "Launching mt5linux RPyC server on 127.0.0.1:18812"
      wine python -m mt5linux --host 127.0.0.1 --port 18812 2>&1 | tee "${LOGDIR}/mt5linux.log" &
    fi

    # Wait a moment and verify the port is actually listening from the Linux side.
    # This helps us distinguish "wine/python missing" from "mt5linux installed but MT5 not connected".
    MT5LINUX_PORT_OPEN=false
    for _ in $(seq 1 20); do
      if (echo > /dev/tcp/127.0.0.1/18812) >/dev/null 2>&1; then
        MT5LINUX_PORT_OPEN=true
        break
      fi
      sleep 0.5
    done
    if [[ "$MT5LINUX_PORT_OPEN" == "true" ]]; then
      echo "mt5linux RPyC port is open on 127.0.0.1:18812"
    else
      echo "mt5linux RPyC port is NOT open on 127.0.0.1:18812" >&2
      echo "=== ${LOGDIR}/bootstrap-mt5.log (tail) ===" >&2
      tail -n 250 "${LOGDIR}/bootstrap-mt5.log" >&2 || true
      echo "mt5linux log (last 200 lines):" >&2
      if [[ -f "${LOGDIR}/mt5linux.log" ]]; then
        tail -n 200 "${LOGDIR}/mt5linux.log" >&2 || true
      else
        echo "(missing ${LOGDIR}/mt5linux.log)" >&2
      fi
    fi
  ) &
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
    "$WINE_CMD" "$MT_TERMINAL_EXE" >"${LOGDIR}/mt5-terminal.log" 2>&1 || true
  else
    echo "MT terminal executable not found at: $MT_TERMINAL_EXE" >&2
  fi
) >/tmp/mt5-launch-wrapper.log 2>&1 &

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
