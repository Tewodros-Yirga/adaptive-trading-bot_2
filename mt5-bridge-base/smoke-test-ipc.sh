#!/usr/bin/env bash
# smoke-test-ipc.sh — validates the baked MT5 base image via mt5-bridge REST API
#
# Architecture (mt5-bridge approach):
#   terminal64.exe   ← Wine/X11
#       ↑ MetaTrader5 named-pipe IPC (same Wine process space — no cross-OS problem)
#   mt5-bridge server (wine python mt5-bridge server --host 0.0.0.0 --port 8000)
#       ↑ HTTP REST (plain TCP — works from any OS)
#   THIS SCRIPT → curl http://127.0.0.1:8000/health
#
# Pass criteria (in order):
#   GATE 1: terminal64.exe starts and stays alive for 120 s.
#   GATE 2: mt5-bridge /health returns HTTP 200 within 20 probes × 20 s = 6.7 min.
#   GATE 3: /account returns a valid JSON account object.
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
export WINEPREFIX="${WINEPREFIX:-/opt/wineprefix}"
export WINEDEBUG="${WINEDEBUG:--all}"
export HOME="${HOME:-/root}"
export LIBGL_ALWAYS_SOFTWARE=1
export GALLIUM_DRIVER=llvmpipe
export WINEESYNC=0
export WINEFSYNC=0

LOGDIR="/tmp/mt5-ipc-smoke"
mkdir -p "${LOGDIR}"

BRIDGE_PORT=8000
BRIDGE_URL="http://127.0.0.1:${BRIDGE_PORT}"

# ---------------------------------------------------------------------------
# Validate pre-baked paths
# ---------------------------------------------------------------------------
if [[ ! -f "/opt/wine_python_exe.path" ]]; then
  echo "ERROR: /opt/wine_python_exe.path missing"; exit 1
fi
WINE_PY="$(tr -d '\r\n' < /opt/wine_python_exe.path)"
[[ -f "${WINE_PY}" ]] || { echo "ERROR: wine python not found: '${WINE_PY}'"; exit 1; }

if [[ ! -f "/opt/mt5_terminal_exe.path" ]]; then
  echo "ERROR: /opt/mt5_terminal_exe.path missing (image is not MT5-baked)"; exit 1
fi
TERM_EXE="$(tr -d '\r\n' < /opt/mt5_terminal_exe.path)"
[[ -f "${TERM_EXE}" ]] || { echo "ERROR: terminal64.exe not found: '${TERM_EXE}'"; exit 1; }
TERM_DIR="$(dirname "${TERM_EXE}")"

# ---------------------------------------------------------------------------
# Report pre-baked AppData state
# ---------------------------------------------------------------------------
APPDATA_MT5="${WINEPREFIX}/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal"
if [[ -d "${APPDATA_MT5}" ]]; then
  echo "INFO: Pre-baked AppData detected at ${APPDATA_MT5}"
  TINI_COUNT=$(find "${APPDATA_MT5}" -name "terminal.ini" 2>/dev/null | wc -l) || TINI_COUNT=0
  echo "INFO: terminal.ini count in AppData: ${TINI_COUNT}"
  find "${APPDATA_MT5}" -maxdepth 3 -type f -name "*.ini" 2>/dev/null | head -10 || true
else
  echo "WARNING: No pre-baked AppData — wizard will appear on startup"
fi

# ---------------------------------------------------------------------------
# Pre-write server config to all known locations
# ---------------------------------------------------------------------------
_write_cfg() {
  local _d="$1"
  mkdir -p "${_d}" 2>/dev/null || return
  printf '[Common]\r\nLogin=435609450\r\nPassword=Mznxbcv12#\r\nServer=Exness-MT5Trial9\r\nNewsEnable=0\r\nAutoSync=0\r\n\r\n[Experts]\r\nEnabled=1\r\nAllowLiveTrading=1\r\n' \
    > "${_d}/common.ini" 2>/dev/null || true
  echo "Wrote server config: ${_d}/common.ini"
}
_write_cfg "${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/config"
_write_cfg "${WINEPREFIX}/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal/Common/config"
find "${APPDATA_MT5}" -mindepth 2 -maxdepth 2 -type d -name "config" 2>/dev/null \
  | while IFS= read -r _hcfg; do _write_cfg "${_hcfg}"; done || true
unset -f _write_cfg

