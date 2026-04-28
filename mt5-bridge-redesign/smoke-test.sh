#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
export WINEPREFIX="${WINEPREFIX:-/opt/wineprefix}"
export LOGDIR="${LOGDIR:-/var/log/mt5}"
export BRIDGE_PORT="${BRIDGE_PORT:-18812}"

BOOTSTRAP_READY="${LOGDIR}/bootstrap.ready"
TERM_PATH_FILE="${LOGDIR}/mt5_terminal_exe.path"
PY_PATH_FILE="${LOGDIR}/wine_python_exe.path"
DISMISS_LOG="${LOGDIR}/dismiss.log"
TERM_LOG="${LOGDIR}/terminal.log"
WS_LOG="${LOGDIR}/wineserver-timeline.log"

echo "Running redesign smoke test..."

for i in $(seq 1 90); do
  if [[ -f "${BOOTSTRAP_READY}" ]]; then
    break
  fi
  sleep 2
done

if [[ ! -f "${BOOTSTRAP_READY}" ]]; then
  echo "FAIL: bootstrap.ready missing"
  exit 1
fi

TERM_EXE="$(tr -d '\r\n' < "${TERM_PATH_FILE}")"
WINE_PY="$(tr -d '\r\n' < "${PY_PATH_FILE}")"

# Gate 1: terminal process alive
TERM_PID="$(pgrep -f -n "terminal64.exe" || true)"
if [[ -z "${TERM_PID}" ]]; then
  echo "FAIL GATE 1: terminal64.exe not running"
  tail -n 80 "${TERM_LOG}" 2>/dev/null || true
  exit 1
fi
echo "PASS GATE 1: terminal alive pid=${TERM_PID}"

# Gate 2: external TCP connectivity from Wine/terminal namespace
TCP_OK=false
for attempt in $(seq 1 20); do
  ESTABLISHED="$(ss -tn state established 2>/dev/null | tail -n +2 | grep -v '127\.' | grep -v ' ::1[: ]' | wc -l || true)"
  echo "[gate2 ${attempt}/20] established_external_tcp=${ESTABLISHED}"
  if [[ "${ESTABLISHED}" -gt 0 ]]; then
    TCP_OK=true
    break
  fi
  sleep 15
done

if [[ "${TCP_OK}" != "true" ]]; then
  echo "FAIL GATE 2: no external TCP connectivity"
  echo "--- dismiss.log tail ---"
  tail -n 80 "${DISMISS_LOG}" 2>/dev/null || true
  echo "--- terminal.log tail ---"
  tail -n 120 "${TERM_LOG}" 2>/dev/null || true
  echo "--- wineserver timeline tail ---"
  tail -n 40 "${WS_LOG}" 2>/dev/null || true
  exit 1
fi
echo "PASS GATE 2: external TCP detected"

# Gate 3: mt5linux RPC diagnostic (non-fatal)
RPC_OK=false
for _ in $(seq 1 30); do
  if (echo > /dev/tcp/127.0.0.1/"${BRIDGE_PORT}") >/dev/null 2>&1; then
    RPC_OK=true
    break
  fi
  sleep 1
done
echo "GATE 3: mt5linux port ${BRIDGE_PORT} open=${RPC_OK}"

RPC_OUT="$(timeout 60 wine "${WINE_PY}" - <<'PY' 2>&1 || true
from mt5linux import MetaTrader5
import os

port = int(os.environ.get("BRIDGE_PORT", "18812"))
mt5 = MetaTrader5(host="127.0.0.1", port=port)
ok = mt5.initialize()
print(f"initialize_ok={ok}")
if ok:
    try:
        print(f"account_info={mt5.account_info()}")
    finally:
        mt5.shutdown()
PY
)"
echo "GATE 3 RPC output: ${RPC_OUT}"

echo "SMOKE PASS: redesign stack healthy enough (GATE 1 + GATE 2)"
exit 0
