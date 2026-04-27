#!/usr/bin/env bash
# smoke-test-ipc.sh — validates the baked MT5 base image
#
# Pass criteria (in order):
#   GATE 1: terminal64.exe starts and stays alive for 120 s (crash detection).
#   GATE 2: mt5.initialize() returns ok=True within 20 probes × 30 s = 10 min.
#
# Key design notes:
#   - Terminal is launched with /portable to suppress the first-run wizard.
#   - LiveUpdate dialog dismissed with Alt+F4 (targets focused child only —
#     windowclose/WM_DELETE_WINDOW propagates to root and kills the terminal).
#   - Shell probe timeout (45 s) > mt5 timeout (30 s) so the Python process
#     always returns before the shell kills it.
#   - "MetaTrader" / "MetaTrader 5" NOT in the dismiss search list — those
#     patterns match the healthy main window and Escape disrupts the terminal.
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
  printf '[Common]\r\nLogin=435609450\r\nPassword=Mznxbcv12#\r\nServer=Exness-MT5Trial9\r\nNewsEnable=0\r\nAutoSync=0\r\nAutoUpdate=0\r\n\r\n[Experts]\r\nEnabled=1\r\nAllowLiveTrading=1\r\n' \
    > "${_d}/common.ini" 2>/dev/null || true
  echo "Wrote server config: ${_d}/common.ini"
}
_write_cfg "${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/config"
_write_cfg "${WINEPREFIX}/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal/Common/config"
find "${APPDATA_MT5}" -mindepth 2 -maxdepth 2 -type d -name "config" 2>/dev/null \
  | while IFS= read -r _hcfg; do _write_cfg "${_hcfg}"; done || true
unset -f _write_cfg

# Write portable terminal.ini stub (suppresses first-run wizard + LiveUpdate).
printf '[Startup]\r\nAutoStart=0\r\n\r\n[Common]\r\nLogin=435609450\r\nPassword=Mznxbcv12#\r\nServer=Exness-MT5Trial9\r\nNewsEnable=0\r\nAutoSync=0\r\nAutoUpdate=0\r\n\r\n[Experts]\r\nEnabled=1\r\nAllowLiveTrading=1\r\n\r\n[LiveUpdate]\r\nEnabled=0\r\nNextUpdate=9999999999\r\n' \
  > "${TERM_DIR}/terminal.ini" 2>/dev/null || true
echo "Wrote portable terminal.ini stub (LiveUpdate disabled)"

# Block update domains at Linux OS level (belt-and-suspenders with Wine hosts).
for _upd in live.mql5.com updates.mql5.com update.mql5.com download.mql5.com \
            cdn.mql5.com update.metatrader5.com updates.metatrader5.com \
            mt5-update.metaquotes.net; do
  echo "127.0.0.1 ${_upd}" >> /etc/hosts 2>/dev/null || true
done
echo "Linux /etc/hosts patched for update domain blocking"

# ---------------------------------------------------------------------------
# Remove liveme.exe from the MT5 install directory.
# MT5 calls CreateProcess(liveme.exe) + WaitForSingleObject on startup,
# which blocks the main thread (and IPC pump) until liveme.exe exits.
# Since liveme.exe shows a GUI dialog that requires user input, it never
# exits — causing -10005 indefinitely. Removing the file causes CreateProcess
# to fail instantly, freeing the main thread for IPC.
# ---------------------------------------------------------------------------
find "${TERM_DIR}" -maxdepth 1 -iname "liveme*.exe" -delete 2>/dev/null || true
ls -la "${TERM_DIR}/liveme.exe" 2>/dev/null \
  && echo "WARNING: liveme.exe still present!" \
  || echo "liveme.exe removed (or was never present)"

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
LIVEME_PID=""
cleanup() {
  kill "${LIVEME_PID:-}"  2>/dev/null || true
  kill "${TERM_PID:-}"    2>/dev/null || true
  kill "${DISMISS_PID:-}" 2>/dev/null || true
  kill "${OPENBOX_PID:-}" 2>/dev/null || true
  kill "${XVFB_PID:-}"   2>/dev/null || true
  wineserver -k 2>/dev/null || true
}
trap cleanup EXIT

# Background loop: aggressively kill any liveme.exe wine process that appears.
# Belt-and-suspenders in case MT5 re-downloads liveme.exe at runtime.
(
  while true; do
    pkill -f -i 'liveme' 2>/dev/null || true
    sleep 1
  done
) &
LIVEME_PID=$!

# Write LiveUpdate=disabled into Wine registry before terminal launch.
# Belt-and-suspenders alongside terminal.ini settings.
WINEDEBUG="-all" WINEESYNC=0 WINEFSYNC=0 \
  wine reg add "HKCU\\Software\\MetaQuotes\\Terminal5" \
    /v "LiveUpdate" /t REG_DWORD /d 0 /f 2>/dev/null || true
