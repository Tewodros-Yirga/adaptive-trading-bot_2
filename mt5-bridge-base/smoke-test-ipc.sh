#!/usr/bin/env bash
# smoke-test-ipc.sh — validates the baked MT5 base image
#
# Pass criteria (in order):
#   GATE 1: terminal64.exe starts and stays alive for 90 s (crash detection).
#   GATE 2: mt5.initialize() returns ok=True within 30 probes × 15 s = 7.5 min.
#
# Error code discrimination:
#   -10003  IPC pipe not found → terminal dead/crashed → FAIL immediately
#   -10005  IPC pipe found but auth packet not processed (main thread blocked
#           by a dialog) → retry after dismiss loop has another chance to
#           send Escape/click
#   True    IPC handshake complete → PASS
#
# The dismiss loop (background) keeps sending Escape to any visible dialog
# every 3 s, driving the terminal into offline mode where the IPC pump runs.
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
# Pre-write MetaQuotes-Demo server config
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

# Also patch the portable install-dir config for /portable mode.
_write_cfg "${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/config"
printf '[Startup]\r\nAutoStart=0\r\n\r\n[Common]\r\nLogin=435609450\r\nPassword=Mznxbcv12#\r\nServer=Exness-MT5Trial9\r\nNewsEnable=0\r\nAutoSync=0\r\n\r\n[Experts]\r\nEnabled=1\r\nAllowLiveTrading=1\r\n' \
  > "${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/terminal.ini" 2>/dev/null || true
