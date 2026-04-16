#!/usr/bin/env bash
set -euo pipefail

export DISPLAY=${DISPLAY:-:99}
export WINEPREFIX="/opt/wineprefix"
DERIVED_TERMINAL_EXE="${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/terminal64.exe"
export MT_TERMINAL_EXE="${MT_TERMINAL_EXE:-$DERIVED_TERMINAL_EXE}"
export PYTHON_WIN_INSTALLER_URL=${PYTHON_WIN_INSTALLER_URL:-https://www.python.org/ftp/python/3.9.13/python-3.9.13.exe}

# Keep large downloads and temp files out of /tmp (Render eviction limit).
export MT5_WORKDIR=${MT5_WORKDIR:-/opt/mt5-work}
export LOGDIR=${LOGDIR:-/opt/mt5-bridge-logs}
export TMPDIR=${TMPDIR:-${MT5_WORKDIR}/tmp}
mkdir -p "${MT5_WORKDIR}/dl" "${TMPDIR}" "${LOGDIR}"

# If Render (or an old deploy) provides the wrong prefix path,
# force the executable path to match WINEPREFIX (/opt/wineprefix).
if [[ ( "${MT_TERMINAL_EXE}" == /root/.wine/* || "${MT_TERMINAL_EXE}" == /tmp/.wine/* || "${MT_TERMINAL_EXE}" == /bridge/.wine/* ) && "${WINEPREFIX}" != /root/.wine* && "${WINEPREFIX}" != /tmp/.wine* && "${WINEPREFIX}" != /bridge/.wine* ]]; then
  export MT_TERMINAL_EXE="$DERIVED_TERMINAL_EXE"
fi

mkdir -p "${WINEPREFIX}"

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

python_ok=false

# Validate that Wine's stdlib is usable (encodings must exist).
if "$WINE_CMD" python --version >/dev/null 2>&1; then
  "$WINE_CMD" python -c "import encodings; print('encodings_ok')" >"${LOGDIR}/python-encodings-check.log" 2>&1 && python_ok=true || true
fi

if [[ "${python_ok}" != "true" ]]; then
  echo "Bootstrap: Wine Python missing or broken; installing Python..."

  # Remove likely broken python installs (best-effort).
  rm -rf "${WINEPREFIX}/drive_c/Python"* >/dev/null 2>&1 || true
  rm -rf "${WINEPREFIX}/drive_c/Program Files/Python"* >/dev/null 2>&1 || true
  rm -rf "${WINEPREFIX}/drive_c/users/"*/AppData/Local/Programs/Python* >/dev/null 2>&1 || true

  curl -L "${PYTHON_WIN_INSTALLER_URL}" -o "${MT5_WORKDIR}/dl/python-installer.exe"
  if [[ ! -f "${MT5_WORKDIR}/dl/python-installer.exe" ]]; then
    echo "Bootstrap: python-installer.exe download failed (missing file)" >&2
    exit 1
  fi

  # Use the same kind of flags as known-good Wine setups:
  # - install for all users
  # - prepend Python to PATH inside Wine so `wine python` works
  "$WINE_CMD" "${MT5_WORKDIR}/dl/python-installer.exe" /quiet InstallAllUsers=1 PrependPath=1 > "${LOGDIR}/python-installer.log" 2>&1 || true

  # Wait for Python to become usable.
  for _ in $(seq 1 90); do
    if "$WINE_CMD" python -c "import encodings; print('encodings_ok')" >"${LOGDIR}/python-encodings-check.log" 2>&1; then
      python_ok=true
      break
    fi
    sleep 2
  done
fi

if [[ "${python_ok}" != "true" ]]; then
  echo "Bootstrap: Wine Python still broken after install attempt." >&2
  echo "Bootstrap: python-installer log tail:" >&2
  tail -n 200 "${LOGDIR}/python-installer.log" >&2 || true
  echo "Bootstrap: encodings-check log tail:" >&2
  tail -n 200 "${LOGDIR}/python-encodings-check.log" >&2 || true
  exit 1
fi

# Install required Wine-side Python libraries.
echo "Bootstrap: Installing Wine Python packages (mt5linux + MetaTrader5)..."
"$WINE_CMD" python -m pip install --upgrade --no-cache-dir pip >"${LOGDIR}/wine-pip-upgrade.log" 2>&1 || true
"$WINE_CMD" python -m pip install --no-cache-dir MetaTrader5 >"${LOGDIR}/wine-metatrader5-pip-install.log" 2>&1 || true
"$WINE_CMD" python -m pip install --no-cache-dir "mt5linux>=0.1.9" >"${LOGDIR}/wine-mt5linux-pip-install.log" 2>&1 || true
"$WINE_CMD" python -m pip install --no-cache-dir python-dateutil >"${LOGDIR}/wine-python-dateutil-pip-install.log" 2>&1 || true

# Cleanup downloaded installers to keep /tmp small on Render.
rm -f "${MT5_WORKDIR}/dl/python-installer.exe" >/dev/null 2>&1 || true

if [[ -n "${MT5_INSTALLER_URL:-}" && ! -f "$MT_TERMINAL_EXE" ]]; then
  echo "Bootstrap: downloading MT5 installer..."
  curl -L "$MT5_INSTALLER_URL" -o "${MT5_WORKDIR}/dl/mt5setup.exe"

  echo "Bootstrap: running MT5 installer..."
  "$WINE_CMD" "${MT5_WORKDIR}/dl/mt5setup.exe" /silent || true
fi

if [[ -f "$MT_TERMINAL_EXE" ]]; then
  echo "Bootstrap: MT5 terminal detected at $MT_TERMINAL_EXE"
else
  echo "Bootstrap: MT5 terminal not found at $MT_TERMINAL_EXE"
fi

# Cleanup downloaded MT5 installer to keep /tmp small on Render.
rm -f "${MT5_WORKDIR}/dl/mt5setup.exe" >/dev/null 2>&1 || true

