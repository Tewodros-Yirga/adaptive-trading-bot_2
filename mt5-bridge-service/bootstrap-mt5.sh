#!/usr/bin/env bash
# Bootstrap script — runs in the background at container startup.
#
# When using the pre-built base image (ghcr.io/loriloha/mt5-bridge-base):
#   - Wine prefix, Windows Python, MetaTrader5, mt5linux are ALL pre-baked.
#   - This script only handles MT5 terminal install (if not baked into image).
#   - Writes bootstrap.ready immediately if everything is already present.
#
# When falling back to scottyhardy/docker-wine (cold start):
#   - This script installs Wine Python + pip packages from scratch.
#   - Total time: ~15-25 minutes.

set -uo pipefail

export DISPLAY=${DISPLAY:-:99}
export HOME="${HOME:-/home/wineuser}"
export WINEPREFIX="${WINEPREFIX:-/opt/wineprefix}"
export WINEDEBUG="${WINEDEBUG:--all}"
DERIVED_TERMINAL_EXE="${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/terminal64.exe"
export MT_TERMINAL_EXE="${MT_TERMINAL_EXE:-$DERIVED_TERMINAL_EXE}"
export PYTHON_WIN_INSTALLER_URL=${PYTHON_WIN_INSTALLER_URL:-https://www.python.org/ftp/python/3.9.13/python-3.9.13-amd64.exe}
export MT5_WORKDIR=${MT5_WORKDIR:-${HOME}/.mt5-work}
export LOGDIR=${LOGDIR:-${HOME}/.mt5-bridge-logs}
export TMPDIR=${TMPDIR:-${MT5_WORKDIR}/tmp}
mkdir -p "${MT5_WORKDIR}/dl" "${TMPDIR}" "${LOGDIR}"

BOOTSTRAP_STATUS_FILE="${LOGDIR}/bootstrap.status"
BOOTSTRAP_READY_FILE="${LOGDIR}/bootstrap.ready"
BOOTSTRAP_FAILED_FILE="${LOGDIR}/bootstrap.failed"
MT5_TERMINAL_READY_FILE="${LOGDIR}/mt5_terminal.ready"
rm -f "${BOOTSTRAP_READY_FILE}" "${BOOTSTRAP_FAILED_FILE}" "${MT5_TERMINAL_READY_FILE}" > /dev/null 2>&1 || true
echo "starting" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

log "Bootstrap: starting (WINEPREFIX=${WINEPREFIX})"

# ---------------------------------------------------------------------------
# Helper: run command with a wall-clock timeout.
# ---------------------------------------------------------------------------
run_with_timeout() {
  local secs="$1"; shift
  log "  [run_with_timeout ${secs}s] $*"
  ( set +euo pipefail; "$@" ) &
  local pid=$! elapsed=0
  while kill -0 "$pid" > /dev/null 2>&1; do
    if [[ "$elapsed" -ge "$secs" ]]; then
      log "  TIMEOUT after ${secs}s: $*"
      kill -TERM "$pid" > /dev/null 2>&1 || true; sleep 2
      kill -KILL "$pid" > /dev/null 2>&1 || true
      wait "$pid" > /dev/null 2>&1 || true; return 124
    fi
    sleep 1; elapsed=$((elapsed + 1))
  done
  wait "$pid"; return $?
}

# ---------------------------------------------------------------------------
# Locate wine command.
# ---------------------------------------------------------------------------
WINE_CMD=""
for cmd in wine wine64; do
  if command -v "$cmd" > /dev/null 2>&1; then WINE_CMD="$cmd"; break; fi
done
if [[ -z "$WINE_CMD" ]]; then
  log "Bootstrap: ERROR: wine command not found."
  echo "failed: no_wine" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true
  touch "${BOOTSTRAP_FAILED_FILE}" > /dev/null 2>&1 || true; exit 1
fi
log "Bootstrap: using wine=${WINE_CMD}"

# ---------------------------------------------------------------------------
# Check if base image has pre-baked Wine Python path at /opt/wine_python_exe.path
# ---------------------------------------------------------------------------
FOUND_PYTHON=""
if [[ -f "/opt/wine_python_exe.path" ]]; then
  PREBAKED_PYTHON=$(cat /opt/wine_python_exe.path 2>/dev/null | tr -d '\n') || true
  if [[ -n "$PREBAKED_PYTHON" ]] && [[ -f "$PREBAKED_PYTHON" ]]; then
    FOUND_PYTHON="$PREBAKED_PYTHON"
    log "Bootstrap: Using pre-baked Wine Python: $FOUND_PYTHON"
  fi
fi

# ---------------------------------------------------------------------------
# STEP wine_prefix_probe — ensure Wine prefix is initialised.
# Skip if pre-baked (Wine prefix was init'd at image build time).
# ---------------------------------------------------------------------------
if [[ -f "/opt/mt5_terminal.preinstalled" ]] || [[ -n "$FOUND_PYTHON" ]]; then
  log "Bootstrap: STEP wine_prefix_probe SKIPPED (pre-baked base image detected)"
else
  log "Bootstrap: STEP wine_prefix_probe starting"
  run_with_timeout 60 "$WINE_CMD" cmd /c ver > "${LOGDIR}/wine-prefix-probe.log" 2>&1 || true
  log "Bootstrap: STEP wine_prefix_probe done"
  sleep 3
fi
echo "wine_prefix_probe done" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true

# ---------------------------------------------------------------------------
# STEP wine_python_probe — find/install Windows Python if not pre-baked.
# ---------------------------------------------------------------------------
python_ok=false
WINE_PYTHON_PATH=""

if [[ -n "$FOUND_PYTHON" ]]; then
  WINE_PYTHON_PATH=$(winepath -w "$FOUND_PYTHON" 2>/dev/null || echo "")
  log "Bootstrap: STEP wine_python_probe SKIPPED (pre-baked: ${WINE_PYTHON_PATH})"
  python_ok=true
else
  log "Bootstrap: STEP wine_python_probe starting"
  FOUND_PYTHON=$(find "${WINEPREFIX}/drive_c" -maxdepth 5 -name "python.exe" 2>/dev/null | head -1) || true
  if [[ -n "$FOUND_PYTHON" ]]; then
    log "  Found Python via search: $FOUND_PYTHON"
    WINE_PYTHON_PATH=$(winepath -w "$FOUND_PYTHON" 2>/dev/null || echo "")
    if [[ -n "$WINE_PYTHON_PATH" ]]; then
      run_with_timeout 30 "$WINE_CMD" "$WINE_PYTHON_PATH" \
        -c "import encodings; print('encodings_ok')" \
        > "${LOGDIR}/python-encodings-check.log" 2>&1 && python_ok=true || true
    fi
  fi
  log "Bootstrap: STEP wine_python_probe done (python_ok=${python_ok})"
fi
echo "wine_python_probe python_ok=${python_ok}" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true

# ---------------------------------------------------------------------------
# STEP python_install — only runs when NOT using the pre-baked base image.
# ---------------------------------------------------------------------------
if [[ "${python_ok}" != "true" ]]; then
  log "Bootstrap: Wine Python not found — installing (cold start, not using pre-baked image)."
  echo "python_install starting" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true

  rm -rf "${WINEPREFIX}/drive_c/Python"* > /dev/null 2>&1 || true
  rm -rf "${WINEPREFIX}/drive_c/Program Files/Python"* > /dev/null 2>&1 || true

  PYTHON_INSTALLER="${MT5_WORKDIR}/dl/python-installer.exe"
  log "Bootstrap: Downloading Python installer..."
  curl -fsSL --retry 3 --retry-delay 5 --connect-timeout 30 --max-time 300 \
    "${PYTHON_WIN_INSTALLER_URL}" -o "${PYTHON_INSTALLER}" > "${LOGDIR}/python-download.log" 2>&1 || true

  if [[ ! -f "${PYTHON_INSTALLER}" ]] || [[ ! -s "${PYTHON_INSTALLER}" ]]; then
    log "Bootstrap: ERROR: python-installer.exe download failed."
    echo "failed: python_download_failed" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true
    touch "${BOOTSTRAP_FAILED_FILE}" > /dev/null 2>&1 || true; exit 1
  fi

  log "Bootstrap: STEP python_install starting"
  run_with_timeout 600 "$WINE_CMD" "${PYTHON_INSTALLER}" \
    /quiet InstallAllUsers=1 PrependPath=1 Shortcuts=0 Include_test=0 \
    > "${LOGDIR}/python-installer.log" 2>&1 || true
  log "Bootstrap: STEP python_install done"
  rm -f "${PYTHON_INSTALLER}" > /dev/null 2>&1 || true
  sleep 5

  FOUND_PYTHON=$(find "${WINEPREFIX}/drive_c" -maxdepth 5 -name "python.exe" 2>/dev/null | head -1) || true
  log "  Post-install python.exe: ${FOUND_PYTHON:-NOT FOUND}"

  for i in $(seq 1 45); do
    if [[ -n "$FOUND_PYTHON" ]]; then
      WINE_PYTHON_PATH=$(winepath -w "$FOUND_PYTHON" 2>/dev/null || echo "")
      if [[ -n "$WINE_PYTHON_PATH" ]]; then
        if run_with_timeout 30 "$WINE_CMD" "$WINE_PYTHON_PATH" \
             -c "import encodings; print('encodings_ok')" \
             > "${LOGDIR}/python-encodings-check.log" 2>&1; then
          python_ok=true; log "  Python usable (attempt ${i})."; break
        fi
      fi
    fi
    FOUND_PYTHON=$(find "${WINEPREFIX}/drive_c" -maxdepth 5 -name "python.exe" 2>/dev/null | head -1) || true
    log "  Waiting for Python... attempt ${i}/45"; sleep 4
  done

  if [[ "${python_ok}" != "true" ]]; then
    log "Bootstrap: ERROR: Wine Python not working after install."
    tail -n 100 "${LOGDIR}/python-installer.log" 2>/dev/null || true
    echo "failed: python_broken" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true
    touch "${BOOTSTRAP_FAILED_FILE}" > /dev/null 2>&1 || true; exit 1
  fi
fi

# ---------------------------------------------------------------------------
# STEP pip installs — only runs when NOT using pre-baked base image.
# ---------------------------------------------------------------------------
if [[ -f "/opt/wine_python_exe.path" ]]; then
  log "Bootstrap: pip packages SKIPPED (pre-baked base image)"
else
  log "Bootstrap: Installing pip packages (MetaTrader5, mt5linux)..."
  echo "pip_install starting" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true

  run_with_timeout 300 "$WINE_CMD" "$WINE_PYTHON_PATH" \
    -m pip install --upgrade --no-cache-dir pip \
    > "${LOGDIR}/wine-pip-upgrade.log" 2>&1 || true
  run_with_timeout 600 "$WINE_CMD" "$WINE_PYTHON_PATH" \
    -m pip install --no-cache-dir MetaTrader5 \
    > "${LOGDIR}/wine-metatrader5-pip-install.log" 2>&1 || true
  run_with_timeout 600 "$WINE_CMD" "$WINE_PYTHON_PATH" \
    -m pip install --no-cache-dir "mt5linux>=0.1.9" \
    > "${LOGDIR}/wine-mt5linux-pip-install.log" 2>&1 || true
  run_with_timeout 300 "$WINE_CMD" "$WINE_PYTHON_PATH" \
    -m pip install --no-cache-dir python-dateutil \
    > "${LOGDIR}/wine-python-dateutil-pip-install.log" 2>&1 || true
  echo "pip_install done" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true

  if run_with_timeout 30 "$WINE_CMD" "$WINE_PYTHON_PATH" \
       -c "import encodings; import mt5linux; print('mt5linux_ok')" \
       > "${LOGDIR}/mt5linux-import-check.log" 2>&1; then
    log "Bootstrap: mt5linux import OK."
  else
    log "Bootstrap: WARNING: mt5linux import failed."
    tail -n 50 "${LOGDIR}/mt5linux-import-check.log" 2>/dev/null || true
  fi
fi

# ---------------------------------------------------------------------------
# bootstrap.ready — Python env is usable; mt5linux RPyC server can start.
# ---------------------------------------------------------------------------
touch "${BOOTSTRAP_READY_FILE}" > /dev/null 2>&1 || true
echo "ready" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true
log "Bootstrap: READY (Python env available). Writing bootstrap.ready sentinel."

# ---------------------------------------------------------------------------
# STEP MT5 terminal — check if pre-baked or install now.
# ---------------------------------------------------------------------------

# Check if base image already installed the terminal.
if [[ -f "/opt/mt5_terminal.preinstalled" ]]; then
  PREBAKED_TERMINAL=""
  if [[ -f "/opt/mt5_terminal_exe.path" ]]; then
    PREBAKED_TERMINAL=$(cat /opt/mt5_terminal_exe.path 2>/dev/null | tr -d '\n') || true
  fi
  if [[ -z "$PREBAKED_TERMINAL" ]] || [[ ! -f "$PREBAKED_TERMINAL" ]]; then
    # Scan to find it.
    PREBAKED_TERMINAL=$(find "${WINEPREFIX}/drive_c" -maxdepth 6 \
      -name "terminal64.exe" 2>/dev/null | head -1) || true
  fi
  if [[ -n "$PREBAKED_TERMINAL" ]] && [[ -f "$PREBAKED_TERMINAL" ]]; then
    export MT_TERMINAL_EXE="$PREBAKED_TERMINAL"
    echo "$PREBAKED_TERMINAL" > "${LOGDIR}/mt5_terminal_exe.path" 2>/dev/null || true
    touch "${MT5_TERMINAL_READY_FILE}" > /dev/null 2>&1 || true
    echo "mt5_terminal ready (pre-baked)" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true
    log "Bootstrap: MT5 terminal pre-baked at ${PREBAKED_TERMINAL}. Writing mt5_terminal.ready."
    log "Bootstrap: complete."
    echo "complete" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true
    exit 0
  fi
fi

# Check if terminal is already installed at the expected path.
if [[ -f "$MT_TERMINAL_EXE" ]]; then
  log "Bootstrap: MT5 terminal already present at $MT_TERMINAL_EXE"
  echo "$MT_TERMINAL_EXE" > "${LOGDIR}/mt5_terminal_exe.path" 2>/dev/null || true
  touch "${MT5_TERMINAL_READY_FILE}" > /dev/null 2>&1 || true
  echo "complete" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true
  log "Bootstrap: complete."
  exit 0
fi

# Install terminal if MT5_INSTALLER_URL is set.
if [[ -n "${MT5_INSTALLER_URL:-}" ]]; then
  log "Bootstrap: Downloading MT5 installer from ${MT5_INSTALLER_URL}..."
  echo "mt5_install starting" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true

  curl -fsSL --retry 3 --retry-delay 5 --connect-timeout 30 --max-time 600 \
    "${MT5_INSTALLER_URL}" -o "${MT5_WORKDIR}/dl/mt5setup.exe" \
    > "${LOGDIR}/mt5-download.log" 2>&1 || true

  if [[ ! -f "${MT5_WORKDIR}/dl/mt5setup.exe" ]] || [[ ! -s "${MT5_WORKDIR}/dl/mt5setup.exe" ]]; then
    log "Bootstrap: WARNING: mt5setup.exe download failed. Check ${LOGDIR}/mt5-download.log"
  else
    MT5_SIZE=$(du -sh "${MT5_WORKDIR}/dl/mt5setup.exe" 2>/dev/null | cut -f1)
    log "Bootstrap: MT5 installer downloaded (${MT5_SIZE}). Running with /auto..."
    # /auto is MetaTrader's own silent install flag. /silent is NSIS and does nothing.
    run_with_timeout 900 "$WINE_CMD" "${MT5_WORKDIR}/dl/mt5setup.exe" /auto \
      > "${LOGDIR}/mt5-install.log" 2>&1 || true
    log "Bootstrap: MT5 installer finished. Scanning for terminal64.exe..."
    rm -f "${MT5_WORKDIR}/dl/mt5setup.exe" > /dev/null 2>&1 || true

    FOUND_TERMINAL=$(find "${WINEPREFIX}/drive_c" -maxdepth 6 \
      -name "terminal64.exe" 2>/dev/null | head -1) || true
    if [[ -n "$FOUND_TERMINAL" ]]; then
      log "Bootstrap: terminal64.exe found at: $FOUND_TERMINAL"
      export MT_TERMINAL_EXE="$FOUND_TERMINAL"
      echo "$FOUND_TERMINAL" > "${LOGDIR}/mt5_terminal_exe.path" 2>/dev/null || true
      touch "${MT5_TERMINAL_READY_FILE}" > /dev/null 2>&1 || true
      echo "mt5_terminal ready" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true
    else
      log "Bootstrap: WARNING: terminal64.exe NOT found after install."
      log "  mt5-install.log tail:"
      tail -n 50 "${LOGDIR}/mt5-install.log" 2>/dev/null || true
    fi
  fi
else
  log "Bootstrap: MT5_INSTALLER_URL not set and terminal not pre-baked — skipping."
  log "  Set MT5_INSTALLER_URL env var in Render to auto-install MetaTrader 5, or"
  log "  rebuild the base image with --build-arg MT5_URL=<url> to bake it in."
fi

if [[ -f "$MT_TERMINAL_EXE" ]]; then
  log "Bootstrap: MT5 terminal confirmed at $MT_TERMINAL_EXE"
else
  log "Bootstrap: MT5 terminal NOT found. Terminal-dependent features will be unavailable."
fi

log "Bootstrap: complete."
echo "complete" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true
