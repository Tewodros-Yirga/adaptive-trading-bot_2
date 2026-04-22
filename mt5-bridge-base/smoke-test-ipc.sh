#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
export WINEPREFIX="${WINEPREFIX:-/opt/wineprefix}"
export WINEDEBUG="${WINEDEBUG:--all}"
export HOME="${HOME:-/root}"

LOGDIR="/tmp/mt5-ipc-smoke"
mkdir -p "${LOGDIR}"

if [[ ! -f "/opt/wine_python_exe.path" ]]; then
  echo "ERROR: /opt/wine_python_exe.path missing"
  exit 1
fi
WINE_PY="$(tr -d '\r\n' < /opt/wine_python_exe.path)"
if [[ -z "${WINE_PY}" || ! -f "${WINE_PY}" ]]; then
  echo "ERROR: wine python not found: '${WINE_PY}'"
  exit 1
fi

if [[ ! -f "/opt/mt5_terminal_exe.path" ]]; then
  echo "ERROR: /opt/mt5_terminal_exe.path missing (image is not MT5-baked)"
  exit 1
fi
TERM_EXE="$(tr -d '\r\n' < /opt/mt5_terminal_exe.path)"
if [[ -z "${TERM_EXE}" || ! -f "${TERM_EXE}" ]]; then
  echo "ERROR: terminal64.exe not found: '${TERM_EXE}'"
  exit 1
fi

rm -f /tmp/.X99-lock || true
Xvfb "${DISPLAY}" -screen 0 1280x720x24 > "${LOGDIR}/xvfb.log" 2>&1 &
XVFB_PID=$!
sleep 3
if ! kill -0 "${XVFB_PID}" 2>/dev/null; then
  echo "ERROR: failed to start Xvfb"
  exit 1
fi

cleanup() {
  kill "${TERM_PID:-}" 2>/dev/null || true
  kill "${XVFB_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT

wine "${TERM_EXE}" > "${LOGDIR}/terminal.log" 2>&1 &
TERM_PID=$!
sleep 8

for attempt in $(seq 1 20); do
  for mode in default portable; do
    PORTABLE_FLAG="False"
    if [[ "${mode}" == "portable" ]]; then
      PORTABLE_FLAG="True"
    fi
    OUT_FILE="${LOGDIR}/probe-${attempt}-${mode}.log"
    timeout 25 wine "${WINE_PY}" -c "
import MetaTrader5 as mt5
portable = ${PORTABLE_FLAG}
ok = False
try:
    ok = mt5.initialize(timeout=15000, portable=portable)
except Exception:
    ok = False
err = mt5.last_error()
mt5.shutdown()
print(f'ok={ok} portable={portable} err={err}')
" > "${OUT_FILE}" 2>&1 || true
    OUT="$(tr -d '\r' < "${OUT_FILE}")"
    echo "[attempt ${attempt}] ${OUT}"
    if [[ "${OUT}" == *"ok=True"* ]]; then
      echo "SMOKE TEST PASS: IPC attach succeeded"
      exit 0
    fi
  done
  sleep 5
done

echo "SMOKE TEST FAIL: IPC attach never succeeded"
exit 1
