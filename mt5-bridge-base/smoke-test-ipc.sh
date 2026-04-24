#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
export WINEPREFIX="${WINEPREFIX:-/opt/wineprefix}"
export WINEDEBUG="${WINEDEBUG:--all}"
export HOME="${HOME:-/root}"

# Force Mesa software rasterisation — GHA runners have no GPU / DRI3.
export LIBGL_ALWAYS_SOFTWARE=1
export GALLIUM_DRIVER=llvmpipe
# Do NOT override MESA_GL_VERSION / MESA_GLSL_VERSION — Mesa version overrides
# cause Wine to crash silently on headless GHA runners (confirmed in prior builds).
export WINEESYNC=0
export WINEFSYNC=0

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

# Check whether this image has a pre-baked AppData (written by the Dockerfile
# first-run step).  If present, the terminal will skip the wizard entirely.
APPDATA_MT5="${WINEPREFIX}/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal"
if [[ -d "${APPDATA_MT5}" ]]; then
  echo "INFO: Pre-baked AppData detected at ${APPDATA_MT5}"
  find "${APPDATA_MT5}" -maxdepth 3 -type d 2>/dev/null | head -20 || true
else
  echo "WARNING: No pre-baked AppData found — wizard may appear on startup"
fi

# Block MT5 update/LiveUpdate domains before launching terminal.
for _d in live.mql5.com updates.mql5.com update.mql5.com download.mql5.com \
          cdn.mql5.com ec.mql5.com files.mql5.com www.mql5.com mql5.com \
          update.metatrader5.com updates.metatrader5.com \
          mt5-update.metaquotes.net metaquotes.net; do
  grep -qF "${_d}" /etc/hosts 2>/dev/null || \
    echo "0.0.0.0 ${_d}" >> /etc/hosts 2>/dev/null || true
done
unset _d

rm -f /tmp/.X99-lock || true
Xvfb "${DISPLAY}" -screen 0 1280x720x24 > "${LOGDIR}/xvfb.log" 2>&1 &
XVFB_PID=$!
sleep 3
if ! kill -0 "${XVFB_PID}" 2>/dev/null; then
  echo "ERROR: failed to start Xvfb"
  exit 1
fi

# A window manager is required for reliable focus/input delivery to Wine dialogs.
if command -v openbox >/dev/null 2>&1; then
  DISPLAY="${DISPLAY}" openbox --sm-disable > "${LOGDIR}/openbox.log" 2>&1 &
  OPENBOX_PID=$!
  sleep 2
else
  OPENBOX_PID=""
fi

