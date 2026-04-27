#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
export WINEPREFIX="${WINEPREFIX:-/opt/wineprefix}"
export LOGDIR="${LOGDIR:-/var/log/mt5}"

mkdir -p "${LOGDIR}"

STATUS_FILE="${LOGDIR}/bootstrap.status"
READY_FILE="${LOGDIR}/bootstrap.ready"
FAILED_FILE="${LOGDIR}/bootstrap.failed"
PY_PATH_FILE="${LOGDIR}/wine_python_exe.path"
TERM_PATH_FILE="${LOGDIR}/mt5_terminal_exe.path"

rm -f "${READY_FILE}" "${FAILED_FILE}"
echo "starting" > "${STATUS_FILE}"

log() { echo "[bootstrap $(date -u +%H:%M:%S)] $*"; }

WINE_CMD=""
if command -v wine > /dev/null 2>&1; then
  WINE_CMD="wine"
elif command -v wine64 > /dev/null 2>&1; then
  WINE_CMD="wine64"
fi

if [[ -z "${WINE_CMD}" ]]; then
  log "ERROR: no wine executable found"
  echo "failed: no_wine" > "${STATUS_FILE}"
  touch "${FAILED_FILE}"
  exit 1
fi

WINE_PY=""
if [[ -f "/opt/wine_python_exe.path" ]]; then
  WINE_PY="$(tr -d '\r\n' < /opt/wine_python_exe.path)"
fi
if [[ -z "${WINE_PY}" ]] || [[ ! -f "${WINE_PY}" ]]; then
  WINE_PY="$(find "${WINEPREFIX}/drive_c" -maxdepth 6 -name "python.exe" 2>/dev/null | head -1 || true)"
fi

TERM_EXE=""
if [[ -f "/opt/mt5_terminal_exe.path" ]]; then
  TERM_EXE="$(tr -d '\r\n' < /opt/mt5_terminal_exe.path)"
fi
if [[ -z "${TERM_EXE}" ]] || [[ ! -f "${TERM_EXE}" ]]; then
  TERM_EXE="$(find "${WINEPREFIX}/drive_c" -maxdepth 8 -name "terminal64.exe" 2>/dev/null | head -1 || true)"
fi

if [[ -z "${WINE_PY}" ]] || [[ ! -f "${WINE_PY}" ]]; then
  log "ERROR: Windows python.exe not found in Wine prefix"
  echo "failed: no_python" > "${STATUS_FILE}"
  touch "${FAILED_FILE}"
  exit 1
fi
if [[ -z "${TERM_EXE}" ]] || [[ ! -f "${TERM_EXE}" ]]; then
  log "ERROR: terminal64.exe not found in Wine prefix"
  echo "failed: no_terminal" > "${STATUS_FILE}"
  touch "${FAILED_FILE}"
  exit 1
fi

echo "${WINE_PY}" > "${PY_PATH_FILE}"
echo "${TERM_EXE}" > "${TERM_PATH_FILE}"

echo "python=${WINE_PY}" >> "${STATUS_FILE}"
echo "terminal=${TERM_EXE}" >> "${STATUS_FILE}"

if timeout 30 "${WINE_CMD}" "${WINE_PY}" -c "import MetaTrader5 as m; print(m.__version__)" >> "${LOGDIR}/bootstrap.log" 2>&1; then
  log "MetaTrader5 module import OK"
else
  log "ERROR: MetaTrader5 module import failed"
  echo "failed: mt5_import" > "${STATUS_FILE}"
  touch "${FAILED_FILE}"
  exit 1
fi

if timeout 30 "${WINE_CMD}" "${WINE_PY}" -c "import mt5linux; print('mt5linux_ok')" >> "${LOGDIR}/bootstrap.log" 2>&1; then
  log "mt5linux import OK"
else
  log "ERROR: mt5linux import failed"
  echo "failed: mt5linux_import" > "${STATUS_FILE}"
  touch "${FAILED_FILE}"
  exit 1
fi

echo "ready" > "${STATUS_FILE}"
touch "${READY_FILE}"
log "Bootstrap ready"