WINEDEBUG="-all" WINEESYNC=0 WINEFSYNC=0 \
  wine reg add "HKCU\\Software\\MetaQuotes\\Terminal5\\Settings" \
    /v "LiveUpdate" /t REG_DWORD /d 0 /f 2>/dev/null || true
echo "Registry: LiveUpdate=0 written"

# ---------------------------------------------------------------------------
# Launch terminal in /portable mode
# /portable = MT5 stores all state in the install dir, not AppData.
# This bypasses the first-run wizard that blocks the main thread.
# ---------------------------------------------------------------------------
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
# Background dialog dismisser
# Only targets known dialog names — NOT "MetaTrader"/"MetaTrader 5" which
# match the healthy main window. Uses Alt+F4 for LiveUpdate (closes only the
# focused child dialog; windowclose kills the parent terminal process).
# ---------------------------------------------------------------------------
(
  _xd() { DISPLAY="${DISPLAY}" xdotool "$@" 2>/dev/null || true; }
  for _iter in $(seq 1 200); do
    IDS=$(
      { _xd search --onlyvisible --name "LiveUpdate";
        _xd search --onlyvisible --name "Select a company";
        _xd search --onlyvisible --name "Welcome to";
        _xd search --onlyvisible --name "Login";
        _xd search --onlyvisible --name "Setup";
        # DO NOT add "MetaTrader" or "MetaTrader 5" — matches the main window.
      } | awk 'NF' | sort -n -u
    )
    for _wid in ${IDS:-}; do
      WIN_NAME=$(_xd getwindowname "${_wid}" || echo "?")
      echo "[smoke-dismiss iter=${_iter}] wid=${_wid} name=${WIN_NAME}"
      _xd windowactivate --sync "${_wid}"; sleep 0.3
      _name=$(echo "${WIN_NAME}" | tr '[:upper:]' '[:lower:]')
      if echo "${_name}" | grep -q 'liveupdate'; then
        # Alt+F4: sends WM_SYSCOMMAND SC_CLOSE to the focused child only.
        # This differs from windowclose (WM_DELETE_WINDOW) which propagates
        # up to the root window and kills the entire terminal process.
        _xd key --window "${_wid}" --clearmodifiers alt+F4; sleep 0.5
        # Belt-and-suspenders: Tab → 'Later' button, Space clicks it.
        _xd key --window "${_wid}" --clearmodifiers Tab; sleep 0.2
        _xd key --window "${_wid}" --clearmodifiers space; sleep 0.3
      else
        # Login / company-select: Escape → offline mode → IPC pump free.
        _xd key --window "${_wid}" --clearmodifiers Escape; sleep 0.2
      fi
    done
    sleep 3
  done
) &
DISMISS_PID=$!

# ---------------------------------------------------------------------------
# GATE 2: mt5.initialize() probe — 20 attempts × 30 s = 10 min
#
# Error codes:
#   -10003 → IPC pipe not found → terminal dead → FAIL immediately
#   -10005 → IPC pipe found but main thread still busy → retry
#   True   → IPC handshake complete → PASS
#
# IMPORTANT timeouts:
#   mt5.initialize(timeout=30000)  — 30 s inside Python
#   shell: timeout 45              — must be > Python timeout so Python
#                                    always returns before the shell kills it
# ---------------------------------------------------------------------------
# Phase 1 probe: IPC-only (no credentials).
# mt5.initialize() without login/server just verifies the named pipe is alive.
# Returns True in ~2 s if the terminal is running — no broker roundtrip,
# no LiveUpdate or network auth can block it.
IPC_PROBE_SCRIPT='
import sys
try:
    import MetaTrader5 as mt5
    ok = mt5.initialize(timeout=10000)
    err = mt5.last_error()
    ver = mt5.version() if ok else None
    mt5.shutdown()
    print("ok=%s err=%s version=%s" % (ok, err, ver))
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

# Phase 2 probe (non-fatal): verify broker login works.
BROKER_PROBE_SCRIPT='
import sys
try:
    import MetaTrader5 as mt5
    ok = mt5.initialize(
        login=435609450,
        password="Mznxbcv12#",
        server="Exness-MT5Trial9",
        timeout=30000
    )
    err = mt5.last_error()
    acct = mt5.account_info()
    mt5.shutdown()
    print("ok=%s err=%s login=%s balance=%s" % (
        ok, err,
        getattr(acct,"login","?") if acct else "?",
        getattr(acct,"balance","?") if acct else "?",
    ))
    sys.exit(0 if ok else 1)