cleanup() {
  kill "${TERM_PID:-}" 2>/dev/null || true
  kill "${OPENBOX_PID:-}" 2>/dev/null || true
  kill "${XVFB_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Pre-write MetaQuotes-Demo server config so the terminal skips the wizard.
# Write to BOTH the generic Common path and any hash-subdirectory already
# present from the pre-baked AppData layer.
# ---------------------------------------------------------------------------
_write_cfg() {
  local _d="$1"
  mkdir -p "${_d}" 2>/dev/null || return
  printf '[Common]\r\nLogin=\r\nServer=MetaQuotes-Demo\r\nNewsEnable=0\r\nAutoSync=0\r\n\r\n[Experts]\r\nEnabled=1\r\nAllowLiveTrading=1\r\n' \
    > "${_d}/common.ini" 2>/dev/null || true
  echo "Wrote server config: ${_d}/common.ini"
}

_write_cfg "${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/config"
_write_cfg "${WINEPREFIX}/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal/Common/config"

# Write to every hash-subdirectory already in AppData (from pre-baked layer).
find "${APPDATA_MT5}" -mindepth 2 -maxdepth 2 -type d -name "config" 2>/dev/null \
  | while IFS= read -r _hcfg; do
      _write_cfg "${_hcfg}"
    done || true

unset -f _write_cfg

# Write a Windows-accessible config file for the /config: launch flag.
WIN_CFG_LINUX="${WINEPREFIX}/drive_c/mt5-headless.ini"
printf '[Common]\r\nServer=MetaQuotes-Demo\r\nNewsEnable=0\r\nAutoSync=0\r\n\r\n[Experts]\r\nEnabled=1\r\nAllowLiveTrading=1\r\n' \
  > "${WIN_CFG_LINUX}" 2>/dev/null || true
echo "Wrote Windows-accessible config: ${WIN_CFG_LINUX}"

# Override WINEDEBUG for the terminal only so crash reasons appear in terminal.log.
# WINEESYNC/WINEFSYNC=0 are inherited from ENV; explicit here for clarity.
WINEDEBUG="err+all" WINEESYNC=0 WINEFSYNC=0 \
  wine "${TERM_EXE}" \
    /config:"C:\\mt5-headless.ini" \
    > "${LOGDIR}/terminal.log" 2>&1 &
TERM_PID=$!

# Wait longer on first start — terminal may apply pending updates before
# creating IPC pipes, even with update domains blocked (wineserver DNS cache).
echo "Waiting 60 s for terminal to initialise..."
sleep 60

# Diagnose early exit — if the process is already dead the crash reason is in terminal.log.
if ! kill -0 "${TERM_PID}" 2>/dev/null; then
  echo "WARNING: terminal64.exe exited within 60s (PID ${TERM_PID} is gone)"
  echo "--- terminal.log early-exit dump ---"
  cat "${LOGDIR}/terminal.log" 2>/dev/null || echo "(empty)"
fi

# Best-effort dialog dismisser for LiveUpdate/first-run wizards that block IPC.
# Runs in parallel for the entire probe window.
(
  if ! command -v xdotool >/dev/null 2>&1; then
    exit 0
  fi
  _xd() { DISPLAY="${DISPLAY}" xdotool "$@" 2>/dev/null || true; }
  sleep 5
  for _iter in $(seq 1 120); do
    IDS=$(
      { _xd search --onlyvisible --name "LiveUpdate";
        _xd search --onlyvisible --name "Select a company";
        _xd search --onlyvisible --name "Welcome to";
        _xd search --onlyvisible --name "MetaTrader 5";
        _xd search --onlyvisible --name "MetaTrader";
        _xd search --onlyvisible --name "Setup";
      } | awk 'NF' | sort -n -u
    )
    ALL_IDS=$(_xd search --onlyvisible | awk 'NF' | sort -n -u)
    WORK_IDS="$(printf '%s\n%s\n' "${IDS}" "${ALL_IDS}" | awk 'NF' | sort -n -u)"
    for _wid in ${WORK_IDS}; do
      _xd windowactivate --sync "${_wid}"
      sleep 0.25
      # Click broker list item rows first (Next is disabled until one is selected).
      for _xy in "400 210" "500 210" "640 210" \
                 "400 245" "500 245" "640 245" \
                 "400 275" "500 275" "640 275"; do
        set -- ${_xy}
        _xd mousemove --clearmodifiers "$1" "$2" click 1
        sleep 0.06
      done
      # Double-click first list item.
      _xd mousemove --clearmodifiers "400" "245" click 1; sleep 0.05
      _xd mousemove --clearmodifiers "400" "245" click 1; sleep 0.1
      # Click Next/OK/Restart/Later rows.
      for _xy in "638 418" "724 488" "640 520" "560 332" "469 335" "520 402"; do
        set -- ${_xy}
        _xd mousemove --clearmodifiers "$1" "$2" click 1
        sleep 0.06
      done
      _xd key --window "${_wid}" --clearmodifiers Return; sleep 0.1
      _xd key --window "${_wid}" --clearmodifiers Escape; sleep 0.1
      _xd key --window "${_wid}" --clearmodifiers Return; sleep 0.06
    done
    sleep 3
  done
) &
DISMISS_PID=$!

# ---------------------------------------------------------------------------
# Probe loop: try mt5.initialize() until ok=True or attempts exhausted.
# Using 30 attempts × 10 s sleep = ~5 min probe window.
# Per-attempt Wine timeout = 45 s; mt5 IPC timeout = 30 000 ms.
# ---------------------------------------------------------------------------
for attempt in $(seq 1 30); do
  WIN_TITLES=""
  if command -v xdotool >/dev/null 2>&1; then
    WIN_TITLES=$(
      DISPLAY="${DISPLAY}" xdotool search --onlyvisible 2>/dev/null \
        | while IFS= read -r _wid; do
            DISPLAY="${DISPLAY}" xdotool getwindowname "${_wid}" 2>/dev/null || true
          done | paste -sd'|' - 2>/dev/null
    ) || WIN_TITLES=""
  fi
  echo "[attempt ${attempt}/30] windows=${WIN_TITLES}"

  # Kill any orphaned Wine-side python.exe from the previous probe attempt.
  WINEDEBUG="-all" wine taskkill /F /IM python.exe > /dev/null 2>&1 || true
  pkill -f "wine.*python.*-c" > /dev/null 2>&1 || true
  sleep 1

  for mode in default portable; do
    PORTABLE_FLAG="False"
    if [[ "${mode}" == "portable" ]]; then
      PORTABLE_FLAG="True"
    fi
    OUT_FILE="${LOGDIR}/probe-${attempt}-${mode}.log"
    rm -f "${OUT_FILE}" 2>/dev/null || true
    WINEDEBUG="-all" timeout 45 wine "${WINE_PY}" -c "
import MetaTrader5 as mt5
portable = ${PORTABLE_FLAG}
ok = False
try:
    ok = mt5.initialize(timeout=30000, portable=portable)
except Exception:
    ok = False
err = mt5.last_error()
mt5.shutdown()
print(f'ok={ok} portable={portable} err={err}')
" > "${OUT_FILE}" 2>&1 || true
    OUT="$(tr -d '\r' < "${OUT_FILE}" 2>/dev/null || true)"
    echo "[attempt ${attempt}] mode=${mode} ${OUT}"
    if [[ "${OUT}" == *"ok=True"* ]]; then
      echo "SMOKE TEST PASS: IPC attach succeeded (attempt=${attempt} mode=${mode})"
      kill "${DISMISS_PID}" 2>/dev/null || true
      exit 0
    fi
  done
  sleep 10
done

kill "${DISMISS_PID}" 2>/dev/null || true

echo "SMOKE TEST FAIL: IPC attach never succeeded after 30 attempts"
echo "--- terminal.log (tail 200) ---"
tail -n 200 "${LOGDIR}/terminal.log" 2>/dev/null || true
echo "--- xvfb.log (tail 100) ---"
tail -n 100 "${LOGDIR}/xvfb.log" 2>/dev/null || true
if [[ -n "${OPENBOX_PID}" ]]; then
  echo "--- openbox.log (tail 50) ---"
  tail -n 50 "${LOGDIR}/openbox.log" 2>/dev/null || true
fi
exit 1
