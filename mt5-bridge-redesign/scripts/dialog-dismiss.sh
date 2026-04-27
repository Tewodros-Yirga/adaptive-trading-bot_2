#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
export LOGDIR="${LOGDIR:-/var/log/mt5}"

mkdir -p "${LOGDIR}"
DISMISS_LOG="${LOGDIR}/dismiss.log"

_ids() {
  xdotool search --onlyvisible --name "LiveUpdate" 2>/dev/null || true
  xdotool search --onlyvisible --name "Welcome to" 2>/dev/null || true
  xdotool search --onlyvisible --name "Select a company" 2>/dev/null || true
  xdotool search --onlyvisible --name "Login" 2>/dev/null || true
  xdotool search --onlyvisible --name "Setup" 2>/dev/null || true
}

while true; do
  IDS="$(_ids | awk '/^[0-9]+$/' | sort -n -u)"
  CNT="$(printf '%s\n' ${IDS:-} | awk 'NF' | wc -l)"
  echo "[dismiss heartbeat] ids=${CNT}" >> "${DISMISS_LOG}"

  for wid in ${IDS:-}; do
    name="$(xdotool getwindowname "${wid}" 2>/dev/null || echo '?')"
    echo "[dismiss action] wid=${wid} name=${name}" >> "${DISMISS_LOG}"
    xdotool windowactivate --sync "${wid}" 2>/dev/null || true
    xdotool key --window "${wid}" --clearmodifiers Escape 2>/dev/null || true
    sleep 0.15
    xdotool key --window "${wid}" --clearmodifiers Return 2>/dev/null || true
    sleep 0.15
    eval "$(xdotool getwindowgeometry --shell "${wid}" 2>/dev/null || true)"
    if [[ -n "${WIDTH:-}" && -n "${HEIGHT:-}" ]]; then
      bx=$(( X + WIDTH * 75 / 100 ))
      by=$(( Y + HEIGHT * 85 / 100 ))
      xdotool mousemove "${bx}" "${by}" click 1 2>/dev/null || true
    fi
  done
  sleep 3
done
