#!/usr/bin/env bash
set -euo pipefail

export DISPLAY=${DISPLAY:-:99}
export WINEPREFIX=${WINEPREFIX:-/bridge/.wine}
# Prevent Render/old deployments from putting the Wine prefix under /tmp (can be evicted).
if [[ "${WINEPREFIX}" == /tmp/.wine || "${WINEPREFIX}" == /tmp/.wine/* || "${WINEPREFIX}" == /root/.wine || "${WINEPREFIX}" == /root/.wine/* ]]; then
  export WINEPREFIX="/bridge/.wine"
fi
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
  shopt -s nullglob
  local hits=()

  # Most common layouts in Wine.
  hits+=("${WINEPREFIX}"/drive_c/users/*/AppData/Local/Programs/Python/Python*/python.exe)
  hits+=("${WINEPREFIX}"/drive_c/Program\ Files/Python*/python.exe)
  hits+=("${WINEPREFIX}"/drive_c/Python*/python.exe)

  local c
  for c in "${hits[@]}"; do
    if [[ -f "$c" ]]; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

PYTHON_WIN_EXE="$(resolve_windows_python || true)"
python_ok=false

if [[ -n "${PYTHON_WIN_EXE}" ]]; then
  # Validate that stdlib is usable (encodings must exist).
  "$WINE_CMD" "${PYTHON_WIN_EXE}" -c "import encodings; print('encodings_ok')" >/tmp/python-encodings-check.log 2>&1 || true
  if [[ $? -eq 0 ]]; then
    python_ok=true
  fi
fi

if [[ "${python_ok}" != "true" ]]; then
  echo "Bootstrap: Windows Python missing or broken (encodings check failed); installing to C:\\Python312..."
  curl -L "${PYTHON_WIN_INSTALLER_URL}" -o /tmp/mt5/python-installer.exe
  if [[ ! -f /tmp/mt5/python-installer.exe ]]; then
    echo "Bootstrap: python-installer.exe download failed (missing file)" >&2
    exit 1
  fi

  # Remove any partial python installs to avoid the broken state you saw.
  rm -rf "${WINEPREFIX}/drive_c/Python312" >/dev/null 2>&1 || true
  rm -rf "${WINEPREFIX}/drive_c/users" >/dev/null 2>&1 || true

  # Recreate the Wine user dir structure (the python installer sometimes expects it).
  mkdir -p "${WINEPREFIX}/drive_c/users" >/dev/null 2>&1 || true

  # Install Python into a deterministic location.
  "$WINE_CMD" /tmp/mt5/python-installer.exe /quiet InstallAllUsers=0 TargetDir=C:\\Python312 Include_pip=1 Include_launcher=1 PrependPath=0 > /tmp/python-installer.log 2>&1 || true

  # Wait for python.exe to appear.
  for _ in $(seq 1 90); do
    PYTHON_WIN_EXE="$(resolve_windows_python || true)"
    if [[ -n "${PYTHON_WIN_EXE}" ]]; then
      break
    fi
    sleep 2
  done

  if [[ -n "${PYTHON_WIN_EXE}" ]]; then
    echo "Bootstrap: Windows Python detected at ${PYTHON_WIN_EXE}"
    # Re-run encodings check.
    "$WINE_CMD" "${PYTHON_WIN_EXE}" -c "import encodings; print('encodings_ok')" >/tmp/python-encodings-check.log 2>&1 || true
    if [[ $? -eq 0 ]]; then
      python_ok=true
    fi
  fi
fi

if [[ "${python_ok}" != "true" ]]; then
  echo "Bootstrap: Windows Python still broken after install attempt." >&2
  echo "Bootstrap: python-installer log tail:" >&2
  tail -n 200 /tmp/python-installer.log >&2 || true
  echo "Bootstrap: encodings-check log tail:" >&2
  tail -n 200 /tmp/python-encodings-check.log >&2 || true
  exit 1
fi

# Cleanup downloaded installers to keep /tmp small on Render.
rm -f /tmp/mt5/python-installer.exe >/dev/null 2>&1 || true

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

# Cleanup downloaded MT5 installer to keep /tmp small on Render.
rm -f /tmp/mt5/mt5setup.exe >/dev/null 2>&1 || true

