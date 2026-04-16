#!/usr/bin/env bash
set -euo pipefail

export DISPLAY=${DISPLAY:-:99}
export WINEPREFIX=${WINEPREFIX:-/tmp/.wine}
DERIVED_TERMINAL_EXE="${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/terminal64.exe"
export MT_TERMINAL_EXE="${MT_TERMINAL_EXE:-$DERIVED_TERMINAL_EXE}"
export PYTHON_WIN_INSTALLER_URL=${PYTHON_WIN_INSTALLER_URL:-https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe}

# If Render (or an old deploy) provides the wrong prefix path (e.g. /root/.wine),
# force the executable path to match WINEPREFIX (/tmp/.wine).
if [[ "${MT_TERMINAL_EXE}" == /root/.wine/* && "${WINEPREFIX}" != /root/.wine* ]]; then
  export MT_TERMINAL_EXE="$DERIVED_TERMINAL_EXE"
fi

mkdir -p "${WINEPREFIX}" /tmp/mt5

echo "Bootstrap: preparing Wine prefix..."
WINEBOOT_CMD=""
if command -v wineboot >/dev/null 2>&1; then
  WINEBOOT_CMD="wineboot"
elif command -v wine64boot >/dev/null 2>&1; then
  WINEBOOT_CMD="wine64boot"
elif command -v wineboot64 >/dev/null 2>&1; then
  WINEBOOT_CMD="wineboot64"
fi

if [[ -z "$WINEBOOT_CMD" ]]; then
  echo "Bootstrap: wineboot command not found." >&2
  exit 1
fi

"$WINEBOOT_CMD" --init || true

WINE_CMD=""
if command -v wine >/dev/null 2>&1; then
  WINE_CMD="wine"
elif command -v wine64 >/dev/null 2>&1; then
  WINE_CMD="wine64"
fi

if [[ -z "$WINE_CMD" ]]; then
  echo "Bootstrap: wine command not found." >&2
  exit 1
fi

resolve_windows_python() {
  local candidates=(
    "${WINEPREFIX}/drive_c/users/root/AppData/Local/Programs/Python/Python312/python.exe"
    "${WINEPREFIX}/drive_c/users/wineuser/AppData/Local/Programs/Python/Python312/python.exe"
    "${WINEPREFIX}/drive_c/Program Files/Python312/python.exe"
    "${WINEPREFIX}/drive_c/Python312/python.exe"
  )
  local c
  for c in "${candidates[@]}"; do
    if [[ -f "$c" ]]; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

PYTHON_WIN_EXE="$(resolve_windows_python || true)"
if [[ -z "${PYTHON_WIN_EXE}" ]]; then
  echo "Bootstrap: Windows Python not found in Wine prefix; installing..."
  curl -L "${PYTHON_WIN_INSTALLER_URL}" -o /tmp/mt5/python-installer.exe
  "$WINE_CMD" /tmp/mt5/python-installer.exe /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 SimpleInstall=1 || true
  for _ in $(seq 1 60); do
    PYTHON_WIN_EXE="$(resolve_windows_python || true)"
    if [[ -n "${PYTHON_WIN_EXE}" ]]; then
      break
    fi
    sleep 2
  done
fi

if [[ -n "${PYTHON_WIN_EXE}" ]]; then
  echo "Bootstrap: Windows Python detected at ${PYTHON_WIN_EXE}"
else
  echo "Bootstrap: Windows Python still not found after install attempt." >&2
fi

if [[ -n "${MT5_INSTALLER_URL:-}" && ! -f "$MT_TERMINAL_EXE" ]]; then
  echo "Bootstrap: downloading MT5 installer..."
  curl -L "$MT5_INSTALLER_URL" -o /tmp/mt5/mt5setup.exe

  echo "Bootstrap: running MT5 installer..."
  "$WINE_CMD" /tmp/mt5/mt5setup.exe /silent || true
fi

if [[ -f "$MT_TERMINAL_EXE" ]]; then
  echo "Bootstrap: MT5 terminal detected at $MT_TERMINAL_EXE"
else
  echo "Bootstrap: MT5 terminal not found at $MT_TERMINAL_EXE"
fi