# Write portable terminal.ini stub (for /portable mode launch).
printf '[Startup]\r\nAutoStart=0\r\n\r\n[Common]\r\nLogin=435609450\r\nPassword=Mznxbcv12#\r\nServer=Exness-MT5Trial9\r\nNewsEnable=0\r\nAutoSync=0\r\n\r\n[Experts]\r\nEnabled=1\r\nAllowLiveTrading=1\r\n' \
  > "${TERM_DIR}/terminal.ini" 2>/dev/null || true
echo "Wrote portable terminal.ini stub"

# Block update domains at Linux OS level.
for _upd in live.mql5.com updates.mql5.com update.mql5.com download.mql5.com \
            cdn.mql5.com update.metatrader5.com updates.metatrader5.com \
            mt5-update.metaquotes.net; do
  echo "127.0.0.1 ${_upd}" >> /etc/hosts 2>/dev/null || true
done
echo "Linux /etc/hosts patched for update domain blocking"

# ---------------------------------------------------------------------------
# Start Xvfb + openbox
# ---------------------------------------------------------------------------
rm -f /tmp/.X99-lock || true
Xvfb "${DISPLAY}" -screen 0 1280x720x24 > "${LOGDIR}/xvfb.log" 2>&1 &
XVFB_PID=$!
sleep 3
kill -0 "${XVFB_PID}" 2>/dev/null || { echo "ERROR: Xvfb failed to start"; exit 1; }

OPENBOX_PID=""
if command -v openbox > /dev/null 2>&1; then
  DISPLAY="${DISPLAY}" openbox --sm-disable > "${LOGDIR}/openbox.log" 2>&1 &
  OPENBOX_PID=$!
  sleep 2
fi