echo "Wrote portable terminal.ini stub"

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
DISMISS_PID=""
cleanup() {
  kill "${TERM_PID:-}"    2>/dev/null || true
  kill "${DISMISS_PID:-}" 2>/dev/null || true
  kill "${OPENBOX_PID:-}" 2>/dev/null || true
  kill "${XVFB_PID:-}"   2>/dev/null || true
  wineserver -k 2>/dev/null || true
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Launch terminal
# ---------------------------------------------------------------------------
# Launch in portable mode — same technique that fixed the build phase.
# /portable stores all state in the install dir; LiveUpdate dialog appears
# but the main thread (IPC pump) is NOT blocked by it in portable mode.
WINEDEBUG="err+all" WINEESYNC=0 WINEFSYNC=0 \
  wine "${TERM_EXE}" /portable \
  > "${LOGDIR}/terminal.log" 2>&1 &
TERM_PID=$!
echo "Terminal PID: ${TERM_PID} — waiting 90 s for initial startup..."
sleep 90

# ---------------------------------------------------------------------------
# GATE 1: terminal must still be alive after 90 s
# ---------------------------------------------------------------------------
if ! kill -0 "${TERM_PID}" 2>/dev/null; then
  echo "SMOKE TEST FAIL: terminal64.exe exited within 90 s (crashed)"
  echo "--- terminal.log ---"
  cat "${LOGDIR}/terminal.log" 2>/dev/null || true
  exit 1
fi
echo "GATE 1 PASS: terminal64.exe is alive after 90 s (PID ${TERM_PID})"

# ---------------------------------------------------------------------------
# Background dialog dismisser
# Sends Escape to every dialog every 3 s to put the terminal into offline
# mode so the main thread (IPC pump) is not blocked by a login form.
# ---------------------------------------------------------------------------
(
  _xd() { DISPLAY="${DISPLAY}" xdotool "$@" 2>/dev/null || true; }
  for _iter in $(seq 1 120); do
    IDS=$(
      { _xd search --onlyvisible --name "LiveUpdate";
        _xd search --onlyvisible --name "Select a company";
        _xd search --onlyvisible --name "Welcome to";
        _xd search --onlyvisible --name "Login";
        _xd search --onlyvisible --name "MetaTrader 5";
        _xd search --onlyvisible --name "MetaTrader";
        _xd search --onlyvisible --name "Setup";
      } | awk 'NF' | sort -n -u
    )
    for _wid in ${IDS:-}; do
      WIN_NAME=$(_xd getwindowname "${_wid}" || echo "?")
      echo "[smoke-dismiss iter=${_iter}] wid=${_wid} name=${WIN_NAME}"
      _xd windowactivate --sync "${_wid}"; sleep 0.2
      # LiveUpdate: use windowclose (WM_DELETE_WINDOW) — Escape was causing
      # the terminal itself to exit rather than just closing the dialog.
      _name=$(echo "${WIN_NAME}" | tr '[:upper:]' '[:lower:]')
      if echo "${_name}" | grep -q 'liveupdate'; then
        _xd windowclose "${_wid}" 2>/dev/null || true; sleep 0.3
      else
        # Login/company-select: Escape → offline mode → IPC pump free
        _xd key --window "${_wid}" --clearmodifiers Escape; sleep 0.2
        _xd key --window "${_wid}" --clearmodifiers Escape; sleep 0.2
      fi
    done
    sleep 3
  done
) &
DISMISS_PID=$!

# ---------------------------------------------------------------------------
# GATE 2: mt5.initialize() probe — 30 attempts × 15 s = 7.5 min
#
# Error discrimination:
#   -10003 → pipe not found → terminal dead → FAIL immediately
#   -10005 → pipe found but main thread blocked → retry (dismiss loop is running)
#   True   → IPC handshake complete → PASS
# ---------------------------------------------------------------------------
PROBE_SCRIPT='
import sys, time
try:
    import MetaTrader5 as mt5
    ok = mt5.initialize(
        login=435609450,
        password="Mznxbcv12#",
        server="Exness-MT5Trial9",
        timeout=12000
    )
    err = mt5.last_error()
    mt5.shutdown()
    print("ok=%s err=%s" % (ok, err))
    if ok:
        sys.exit(0)
    elif err[0] == -10003:
        sys.exit(2)
    else:
        sys.exit(3)
except Exception as e:
    print("exception: %s" % e)
    sys.exit(4)
'

echo "GATE 2: probing mt5.initialize() (30 attempts × 15 s = 7.5 min)..."
IPC_PASS=false

for attempt in $(seq 1 30); do
  # Show visible windows for diagnostics
  WIN_TITLES=""
  if command -v xdotool > /dev/null 2>&1; then
    WIN_TITLES=$(
      DISPLAY="${DISPLAY}" xdotool search --onlyvisible 2>/dev/null \
        | while IFS= read -r _wid; do
            DISPLAY="${DISPLAY}" xdotool getwindowname "${_wid}" 2>/dev/null || true
          done | paste -sd'|' -
    ) || WIN_TITLES=""
  fi
  echo "[attempt ${attempt}/30] windows=${WIN_TITLES}"

  # Crash check
  if ! kill -0 "${TERM_PID}" 2>/dev/null; then
    echo "SMOKE TEST FAIL: terminal64.exe died during probe (attempt ${attempt})"
    echo "--- terminal.log (tail 100) ---"
    tail -n 100 "${LOGDIR}/terminal.log" 2>/dev/null || true
    kill "${DISMISS_PID}" 2>/dev/null || true
    exit 1
  fi

  # Run probe
  PROBE_OUT=""
  PROBE_EXIT=0
  PROBE_OUT=$(timeout 20 wine "${WINE_PY}" -c "${PROBE_SCRIPT}" 2>&1) || PROBE_EXIT=$?

  echo "[attempt ${attempt}/30] probe_exit=${PROBE_EXIT} probe_out=${PROBE_OUT}"

  if [[ "${PROBE_EXIT}" -eq 0 ]]; then
    echo "GATE 2 PASS: mt5.initialize() returned ok=True (attempt ${attempt})"
    IPC_PASS=true
    break
  elif [[ "${PROBE_EXIT}" -eq 2 ]]; then
    echo "SMOKE TEST FAIL: -10003 (IPC pipe not found) — terminal is not creating the pipe"
    echo "This usually means the terminal crashed or is stuck before IPC init."
    echo "--- terminal.log (tail 100) ---"
    tail -n 100 "${LOGDIR}/terminal.log" 2>/dev/null || true
    kill "${DISMISS_PID}" 2>/dev/null || true
    exit 1
  else
    echo "[attempt ${attempt}/30] -10005 or other error — main thread busy, retrying in 15 s..."
  fi

  sleep 15
done

kill "${DISMISS_PID}" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Final verdict
# ---------------------------------------------------------------------------
if [[ "${IPC_PASS}" == "true" ]]; then
  echo "SMOKE TEST PASS: IPC handshake succeeded — terminal is healthy"
  exit 0
fi

echo "SMOKE TEST FAIL: mt5.initialize() never returned ok=True after 30 attempts"
echo ""
echo "Diagnostic summary:"
echo "  - If all 30 attempts returned -10005: the terminal's main thread was"
echo "    blocked for the entire 7.5 min window. Check terminal.log for"
echo "    dialogs that the dismiss loop could not cancel."
echo "  - Consider rebuilding the base image with a newer terminal version"
echo "    (monthly schedule is in place) to avoid the LiveUpdate dialog."
echo ""
echo "--- terminal.log (tail 200) ---"
tail -n 200 "${LOGDIR}/terminal.log" 2>/dev/null || true
echo "--- xvfb.log (tail 50) ---"
tail -n 50 "${LOGDIR}/xvfb.log" 2>/dev/null || true
exit 1
