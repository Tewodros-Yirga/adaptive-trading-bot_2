#!/usr/bin/env bash
set -euo pipefail

export DISPLAY=${DISPLAY:-:99}
export WINEPREFIX=${WINEPREFIX:-/tmp/.wine}
export MT_TERMINAL_EXE=${MT_TERMINAL_EXE:-${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/terminal64.exe}

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

if [[ -n "${MT5_INSTALLER_URL:-}" && ! -f "$MT_TERMINAL_EXE" ]]; then
  echo "Bootstrap: downloading MT5 installer..."
  curl -L "$MT5_INSTALLER_URL" -o /tmp/mt5/mt5setup.exe

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

  echo "Bootstrap: running MT5 installer..."
  "$WINE_CMD" /tmp/mt5/mt5setup.exe /silent || true
fi

if [[ -f "$MT_TERMINAL_EXE" ]]; then
  echo "Bootstrap: MT5 terminal detected at $MT_TERMINAL_EXE"
else
  echo "Bootstrap: MT5 terminal not found at $MT_TERMINAL_EXE"
fi

