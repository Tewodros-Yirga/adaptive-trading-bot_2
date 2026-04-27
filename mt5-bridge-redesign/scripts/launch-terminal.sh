#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
export WINEPREFIX="${WINEPREFIX:-/opt/wineprefix}"
export LOGDIR="${LOGDIR:-/var/log/mt5}"

mkdir -p "${LOGDIR}"

READY_FILE="${LOGDIR}/bootstrap.ready"
TERM_PATH_FILE="${LOGDIR}/mt5_terminal_exe.path"
TERM_LOG="${LOGDIR}/terminal.log"
WS_LOG="${LOGDIR}/wineserver-timeline.log"

log() { echo "[terminal $(date -u +%H:%M:%S)] $*" | tee -a "${TERM_LOG}" >/dev/null; }

_ws_pids() { pgrep -fa wineserver 2>/dev/null | awk '{print $1}' | tr '\n' ',' | sed 's/,$//'; }
_log_ws() {
  local tag="$1"
  local stamp
  stamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "[${stamp}] tag=${tag} wineserver_pids=$(_ws_pids)" >> "${WS_LOG}" 2>/dev/null || true
}

for _ in $(seq 1 90); do
  if [[ -f "${READY_FILE}" ]]; then
    break
  fi
  sleep 2
done

if [[ ! -f "${READY_FILE}" ]]; then
  log "ERROR: bootstrap.ready not found"
  exit 1
fi

TERM_EXE="$(tr -d '\r\n' < "${TERM_PATH_FILE}")"
if [[ -z "${TERM_EXE}" ]] || [[ ! -f "${TERM_EXE}" ]]; then
  log "ERROR: terminal exe not found at '${TERM_EXE}'"
  exit 1
fi

TERM_DIR="$(dirname "${TERM_EXE}")"

# Common startup hardening seen in successful Wine deployments.
printf '[Startup]\r\nAutoStart=0\r\n\r\n[Common]\r\nAutoUpdate=0\r\n\r\n[LiveUpdate]\r\nEnabled=0\r\nNextUpdate=9999999999\r\n\r\n[Experts]\r\nEnabled=1\r\nAllowLiveTrading=1\r\n' \
  > "${TERM_DIR}/terminal.ini" 2>/dev/null || true

for _d in live.mql5.com updates.mql5.com update.mql5.com download.mql5.com \
          cdn.mql5.com update.metatrader5.com updates.metatrader5.com \
          mt5-update.metaquotes.net metaquotes.net; do
  grep -qF "${_d}" /etc/hosts 2>/dev/null || echo "0.0.0.0 ${_d}" >> /etc/hosts 2>/dev/null || true
done

WINE_CMD="wine"
if ! command -v wine >/dev/null 2>&1; then
  WINE_CMD="wine64"
fi

_log_ws "before_terminal_launch"
log "Launching terminal: ${TERM_EXE}"
exec ${WINE_CMD} "${TERM_EXE}" /portable >> "${TERM_LOG}" 2>&1