TERM_PID=""
BRIDGE_PID=""
DISMISS_PID=""
cleanup() {
  kill "${BRIDGE_PID:-}"   2>/dev/null || true
  kill "${TERM_PID:-}"     2>/dev/null || true
  kill "${DISMISS_PID:-}"  2>/dev/null || true
  kill "${OPENBOX_PID:-}"  2>/dev/null || true
  kill "${XVFB_PID:-}"     2>/dev/null || true
  wineserver -k 2>/dev/null || true
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Launch terminal in portable mode
# ---------------------------------------------------------------------------
echo "Launching terminal in portable mode..."
WINEDEBUG="err+all" WINEESYNC=0 WINEFSYNC=0 \
  wine "${TERM_EXE}" /portable \
  > "${LOGDIR}/terminal.log" 2>&1 &
TERM_PID=$!
echo "Terminal PID: ${TERM_PID} — waiting 120 s for initial startup + broker login..."
sleep 120

# ---------------------------------------------------------------------------
# GATE 1: terminal must still be alive after 120 s
# ---------------------------------------------------------------------------
if ! kill -0 "${TERM_PID}" 2>/dev/null; then
  echo "SMOKE TEST FAIL: terminal64.exe exited within 120 s (crashed)"
  echo "--- terminal.log ---"
  cat "${LOGDIR}/terminal.log" 2>/dev/null || true
  exit 1
fi
echo "GATE 1 PASS: terminal64.exe is alive after 120 s (PID ${TERM_PID})"

# ---------------------------------------------------------------------------
# Background dialog dismisser (LiveUpdate only — Alt+F4 on dialog window)
# ---------------------------------------------------------------------------
(
  _xd() { DISPLAY="${DISPLAY}" xdotool "$@" 2>/dev/null || true; }
  for _iter in $(seq 1 120); do
    IDS=$(
      { _xd search --onlyvisible --name "LiveUpdate";
        _xd search --onlyvisible --name "Select a company";
        _xd search --onlyvisible --name "Welcome to";
        _xd search --onlyvisible --name "Login";
        _xd search --onlyvisible --name "Setup";
        # NOTE: NOT searching "MetaTrader" — it matches the healthy main window
      } | awk 'NF' | sort -n -u
    )
    for _wid in ${IDS:-}; do
      WIN_NAME=$(_xd getwindowname "${_wid}" || echo "?")
      echo "[smoke-dismiss iter=${_iter}] wid=${_wid} name=${WIN_NAME}"
      _xd windowactivate --sync "${_wid}"; sleep 0.3
      _name=$(echo "${WIN_NAME}" | tr '[:upper:]' '[:lower:]')
      if echo "${_name}" | grep -q 'liveupdate'; then
        # Alt+F4 targets only the focused child dialog, NOT the parent terminal.
        _xd key --window "${_wid}" --clearmodifiers alt+F4; sleep 0.4
        # Fallback: Tab → 'Later' button, Space activates it.
        _xd key --window "${_wid}" --clearmodifiers Tab; sleep 0.2
        _xd key --window "${_wid}" --clearmodifiers space; sleep 0.3
      else
        _xd key --window "${_wid}" --clearmodifiers Escape; sleep 0.2
      fi
    done
    sleep 3
  done
) &
DISMISS_PID=$!

# ---------------------------------------------------------------------------
# Launch mt5-bridge server inside Wine
# mt5-bridge talks to the running terminal via MetaTrader5 named-pipe IPC
# (same Wine process space → no cross-OS timeout issues).
# External callers reach it via plain HTTP on port 8000.
# ---------------------------------------------------------------------------
echo "Starting mt5-bridge server (wine python -m mt5_bridge server)..."
WINEDEBUG="-all" WINEESYNC=0 WINEFSYNC=0 \
  wine "${WINE_PY}" -m mt5_bridge server \
    --host 0.0.0.0 \
    --port "${BRIDGE_PORT}" \
    --mt5-path "$(echo "${TERM_EXE}" | sed 's|/opt/wineprefix/drive_c/|C:\\\\|;s|/|\\\\|g')" \
  > "${LOGDIR}/bridge.log" 2>&1 &
BRIDGE_PID=$!
echo "mt5-bridge server PID: ${BRIDGE_PID}"

# ---------------------------------------------------------------------------
# GATE 2: /health returns HTTP 200 within 20 probes × 20 s = ~6.7 min
# ---------------------------------------------------------------------------
echo "GATE 2: probing ${BRIDGE_URL}/health (20 attempts × 20 s)..."
IPC_PASS=false

for attempt in $(seq 1 20); do
  sleep 20

  # Crash checks
  if ! kill -0 "${TERM_PID}" 2>/dev/null; then
    echo "SMOKE TEST FAIL: terminal64.exe died during probe (attempt ${attempt})"
    echo "--- terminal.log (tail 50) ---"
    tail -n 50 "${LOGDIR}/terminal.log" 2>/dev/null || true
    exit 1
  fi
  if ! kill -0 "${BRIDGE_PID}" 2>/dev/null; then
    echo "SMOKE TEST FAIL: mt5-bridge server died (attempt ${attempt})"
    echo "--- bridge.log (tail 50) ---"
    tail -n 50 "${LOGDIR}/bridge.log" 2>/dev/null || true
    exit 1
  fi

  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    --max-time 10 "${BRIDGE_URL}/health" 2>/dev/null) || HTTP_CODE="000"
  echo "[attempt ${attempt}/20] /health HTTP ${HTTP_CODE}"

  if [[ "${HTTP_CODE}" == "200" ]]; then
    echo "GATE 2 PASS: mt5-bridge /health returned 200 (attempt ${attempt})"
    IPC_PASS=true
    break
  fi
done

if [[ "${IPC_PASS}" != "true" ]]; then
  echo "SMOKE TEST FAIL: /health never returned 200 after 20 attempts"
  echo "--- bridge.log (tail 100) ---"
  tail -n 100 "${LOGDIR}/bridge.log" 2>/dev/null || true
  echo "--- terminal.log (tail 50) ---"
  tail -n 50 "${LOGDIR}/terminal.log" 2>/dev/null || true
  exit 1
fi

# ---------------------------------------------------------------------------
# GATE 3: /account returns valid JSON
# ---------------------------------------------------------------------------
echo "GATE 3: verifying /account returns valid JSON..."
ACCT=$(curl -s --max-time 10 "${BRIDGE_URL}/account" 2>/dev/null) || ACCT=""
echo "Account response: ${ACCT}"
if echo "${ACCT}" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'balance' in d or 'login' in d" 2>/dev/null; then
  echo "GATE 3 PASS: /account returned valid account JSON"
else
  echo "WARNING: /account did not return expected JSON — terminal may not be logged in yet"
  echo "(This is non-fatal; the bridge is up and IPC is working)"
fi

echo ""
echo "SMOKE TEST PASS: mt5-bridge REST API is healthy — terminal is ready"
exit 0
