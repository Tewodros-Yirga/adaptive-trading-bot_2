#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
export WINEPREFIX="${WINEPREFIX:-/opt/wineprefix}"
export WINEDEBUG="${WINEDEBUG:--all}"
export HOME="${HOME:-/root}"

# Force Mesa software rasterisation — GHA runners have no GPU / DRI3.
export LIBGL_ALWAYS_SOFTWARE=1
export GALLIUM_DRIVER=llvmpipe
export MESA_GL_VERSION_OVERRIDE=4.5
export MESA_GLSL_VERSION_OVERRIDE=450

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

# Block MT5 update/LiveUpdate domains before launching terminal.
for _d in live.mql5.com updates.mql5.com update.mql5.com download.mql5.com \
          cdn.mql5.com update.metatrader5.com updates.metatrader5.com \
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
else
  OPENBOX_PID=""
fi

cleanup() {
  kill "${TERM_PID:-}" 2>/dev/null || true
  kill "${OPENBOX_PID:-}" 2>/dev/null || true
  kill "${XVFB_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT

# /portable skips the broker-selection wizard that blocks IPC on first launch.
wine "${TERM_EXE}" /portable > "${LOGDIR}/terminal.log" 2>&1 &
TERM_PID=$!
sleep 30

# Best-effort dialog dismisser for LiveUpdate/first-run wizards that block IPC.
(
  if ! command -v xdotool >/dev/null 2>&1; then
    exit 0
  fi
  _xd() { DISPLAY="${DISPLAY}" xdotool "$@" 2>/dev/null || true; }
  sleep 5
  for _ in $(seq 1 90); do
    IDS=$(
      { _xd search --onlyvisible --name LiveUpdate;
        _xd search --onlyvisible --name "Select a company";
        _xd search --onlyvisible --name "Welcome to";
        _xd search --onlyvisible --name "MetaTrader 5";
      } | awk 'NF' | sort -n -u
    )
    for wid in ${IDS}; do
      _xd windowactivate --sync "${wid}"
      sleep 0.2
      # Click common button/list locations.
      for xy in "400 245" "520 402" "638 418" "724 488" "640 520"; do
        set -- ${xy}
        _xd mousemove --clearmodifiers "$1" "$2" click 1
        sleep 0.08
      done
      _xd key --window "${wid}" --clearmodifiers Return
      _xd key --window "${wid}" --clearmodifiers Escape
    done
    sleep 3
  done
) &

for attempt in $(seq 1 20); do
  WIN_TITLES=""
  if command -v xdotool >/dev/null 2>&1; then
    WIN_TITLES=$(
      DISPLAY="${DISPLAY}" xdotool search --onlyvisible 2>/dev/null \
        | while IFS= read -r wid; do
            DISPLAY="${DISPLAY}" xdotool getwindowname "${wid}" 2>/dev/null || true
          done | paste -sd'|' - 2>/dev/null
    ) || WIN_TITLES=""
  fi
  echo "[attempt ${attempt}] windows=${WIN_TITLES}"
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
echo "--- terminal.log (tail) ---"
tail -n 200 "${LOGDIR}/terminal.log" 2>/dev/null || true
echo "--- xvfb.log (tail) ---"
tail -n 100 "${LOGDIR}/xvfb.log" 2>/dev/null || true
if [[ -n "${OPENBOX_PID}" ]]; then
  echo "--- openbox.log (tail) ---"
  tail -n 100 "${LOGDIR}/openbox.log" 2>/dev/null || true
fi
exit 1
