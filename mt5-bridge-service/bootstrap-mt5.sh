#!/usr/bin/env bash
# Bootstrap script: installs Wine-side Python, MetaTrader5, and mt5linux packages.
# Designed to run in the background on Render while the FastAPI bridge starts immediately.
#
# Key design decisions:
#  - NO set -e / set -euo pipefail: we want to continue after Wine commands time out.
#  - All Wine commands are wrapped in run_with_timeout for predictable failure.
#  - Verbose logging to $LOGDIR/bootstrap-mt5.log so /debug/mt5 can surface progress.

set -uo pipefail

export DISPLAY=${DISPLAY:-:99}
export HOME="${HOME:-/home/wineuser}"
export WINEPREFIX="${WINEPREFIX:-${HOME}/.wineprefix}"
export WINEDEBUG="${WINEDEBUG:--all}"
DERIVED_TERMINAL_EXE="${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/terminal64.exe"
export MT_TERMINAL_EXE="${MT_TERMINAL_EXE:-$DERIVED_TERMINAL_EXE}"
export PYTHON_WIN_INSTALLER_URL=${PYTHON_WIN_INSTALLER_URL:-https://www.python.org/ftp/python/3.9.13/python-3.9.13-amd64.exe}

# Keep large downloads and temp files out of /tmp (Render eviction limit).
export MT5_WORKDIR=${MT5_WORKDIR:-${HOME}/.mt5-work}
export LOGDIR=${LOGDIR:-${HOME}/.mt5-bridge-logs}
export TMPDIR=${TMPDIR:-${MT5_WORKDIR}/tmp}
mkdir -p "${MT5_WORKDIR}/dl" "${TMPDIR}" "${LOGDIR}"

# Sentinels for other processes to know what's ready.
BOOTSTRAP_STATUS_FILE="${LOGDIR}/bootstrap.status"
BOOTSTRAP_READY_FILE="${LOGDIR}/bootstrap.ready"
BOOTSTRAP_FAILED_FILE="${LOGDIR}/bootstrap.failed"
rm -f "${BOOTSTRAP_READY_FILE}" "${BOOTSTRAP_FAILED_FILE}" > /dev/null 2>&1 || true
echo "starting" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true

log() {
  echo "[$(date -u +%H:%M:%S)] $*"
}

log "Bootstrap: preparing Wine prefix (WINEPREFIX=${WINEPREFIX})"

# If Render (or an old deploy) provides the wrong prefix path, force the
# executable path to match WINEPREFIX.
if [[ ( "${MT_TERMINAL_EXE}" == /root/.wine/* || "${MT_TERMINAL_EXE}" == /tmp/.wine/* || "${MT_TERMINAL_EXE}" == /bridge/.wine/* ) \
      && "${WINEPREFIX}" != /root/.wine* \
      && "${WINEPREFIX}" != /tmp/.wine* \
      && "${WINEPREFIX}" != /bridge/.wine* ]]; then
  export MT_TERMINAL_EXE="$DERIVED_TERMINAL_EXE"
fi

mkdir -p "${WINEPREFIX}"

# ---------------------------------------------------------------------------
# Locate wineboot / wine
# ---------------------------------------------------------------------------
WINEBOOT_CMD=""
for cmd in wineboot wine64boot wineboot64; do
  if command -v "$cmd" > /dev/null 2>&1; then
    WINEBOOT_CMD="$cmd"
    break
  fi
done

if [[ -z "$WINEBOOT_CMD" ]]; then
  log "Bootstrap: ERROR: wineboot command not found."
  echo "failed: no_wineboot" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true
  touch "${BOOTSTRAP_FAILED_FILE}" > /dev/null 2>&1 || true
  exit 1
fi

WINE_CMD=""
for cmd in wine wine64; do
  if command -v "$cmd" > /dev/null 2>&1; then
    WINE_CMD="$cmd"
    break
  fi
done

if [[ -z "$WINE_CMD" ]]; then
  log "Bootstrap: ERROR: wine command not found."
  echo "failed: no_wine" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true
  touch "${BOOTSTRAP_FAILED_FILE}" > /dev/null 2>&1 || true
  exit 1
fi

log "Bootstrap: using wine=${WINE_CMD}, wineboot=${WINEBOOT_CMD}"

# ---------------------------------------------------------------------------
# run_with_timeout: runs a command with a wall-clock timeout.
# Returns 0 if command succeeded, 124 on timeout, or the command's exit code.
# IMPORTANT: does NOT use set -e so callers decide how to handle failures.
# ---------------------------------------------------------------------------
run_with_timeout() {
  local secs="$1"
  shift
  local cmd_str="$*"
  log "  [run_with_timeout ${secs}s] $cmd_str"

  (
    # Unset pipefail inside the subshell so the command's own failures don't
    # cause the subshell to abort unexpectedly.
    set +euo pipefail
    "$@"
  ) &
  local pid=$!
  local elapsed=0
  while kill -0 "$pid" > /dev/null 2>&1; do
    if [[ "$elapsed" -ge "$secs" ]]; then
      log "  TIMEOUT after ${secs}s: $cmd_str"
      kill -TERM "$pid" > /dev/null 2>&1 || true
      sleep 2
      kill -KILL "$pid" > /dev/null 2>&1 || true
      wait "$pid" > /dev/null 2>&1 || true
      return 124
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  wait "$pid"
  return $?
}

# ---------------------------------------------------------------------------
# STEP wine_prefix_probe
# Trigger Wine prefix creation / ensure Wine is responsive.
# ---------------------------------------------------------------------------
log "Bootstrap: STEP wine_prefix_probe starting"
run_with_timeout 60 "$WINE_CMD" cmd /c ver > "${LOGDIR}/wine-prefix-probe.log" 2>&1 || true
log "Bootstrap: STEP wine_prefix_probe done"
echo "wine_prefix_probe done" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true

# Allow the Wine server to settle before probing Python.
sleep 3

# ---------------------------------------------------------------------------
# STEP wine_python_probe
# Detect whether Windows Python is already installed in the Wine prefix.
# We probe specific well-known install paths rather than letting Wine search,
# which avoids the slow "wine python" hanging when python.exe doesn't exist.
# ---------------------------------------------------------------------------
log "Bootstrap: STEP wine_python_probe starting"

python_ok=false

# Gather all candidate python.exe paths inside the Wine prefix.
PYTHON_EXE_CANDIDATES=(
  "${WINEPREFIX}/drive_c/Python39/python.exe"
  "${WINEPREFIX}/drive_c/Python310/python.exe"
  "${WINEPREFIX}/drive_c/Python311/python.exe"
  "${WINEPREFIX}/drive_c/Python38/python.exe"
  "${WINEPREFIX}/drive_c/Program Files/Python39/python.exe"
  "${WINEPREFIX}/drive_c/Program Files/Python310/python.exe"
  "${WINEPREFIX}/drive_c/users/Public/python.exe"
)

# Also try to find any python.exe via bounded filesystem search.
FOUND_PYTHON=""
for candidate in "${PYTHON_EXE_CANDIDATES[@]}"; do
  if [[ -f "$candidate" ]]; then
    FOUND_PYTHON="$candidate"
    log "  Found Python at: $candidate"
    break
  fi
done

if [[ -z "$FOUND_PYTHON" ]]; then
  # Bounded search under drive_c — limit depth to avoid scanning huge trees.
  FOUND_PYTHON=$(find "${WINEPREFIX}/drive_c" -maxdepth 5 -name "python.exe" 2>/dev/null | head -1) || true
  if [[ -n "$FOUND_PYTHON" ]]; then
    log "  Found Python via search: $FOUND_PYTHON"
  fi
fi

if [[ -n "$FOUND_PYTHON" ]]; then
  log "  Probing python at: $FOUND_PYTHON"
  # Convert Linux path to Wine path (Z: drive).
  WINE_PYTHON_PATH=$(winepath -w "$FOUND_PYTHON" 2>/dev/null || echo "")
  if [[ -n "$WINE_PYTHON_PATH" ]]; then
    run_with_timeout 30 "$WINE_CMD" "$WINE_PYTHON_PATH" -c "import encodings; print('encodings_ok')" \
      > "${LOGDIR}/python-encodings-check.log" 2>&1 && python_ok=true || true
  else
    # Fallback: try running the linux-side path directly through wine
    run_with_timeout 30 "$WINE_CMD" "$(winepath -w "$FOUND_PYTHON" 2>/dev/null || echo "$FOUND_PYTHON")" \
      -c "import encodings; print('encodings_ok')" \
      > "${LOGDIR}/python-encodings-check.log" 2>&1 && python_ok=true || true
  fi
  log "  python_ok=${python_ok} (encodings check log: $(tail -1 "${LOGDIR}/python-encodings-check.log" 2>/dev/null || echo '(no log)'))"
else
  log "  No python.exe found in Wine prefix; will install."
fi

log "Bootstrap: STEP wine_python_probe done (python_ok=${python_ok})"
echo "wine_python_probe python_ok=${python_ok}" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true

# ---------------------------------------------------------------------------
# STEP python_install (only if Python wasn't found/working)
# ---------------------------------------------------------------------------
if [[ "${python_ok}" != "true" ]]; then
  log "Bootstrap: Wine Python missing or broken; installing Python..."
  echo "python_install starting" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true

  # Remove likely broken python installs (best-effort).
  rm -rf "${WINEPREFIX}/drive_c/Python"* > /dev/null 2>&1 || true
  rm -rf "${WINEPREFIX}/drive_c/Program Files/Python"* > /dev/null 2>&1 || true
  rm -rf "${WINEPREFIX}/drive_c/users/"*/AppData/Local/Programs/Python* > /dev/null 2>&1 || true

  PYTHON_INSTALLER="${MT5_WORKDIR}/dl/python-installer.exe"

  log "Bootstrap: Downloading Python installer from ${PYTHON_WIN_INSTALLER_URL}..."
  curl -fsSL --retry 3 --retry-delay 5 --connect-timeout 30 --max-time 300 \
    "${PYTHON_WIN_INSTALLER_URL}" -o "${PYTHON_INSTALLER}" 2>&1 | tee -a "${LOGDIR}/python-download.log" || true

  if [[ ! -f "${PYTHON_INSTALLER}" ]] || [[ ! -s "${PYTHON_INSTALLER}" ]]; then
    log "Bootstrap: ERROR: python-installer.exe download failed or empty."
    echo "failed: python_download_failed" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true
    touch "${BOOTSTRAP_FAILED_FILE}" > /dev/null 2>&1 || true
    exit 1
  fi
  log "Bootstrap: Python installer downloaded ($(du -sh "${PYTHON_INSTALLER}" 2>/dev/null | cut -f1))."

  log "Bootstrap: STEP python_install starting"
  # Install Python for all users, prepend to PATH, no shortcuts.
  run_with_timeout 600 "$WINE_CMD" "${PYTHON_INSTALLER}" \
    /quiet InstallAllUsers=1 PrependPath=1 Shortcuts=0 Include_test=0 \
    > "${LOGDIR}/python-installer.log" 2>&1 || true
  log "Bootstrap: STEP python_install done"
  echo "python_install done" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true

  # Give Wine a moment to flush registry/fs changes.
  sleep 5

  # Re-scan for Python after install.
  FOUND_PYTHON=$(find "${WINEPREFIX}/drive_c" -maxdepth 5 -name "python.exe" 2>/dev/null | head -1) || true
  log "  Post-install python.exe search: ${FOUND_PYTHON:-NOT FOUND}"

  # Wait up to 3 minutes for Python to become usable (Wine can be slow).
  for i in $(seq 1 45); do
    if [[ -n "$FOUND_PYTHON" ]]; then
      WINE_PYTHON_PATH=$(winepath -w "$FOUND_PYTHON" 2>/dev/null || echo "")
      if [[ -n "$WINE_PYTHON_PATH" ]]; then
        if run_with_timeout 30 "$WINE_CMD" "$WINE_PYTHON_PATH" \
             -c "import encodings; print('encodings_ok')" \
             > "${LOGDIR}/python-encodings-check.log" 2>&1; then
          python_ok=true
          log "  Python usable after install (attempt ${i})."
          break
        fi
      fi
    fi
    # Re-scan in case the installer writes to a non-standard path.
    FOUND_PYTHON=$(find "${WINEPREFIX}/drive_c" -maxdepth 5 -name "python.exe" 2>/dev/null | head -1) || true
    log "  Waiting for Python to become usable... attempt ${i}/45"
    sleep 4
  done
fi

if [[ "${python_ok}" != "true" ]]; then
  log "Bootstrap: ERROR: Wine Python still not working after install attempt."
  log "  python-installer.log tail:"
  tail -n 100 "${LOGDIR}/python-installer.log" 2>/dev/null || true
  log "  python-encodings-check.log tail:"
  tail -n 50 "${LOGDIR}/python-encodings-check.log" 2>/dev/null || true
  echo "failed: python_broken" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true
  touch "${BOOTSTRAP_FAILED_FILE}" > /dev/null 2>&1 || true
  exit 1
fi

log "Bootstrap: Wine Python is working. FOUND_PYTHON=${FOUND_PYTHON}"
WINE_PYTHON_PATH=$(winepath -w "$FOUND_PYTHON" 2>/dev/null || echo "")
log "  Wine path: ${WINE_PYTHON_PATH}"

# ---------------------------------------------------------------------------
# STEP wine_pip_upgrade + lib installs
# ---------------------------------------------------------------------------
log "Bootstrap: Installing Wine Python packages (pip, MetaTrader5, mt5linux)..."
echo "pip_install starting" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true

log "Bootstrap: STEP wine_pip_upgrade starting"
run_with_timeout 300 "$WINE_CMD" "$WINE_PYTHON_PATH" -m pip install \
  --upgrade --no-cache-dir pip \
  > "${LOGDIR}/wine-pip-upgrade.log" 2>&1 || true
log "Bootstrap: STEP wine_pip_upgrade done (exit code ignored)"

log "Bootstrap: STEP wine_pip_install MetaTrader5 starting"
run_with_timeout 600 "$WINE_CMD" "$WINE_PYTHON_PATH" -m pip install \
  --no-cache-dir MetaTrader5 \
  > "${LOGDIR}/wine-metatrader5-pip-install.log" 2>&1 || true
log "Bootstrap: STEP wine_pip_install MetaTrader5 done"

log "Bootstrap: STEP wine_pip_install mt5linux starting"
run_with_timeout 600 "$WINE_CMD" "$WINE_PYTHON_PATH" -m pip install \
  --no-cache-dir "mt5linux>=0.1.9" \
  > "${LOGDIR}/wine-mt5linux-pip-install.log" 2>&1 || true
log "Bootstrap: STEP wine_pip_install mt5linux done"

run_with_timeout 300 "$WINE_CMD" "$WINE_PYTHON_PATH" -m pip install \
  --no-cache-dir python-dateutil \
  > "${LOGDIR}/wine-python-dateutil-pip-install.log" 2>&1 || true

log "Bootstrap: STEP wine_pip_install_libs done"
echo "pip_install done" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true

# Verify mt5linux is importable.
if run_with_timeout 30 "$WINE_CMD" "$WINE_PYTHON_PATH" \
     -c "import encodings; import mt5linux; print('mt5linux_ok')" \
     > "${LOGDIR}/mt5linux-import-check.log" 2>&1; then
  log "Bootstrap: mt5linux import verified OK."
else
  log "Bootstrap: WARNING: mt5linux import check failed. Logs:"
  tail -n 50 "${LOGDIR}/mt5linux-import-check.log" 2>/dev/null || true
  # Non-fatal: the container will keep trying in start.sh
fi

touch "${BOOTSTRAP_READY_FILE}" > /dev/null 2>&1 || true
echo "ready" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true
log "Bootstrap: READY. Bootstrap sentinel written."

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
rm -f "${MT5_WORKDIR}/dl/python-installer.exe" > /dev/null 2>&1 || true

# ---------------------------------------------------------------------------
# MT5 terminal installer (optional, only if MT5_INSTALLER_URL is set)
# ---------------------------------------------------------------------------
MT5_TERMINAL_READY_FILE="${LOGDIR}/mt5_terminal.ready"
rm -f "${MT5_TERMINAL_READY_FILE}" > /dev/null 2>&1 || true

if [[ -n "${MT5_INSTALLER_URL:-}" && ! -f "$MT_TERMINAL_EXE" ]]; then
  log "Bootstrap: downloading MT5 installer from ${MT5_INSTALLER_URL}..."
  echo "mt5_install starting" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true

  curl -fsSL --retry 3 --retry-delay 5 --connect-timeout 30 --max-time 600 \
    "${MT5_INSTALLER_URL}" -o "${MT5_WORKDIR}/dl/mt5setup.exe" \
    > "${LOGDIR}/mt5-download.log" 2>&1 || true

  if [[ ! -f "${MT5_WORKDIR}/dl/mt5setup.exe" ]] || [[ ! -s "${MT5_WORKDIR}/dl/mt5setup.exe" ]]; then
    log "Bootstrap: WARNING: mt5setup.exe download failed or empty. Check ${LOGDIR}/mt5-download.log"
  else
    MT5_SIZE=$(du -sh "${MT5_WORKDIR}/dl/mt5setup.exe" 2>/dev/null | cut -f1)
    log "Bootstrap: MT5 installer downloaded (${MT5_SIZE}). Running installer..."
    # MetaTrader 5 uses its own installer format — /auto is the correct silent flag.
    # /silent is an NSIS flag and causes the installer to exit immediately without installing.
    run_with_timeout 900 "$WINE_CMD" "${MT5_WORKDIR}/dl/mt5setup.exe" /auto \
      > "${LOGDIR}/mt5-install.log" 2>&1 || true
    log "Bootstrap: MT5 installer finished (exit ignored). Searching for terminal64.exe..."
    rm -f "${MT5_WORKDIR}/dl/mt5setup.exe" > /dev/null 2>&1 || true

    # /auto installs to C:\Program Files\MetaTrader 5\ by default, but scan broadly.
    FOUND_TERMINAL=$(find "${WINEPREFIX}/drive_c" -maxdepth 6 -name "terminal64.exe" 2>/dev/null | head -1) || true
    if [[ -n "$FOUND_TERMINAL" ]]; then
      log "Bootstrap: terminal64.exe found at: $FOUND_TERMINAL"
      # Update the MT_TERMINAL_EXE env to the discovered path for this process tree.
      export MT_TERMINAL_EXE="$FOUND_TERMINAL"
      # Write the discovered path to a file so start.sh can read it.
      echo "$FOUND_TERMINAL" > "${LOGDIR}/mt5_terminal_exe.path" 2>/dev/null || true
      touch "${MT5_TERMINAL_READY_FILE}" > /dev/null 2>&1 || true
      echo "mt5_terminal ready" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true
    else
      log "Bootstrap: WARNING: terminal64.exe NOT found after install."
      log "  mt5-install.log tail:"
      tail -n 50 "${LOGDIR}/mt5-install.log" 2>/dev/null || true
    fi
  fi
elif [[ -f "$MT_TERMINAL_EXE" ]]; then
  log "Bootstrap: MT5 terminal already present at $MT_TERMINAL_EXE"
  echo "$MT_TERMINAL_EXE" > "${LOGDIR}/mt5_terminal_exe.path" 2>/dev/null || true
  touch "${MT5_TERMINAL_READY_FILE}" > /dev/null 2>&1 || true
else
  log "Bootstrap: MT5_INSTALLER_URL not set and terminal not found — skipping MT5 install."
  log "  Set MT5_INSTALLER_URL env var to auto-install MetaTrader 5."
fi

if [[ -f "$MT_TERMINAL_EXE" ]]; then
  log "Bootstrap: MT5 terminal confirmed at $MT_TERMINAL_EXE"
else
  log "Bootstrap: MT5 terminal NOT found at $MT_TERMINAL_EXE"
fi

log "Bootstrap: complete."
echo "complete" > "${BOOTSTRAP_STATUS_FILE}" 2>/dev/null || true