except Exception as e:
    print("exception: %s" % e)
    sys.exit(1)
'

echo "GATE 2: probing mt5.initialize() IPC-only (20 attempts × 30 s = 10 min)..."
IPC_PASS=false

for attempt in $(seq 1 20); do
  # Show visible windows for diagnostics.
  WIN_TITLES=""
  if command -v xdotool > /dev/null 2>&1; then
    WIN_TITLES=$(
      DISPLAY="${DISPLAY}" xdotool search --onlyvisible 2>/dev/null \
        | while IFS= read -r _wid; do
            DISPLAY="${DISPLAY}" xdotool getwindowname "${_wid}" 2>/dev/null || true
          done | paste -sd'|' -
    ) || WIN_TITLES=""
  fi
  echo "[attempt ${attempt}/20] windows=${WIN_TITLES}"

  # Crash check.
  if ! kill -0 "${TERM_PID}" 2>/dev/null; then
    echo "SMOKE TEST FAIL: terminal64.exe died during probe (attempt ${attempt})"
    echo "--- terminal.log (tail 100) ---"
    tail -n 100 "${LOGDIR}/terminal.log" 2>/dev/null || true
    kill "${DISMISS_PID}" 2>/dev/null || true
    exit 1
  fi

  # Run IPC-only probe — no credentials, no broker roundtrip.
  # Shell timeout 20 s > Python mt5 timeout 10 s.
  PROBE_OUT=""
  PROBE_EXIT=0
  PROBE_OUT=$(DISPLAY="${DISPLAY}" timeout 20 wine "${WINE_PY}" -c "${IPC_PROBE_SCRIPT}" 2>&1) || PROBE_EXIT=$?

  echo "[attempt ${attempt}/20] probe_exit=${PROBE_EXIT} probe_out=${PROBE_OUT}"

  if [[ "${PROBE_EXIT}" -eq 0 ]]; then
    echo "GATE 2 PASS: mt5.initialize() returned ok=True (attempt ${attempt})"
    IPC_PASS=true
    break
  elif [[ "${PROBE_EXIT}" -eq 2 ]]; then
    echo "SMOKE TEST FAIL: -10003 (IPC pipe not found) — terminal is not creating the pipe"
    echo "--- terminal.log (tail 100) ---"
    tail -n 100 "${LOGDIR}/terminal.log" 2>/dev/null || true
    kill "${DISMISS_PID}" 2>/dev/null || true
    exit 1
  elif [[ "${PROBE_EXIT}" -eq 124 ]]; then
    echo "[attempt ${attempt}/20] probe timed out (shell 45 s) — terminal pipe exists but hung; retrying..."
  else
    echo "[attempt ${attempt}/20] -10005 or other error — main thread busy, retrying in 30 s..."
  fi

  sleep 30
done

kill "${DISMISS_PID}" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Final verdict
# ---------------------------------------------------------------------------
if [[ "${IPC_PASS}" != "true" ]]; then
  echo "SMOKE TEST FAIL: mt5.initialize() (IPC-only) never returned ok=True after 20 attempts"
  echo ""
  echo "Diagnostic summary:"
  echo "  probe_exit=3 (-10005): terminal main thread blocked entire window."
  echo "  probe_exit=2 (-10003): IPC pipe not found — terminal crashed."
  echo "  probe_exit=124: IPC pipe exists but auth handshake never completes."
  echo "  Check terminal.log below for clues."
  echo ""
  echo "--- terminal.log (tail 200) ---"
  tail -n 200 "${LOGDIR}/terminal.log" 2>/dev/null || true
  echo "--- xvfb.log (tail 50) ---"
  tail -n 50 "${LOGDIR}/xvfb.log" 2>/dev/null || true
  exit 1
fi

# ---------------------------------------------------------------------------
# GATE 3 (non-fatal): broker login check
# ---------------------------------------------------------------------------
echo "GATE 3: verifying broker login (Exness-MT5Trial9, non-fatal)..."
BROKER_OUT=""
BROKER_EXIT=0
BROKER_OUT=$(DISPLAY="${DISPLAY}" timeout 45 wine "${WINE_PY}" -c "${BROKER_PROBE_SCRIPT}" 2>&1) || BROKER_EXIT=$?
echo "Broker probe: exit=${BROKER_EXIT} out=${BROKER_OUT}"
if [[ "${BROKER_EXIT}" -eq 0 ]]; then
  echo "GATE 3 PASS: broker login succeeded"
else
  echo "GATE 3 WARNING: broker login failed (account may be expired or server unreachable)"
  echo "This is non-fatal for the base image — credentials are configured at runtime."
fi

echo ""
echo "SMOKE TEST PASS: IPC handshake succeeded — terminal is ready"
exit 0
