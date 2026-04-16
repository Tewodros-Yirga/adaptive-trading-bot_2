#!/usr/bin/env bash
set -euo pipefail

export DISPLAY=${DISPLAY:-:99}
export BRIDGE_PORT=${PORT:-${BRIDGE_PORT:-5555}}
export WINEPREFIX=${WINEPREFIX:-/opt/wineprefix}
# Prevent Render/old deployments from putting the Wine prefix under /tmp (can be evicted).
if [[ "${WINEPREFIX}" == /tmp/.wine || "${WINEPREFIX}" == /tmp/.wine/* || "${WINEPREFIX}" == /root/.wine || "${WINEPREFIX}" == /root/.wine/* ]]; then
  export WINEPREFIX="/opt/wineprefix"
fi
DERIVED_TERMINAL_EXE="${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/terminal64.exe"
export MT_TERMINAL_EXE="${MT_TERMINAL_EXE:-$DERIVED_TERMINAL_EXE}"
export PYTHON_WIN_INSTALLER_URL=${PYTHON_WIN_INSTALLER_URL:-https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe}

# If Render (or an old deploy) still provides the wrong prefix path,
# force the executable path to match the actual runtime WINEPREFIX.
if [[ ("${MT_TERMINAL_EXE}" == /root/.wine/* || "${MT_TERMINAL_EXE}" == /tmp/.wine/* || "${MT_TERMINAL_EXE}" == /bridge/.wine/*) && "${WINEPREFIX}" != /root/.wine* && "${WINEPREFIX}" != /tmp/.wine* && "${WINEPREFIX}" != /bridge/.wine* ]]; then
  export MT_TERMINAL_EXE="$DERIVED_TERMINAL_EXE"
fi

mkdir -p "${WINEPREFIX}" /tmp/mt5 /tmp/supervisor

resolve_windows_python() {
  shopt -s nullglob
  local hits=()

  hits+=("${WINEPREFIX}"/drive_c/users/*/AppData/Local/Programs/Python/Python*/python.exe)
  hits+=("${WINEPREFIX}"/drive_c/Program\ Files/Python*/python.exe)
  hits+=("${WINEPREFIX}"/drive_c/Python*/python.exe)

  local c
  for c in "${hits[@]}"; do
    if [[ -f "$c" ]]; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

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
    PYTHON_WIN_EXE=""
    for _ in $(seq 1 180); do
      PYTHON_WIN_EXE="$(resolve_windows_python || true)"
      if [[ -n "${PYTHON_WIN_EXE}" ]]; then
        break
      fi
      sleep 2
    done

    if [[ -z "${PYTHON_WIN_EXE}" ]]; then
      echo "Windows Python executable not found in Wine prefix after waiting." >&2
      echo "=== /tmp/bootstrap-mt5.log (tail) ===" >&2
      tail -n 250 /tmp/bootstrap-mt5.log >&2 || true
      echo "=== /tmp/python-installer.log (tail) ===" >&2
      tail -n 250 /tmp/python-installer.log >&2 || true
      echo "=== candidate search (tail) ===" >&2
      ls -la "${WINEPREFIX}/drive_c/users" >/tmp/wineusers-ls.log 2>&1 || true
      tail -n 80 /tmp/wineusers-ls.log >&2 || true
    else
      echo "Using Windows Python at ${PYTHON_WIN_EXE}"
      # Ensure Python stdlib is usable (fixes the "No module named encodings" failure).
      for _ in $(seq 1 60); do
        wine "${PYTHON_WIN_EXE}" -c "import encodings" >/dev/null 2>&1 && break
        sleep 2
        PYTHON_WIN_EXE="$(resolve_windows_python || true)"
      done

      wine "${PYTHON_WIN_EXE}" -c "import encodings" >/tmp/python-encodings-check-start.log 2>&1
      if [[ $? -ne 0 ]]; then
        echo "Windows Python encodings check failed; skipping mt5linux start." >&2
        tail -n 120 /tmp/python-encodings-check-start.log >&2 || true
      else
        wine "${PYTHON_WIN_EXE}" -c "import mt5linux" >/tmp/mt5linux-import.log 2>&1
        if [[ $? -ne 0 ]]; then
          echo "mt5linux missing in Wine python; attempting pip install..." >&2
          wine "${PYTHON_WIN_EXE}" -m pip install --upgrade pip >/tmp/mt5linux-pip-upgrade.log 2>&1 || true
          wine "${PYTHON_WIN_EXE}" -m pip install mt5linux MetaTrader5 >/tmp/mt5linux-pip-install.log 2>&1 || true
        else
          echo "mt5linux already present in Wine python."
        fi

        # Start the RPyC server.
        # Force IPv4 binding/port to avoid `localhost` => `::1` issues.
        wine "${PYTHON_WIN_EXE}" -m mt5linux --host 127.0.0.1 --port 18812 2>&1 | tee /tmp/mt5linux.log &
      fi
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
      echo "=== /tmp/bootstrap-mt5.log (tail) ===" >&2
      tail -n 250 /tmp/bootstrap-mt5.log >&2 || true
      echo "mt5linux log (last 200 lines):" >&2
      if [[ -f /tmp/mt5linux.log ]]; then
        tail -n 200 /tmp/mt5linux.log >&2 || true
      else
        echo "(missing /tmp/mt5linux.log)" >&2
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
    "$WINE_CMD" "$MT_TERMINAL_EXE" >/tmp/mt5-terminal.log 2>&1 || true
  else
    echo "MT terminal executable not found at: $MT_TERMINAL_EXE" >&2
  fi
) >/tmp/mt5-launch-wrapper.log 2>&1 &

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
