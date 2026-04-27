#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
export WINEPREFIX="${WINEPREFIX:-/opt/wineprefix}"
export LOGDIR="${LOGDIR:-/var/log/mt5}"
export BRIDGE_PORT="${BRIDGE_PORT:-18812}"
export VNC_PORT="${VNC_PORT:-5900}"
export NOVNC_PORT="${NOVNC_PORT:-6080}"

mkdir -p "${LOGDIR}" /tmp/supervisor

echo "[start] redesign stack starting"
echo "[start] DISPLAY=${DISPLAY} WINEPREFIX=${WINEPREFIX} LOGDIR=${LOGDIR}"

exec /usr/bin/supervisord -c /opt/mt5-redesign/supervisord.conf
