#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
export WINEPREFIX="${WINEPREFIX:-/opt/wineprefix}"
export LOGDIR="${LOGDIR:-/var/log/mt5}"
export BRIDGE_PORT="${BRIDGE_PORT:-18812}"

mkdir -p "${LOGDIR}"
READY_FILE="${LOGDIR}/bootstrap.ready"
PY_PATH_FILE="${LOGDIR}/wine_python_exe.path"
MT5LINUX_LOG="${LOGDIR}/mt5linux.log"

for _ in $(seq 1 120); do
  if [[ -f "${READY_FILE}" ]]; then
    break
  fi
  sleep 2
done

if [[ ! -f "${READY_FILE}" ]]; then
  echo "[mt5linux] ERROR: bootstrap not ready" >> "${MT5LINUX_LOG}"
  exit 1
fi

WINE_PY="$(tr -d '\r\n' < "${PY_PATH_FILE}")"
if [[ -z "${WINE_PY}" ]] || [[ ! -f "${WINE_PY}" ]]; then
  echo "[mt5linux] ERROR: wine python not found at '${WINE_PY}'" >> "${MT5LINUX_LOG}"
  exit 1
fi

WINE_CMD="wine"
if ! command -v wine >/dev/null 2>&1; then
  WINE_CMD="wine64"
fi

echo "[mt5linux] Launching server on 127.0.0.1:${BRIDGE_PORT}" >> "${MT5LINUX_LOG}"
exec ${WINE_CMD} "${WINE_PY}" -m mt5linux --host 127.0.0.1 --port "${BRIDGE_PORT}" >> "${MT5LINUX_LOG}" 2>&1
