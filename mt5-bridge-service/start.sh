#!/usr/bin/env bash
set -euo pipefail

export DISPLAY=${DISPLAY:-:99}
export BRIDGE_PORT=${PORT:-${BRIDGE_PORT:-5555}}
export HOME="${HOME:-/home/wineuser}"
export WINEPREFIX="${WINEPREFIX:-${HOME}/.wineprefix}"
DERIVED_TERMINAL_EXE="${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/terminal64.exe"
export MT_TERMINAL_EXE="${MT_TERMINAL_EXE:-$DERIVED_TERMINAL_EXE}"
export PYTHON_WIN_INSTALLER_URL=${PYTHON_WIN_INSTALLER_URL:-https://www.python.org/ftp/python/3.9.13/python-3.9.13-amd64.exe}

# Keep logs and bootstrap downloads out of /tmp (Render eviction limit).
export MT5_WORKDIR=${MT5_WORKDIR:-${HOME}/.mt5-work}
export LOGDIR=${LOGDIR:-${HOME}/.mt5-bridge-logs}
export MT5_CONTEXT_MODE="${MT5_CONTEXT_MODE:-default}"
export MT5_CONTEXT_DIR="${MT5_CONTEXT_DIR:-${WINEPREFIX}/drive_c/mt5-data}"
mkdir -p "${WINEPREFIX}" "${MT5_WORKDIR}" "${LOGDIR}"

# ── Block MT5 LiveUpdate domains NOW — before Xvfb/Wine process any DNS ──────
# Must run here (before any subprocess) so the terminal binary never resolves
# update CDNs on startup. Covers the original set plus extra domains.
for _d in \
  live.mql5.com updates.mql5.com update.mql5.com \
  download.mql5.com cdn.mql5.com ec.mql5.com files.mql5.com \
  www.mql5.com mql5.com \
  update.metatrader5.com updates.metatrader5.com \
  mt5-update.metaquotes.net metaquotes.net; do
  grep -qF "${_d}" /etc/hosts 2>/dev/null || \
    echo "0.0.0.0 ${_d}" >> /etc/hosts 2>/dev/null || true
done
unset _d

# Render free-tier limits /tmp to ~2GB. Some Wine/MT5 output is redirected to
# `/tmp/mt5-launch-wrapper.log`, so symlink it into our persistent LOGDIR.
rm -f /tmp/mt5-launch-wrapper.log > /dev/null 2>&1 || true
ln -sf "${LOGDIR}/mt5-launch-wrapper.log" /tmp/mt5-launch-wrapper.log > /dev/null 2>&1 || true

# If Render (or an old deploy) still provides the wrong prefix path,
# force the executable path to match the actual runtime WINEPREFIX.
if [[ ("${MT_TERMINAL_EXE}" == /root/.wine/* || "${MT_TERMINAL_EXE}" == /tmp/.wine/* || "${MT_TERMINAL_EXE}" == /bridge/.wine/*) \
      && "${WINEPREFIX}" != /root/.wine* \
      && "${WINEPREFIX}" != /tmp/.wine* \
      && "${WINEPREFIX}" != /bridge/.wine* ]]; then
  export MT_TERMINAL_EXE="$DERIVED_TERMINAL_EXE"
fi

mkdir -p "${WINEPREFIX}"

required_vars=("MT_LOGIN" "MT_PASSWORD" "MT_SERVER" "MT_BRIDGE_SECRET")
for key in "${required_vars[@]}"; do
  if [[ -z "${!key:-}" ]]; then
    echo "Missing required env var: ${key}" >&2
    exit 1
  fi
done

# Start virtual display first so Wine can initialize safely.
echo "Starting Xvfb display server on ${DISPLAY}..."
Xvfb "${DISPLAY}" -screen 0 1280x720x24 > "${LOGDIR}/xvfb.log" 2>&1 &
XVFB_PID=$!
sleep 2
if ! kill -0 "${XVFB_PID}" 2>/dev/null; then
  echo "Failed to start Xvfb. Check ${LOGDIR}/xvfb.log" >&2
  exit 1
fi

# Start a lightweight window manager so xdotool can deliver focus events
# to Wine dialogs. Without a WM, windowactivate is a no-op and keyboard/
# mouse input never reaches Wine's message queue.
DISPLAY="${DISPLAY}" openbox --sm-disable > "${LOGDIR}/openbox.log" 2>&1 &
echo "Openbox WM started (PID $!)"

echo "Starting bridge API (uvicorn) immediately; MT5 bootstrap in background..."

PORT="${PORT:-${BRIDGE_PORT:-5555}}"

# Bootstrap Wine/MT5 in the background so Render detects the open port quickly.
/bridge/bootstrap-mt5.sh > "${LOGDIR}/bootstrap-mt5.log" 2>&1 &

# ---------------------------------------------------------------------------
# mt5linux launcher (background subshell)
# Waits for bootstrap to finish, then launches the Wine-side RPyC server.
# Uses the python.exe path discovered by bootstrap (NOT `wine python` globally,
# which hangs when Python isn't on Wine's system PATH).
# ---------------------------------------------------------------------------
if command -v wine > /dev/null 2>&1; then
  (
    set +euo pipefail
    echo "[mt5linux-launcher] Waiting for bootstrap.ready sentinel..."

    # Wait up to 20 minutes (600 x 2s). Python download+install can take ~10 min.
    WAITED=0
    for _ in $(seq 1 600); do
      if [[ -f "${LOGDIR}/bootstrap.ready" ]]; then
        echo "[mt5linux-launcher] Bootstrap ready after ~${WAITED}s."
        break
      fi
      if [[ -f "${LOGDIR}/bootstrap.failed" ]]; then
        echo "[mt5linux-launcher] Bootstrap FAILED. Dumping log:" >&2
        tail -n 100 "${LOGDIR}/bootstrap-mt5.log" >&2 || true
        exit 1
      fi
      WAITED=$((WAITED + 2))
      if (( WAITED % 60 == 0 )); then
        echo "[mt5linux-launcher] Still waiting for bootstrap... (${WAITED}s elapsed)"
        echo "[mt5linux-launcher]   status: $(cat "${LOGDIR}/bootstrap.status" 2>/dev/null || echo '(none)')"
      fi
      sleep 2
    done

    if [[ ! -f "${LOGDIR}/bootstrap.ready" ]]; then
      echo "[mt5linux-launcher] Timed out waiting for bootstrap.ready" >&2
      tail -n 200 "${LOGDIR}/bootstrap-mt5.log" >&2 || true
      exit 1
    fi

    # Locate the Wine python.exe — check pre-baked base image path first.
    FOUND_PYTHON=""
    if [[ -f "/opt/wine_python_exe.path" ]]; then
      PREBAKED=$(cat /opt/wine_python_exe.path 2>/dev/null | tr -d '\n') || true
      if [[ -n "$PREBAKED" ]] && [[ -f "$PREBAKED" ]]; then
        FOUND_PYTHON="$PREBAKED"
        echo "[mt5linux-launcher] Using pre-baked python.exe: $FOUND_PYTHON"
      fi
    fi
    if [[ -z "$FOUND_PYTHON" ]]; then
      FOUND_PYTHON=$(find "${WINEPREFIX}/drive_c" -maxdepth 5 -name "python.exe" 2>/dev/null | head -1) || true
    fi
    if [[ -z "$FOUND_PYTHON" ]]; then
      echo "[mt5linux-launcher] Wine python.exe not found under ${WINEPREFIX}/drive_c" >&2
      exit 1
    fi
    echo "[mt5linux-launcher] Using python.exe: $FOUND_PYTHON"

    # Run Wine Python directly (wine handles unix paths with spaces correctly).
    # Ownership is now root:root so wine accepts the WINEPREFIX.
    if ! timeout 30 wine "${FOUND_PYTHON}" -c "import encodings; import mt5linux" > /dev/null 2>&1; then
      echo "[mt5linux-launcher] mt5linux not importable; attempting pip install..." >&2
      timeout 300 wine "${FOUND_PYTHON}" -m pip install --no-cache-dir mt5linux MetaTrader5 python-dateutil \
        >> "${LOGDIR}/wine-mt5linux-pip-install.log" 2>&1 || true
    fi

    if ! timeout 30 wine "${FOUND_PYTHON}" -c "import encodings; import mt5linux" > /dev/null 2>&1; then
      echo "[mt5linux-launcher] mt5linux still not importable. Check ${LOGDIR}/bootstrap-mt5.log" >&2
      exit 1
    fi

    echo "[mt5linux-launcher] Launching mt5linux RPyC server on 127.0.0.1:18812"
    wine "${FOUND_PYTHON}" -m mt5linux --host 127.0.0.1 --port 18812 2>&1 \
      | tee "${LOGDIR}/mt5linux.log" &

    # Wait for the RPyC port to open (up to 30s).
    MT5LINUX_PORT_OPEN=false
    for _ in $(seq 1 30); do
      if (echo > /dev/tcp/127.0.0.1/18812) > /dev/null 2>&1; then
        MT5LINUX_PORT_OPEN=true
        break
      fi
      sleep 1
    done

    if [[ "$MT5LINUX_PORT_OPEN" == "true" ]]; then
      echo "[mt5linux-launcher] mt5linux RPyC port is OPEN on 127.0.0.1:18812"
    else
      echo "[mt5linux-launcher] mt5linux RPyC port is NOT open on 127.0.0.1:18812" >&2
      echo "=== bootstrap-mt5.log (last 200 lines) ===" >&2
      tail -n 200 "${LOGDIR}/bootstrap-mt5.log" >&2 || true
      echo "=== mt5linux.log (last 100 lines) ===" >&2
      tail -n 100 "${LOGDIR}/mt5linux.log" >&2 || true
    fi
  ) &
fi

# ---------------------------------------------------------------------------
# MT5 terminal launcher (background subshell)
# Waits for bootstrap to write mt5_terminal_exe.path, then launches the terminal.
# bootstrap.sh sets bootstrap.ready BEFORE the MT5 installer runs, so we must
# wait for the separate mt5_terminal.ready sentinel here.
# ---------------------------------------------------------------------------
(
  set +euo pipefail

  WINE_CMD=""
  if command -v wine > /dev/null 2>&1; then
    WINE_CMD="wine"
  elif command -v wine64 > /dev/null 2>&1; then
    WINE_CMD="wine64"
  fi

  if [[ -z "$WINE_CMD" ]]; then
    echo "[mt5-terminal] Wine command not found; skipping MT5 terminal launch." >&2
    exit 0
  fi

  # Guard: only launch MT5 terminal if explicitly enabled via env var.
  # On Render free tier (512MB RAM), terminal64.exe uses ~400MB and causes OOM.
  # Set MT5_LAUNCH_TERMINAL=true to enable (requires a paid plan with >=1GB RAM).
  if [[ "${MT5_LAUNCH_TERMINAL:-false}" != "true" ]]; then
    echo "[mt5-terminal] MT5_LAUNCH_TERMINAL is not 'true' — skipping terminal launch (OOM guard)."
    echo "[mt5-terminal] Set MT5_LAUNCH_TERMINAL=true in Render env vars to enable (requires >=1GB RAM)."
    exit 0
  fi

  echo "[mt5-terminal] Waiting for MT5 terminal to be installed..."

  # Wait up to 30 minutes for the terminal installer to finish.
  WAITED=0
  TERMINAL_EXE=""
  for _ in $(seq 1 900); do
    # Check sentinel file written by bootstrap after terminal install.
    if [[ -f "${LOGDIR}/mt5_terminal.ready" ]]; then
      # Read the actual path bootstrap discovered (may differ from the env default).
      if [[ -f "${LOGDIR}/mt5_terminal_exe.path" ]]; then
        TERMINAL_EXE=$(cat "${LOGDIR}/mt5_terminal_exe.path" 2>/dev/null | tr -d '\n') || true
      fi
      TERMINAL_EXE="${TERMINAL_EXE:-$MT_TERMINAL_EXE}"
      echo "[mt5-terminal] Terminal ready after ~${WAITED}s. Path: $TERMINAL_EXE"
      break
    fi
    # Also check if the exe appeared at the default path (e.g. pre-installed image).
    if [[ -f "$MT_TERMINAL_EXE" ]]; then
      TERMINAL_EXE="$MT_TERMINAL_EXE"
      echo "[mt5-terminal] terminal64.exe found at default path after ~${WAITED}s."
      break
    fi
    WAITED=$((WAITED + 2))
    if (( WAITED % 120 == 0 )); then
      echo "[mt5-terminal] Still waiting for MT5 terminal install... (${WAITED}s elapsed)"
      echo "[mt5-terminal]   status: $(cat "${LOGDIR}/bootstrap.status" 2>/dev/null || echo '(none)')"
    fi
    sleep 2
  done

  if [[ -z "$TERMINAL_EXE" ]] || [[ ! -f "$TERMINAL_EXE" ]]; then
    echo "[mt5-terminal] MT5 terminal executable not found after waiting. Skipping launch." >&2
    echo "[mt5-terminal]   Expected: $MT_TERMINAL_EXE" >&2
    echo "[mt5-terminal]   From sentinel: $(cat "${LOGDIR}/mt5_terminal_exe.path" 2>/dev/null || echo '(none)')" >&2
    exit 0
  fi

  IPC_READY_FILE="${LOGDIR}/mt5_ipc.ready"
  IPC_FAILED_FILE="${LOGDIR}/mt5_ipc.failed"
  IPC_STATUS_FILE="${LOGDIR}/mt5_ipc.status"
  IPC_PROBE_LOG="${LOGDIR}/mt5-ipc-probe.log"
  CONTEXT_STATUS_FILE="${LOGDIR}/mt5_context.status"
  rm -f "${IPC_READY_FILE}" "${IPC_FAILED_FILE}" "${IPC_STATUS_FILE}" "${IPC_PROBE_LOG}" "${CONTEXT_STATUS_FILE}" 2>/dev/null || true
  echo "pending" > "${IPC_STATUS_FILE}" 2>/dev/null || true

  CONTEXT_ARGS=()
  case "${MT5_CONTEXT_MODE}" in
    portable)
      CONTEXT_ARGS+=("/portable")
      ;;
    data_dir)
      mkdir -p "${MT5_CONTEXT_DIR}" 2>/dev/null || true
      CONTEXT_ARGS+=("/datapath:${MT5_CONTEXT_DIR}")
      ;;
    default|*)
      # Default: use the pre-baked AppData MetaQuotes demo session.
      # The terminal auto-connects on startup → IPC pipes created immediately.
      # Portable mode requires interactive login before IPC becomes available.
      echo "[mt5-terminal] MT5_CONTEXT_MODE=${MT5_CONTEXT_MODE:-unset}; using default (AppData) mode"
      ;;
  esac
  # MT5 update-domain blocking is done at the top of start.sh (before Xvfb),
  # so the terminal never resolves update CDN domains on startup.
  # Note: /noupdate is NOT a valid terminal64.exe argument.
  echo "mode=${MT5_CONTEXT_MODE}; exe=${TERMINAL_EXE}; args=${CONTEXT_ARGS[*]:-(none)}" > "${CONTEXT_STATUS_FILE}" 2>/dev/null || true

  # Pre-write MT5 server config to every candidate config path so the terminal
  # skips the "Select a company" first-run wizard and goes straight to a Login
  # screen instead.  We always overwrite (remove the guard) because MT_SERVER
  # may differ from whatever the Dockerfile baked in.
  # The Login screen responds to mt5.initialize(login,password,server) over IPC;
  # the company-selection wizard does NOT. Password is NOT stored here (MT5
  # encrypts it; plaintext is ignored) — it is injected by mt5.initialize().
  _mt5_precfg() {
    local _d="$1"
    mkdir -p "${_d}" 2>/dev/null || return
    printf '[Common]\r\nLogin=%s\r\nServer=%s\r\nNewsEnable=0\r\nAutoSync=0\r\n\r\n[Experts]\r\nEnabled=1\r\nAllowLiveTrading=1\r\n' \
      "${MT_LOGIN}" "${MT_SERVER}" > "${_d}/common.ini" 2>/dev/null || true
    echo "[mt5-terminal] Wrote server config → ${_d}/common.ini"
  }
  # Standard install path and the Common AppData path.
  _mt5_precfg "${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/config"
  _mt5_precfg "${WINEPREFIX}/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal/Common/config"
  # Also write to every hash-subdirectory created by the Dockerfile first-run
  # init so the correct profile-specific config directory is covered.
  find "${WINEPREFIX}/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal" \
    -mindepth 2 -maxdepth 2 -type d -name "config" 2>/dev/null \
    | while IFS= read -r _hcfg; do _mt5_precfg "${_hcfg}"; done || true
  unset -f _mt5_precfg

  # Write a Windows-accessible standalone config file used by the /config: flag.
  # This is the most reliable way to pass Login/Server to the terminal on startup
  # regardless of which profile directory it selects internally.
  _WIN_CFG_LINUX="${WINEPREFIX}/drive_c/mt5-headless.ini"
  printf '[Common]\r\nLogin=%s\r\nServer=%s\r\nNewsEnable=0\r\nAutoSync=0\r\n\r\n[Experts]\r\nEnabled=1\r\nAllowLiveTrading=1\r\n' \
    "${MT_LOGIN}" "${MT_SERVER}" > "${_WIN_CFG_LINUX}" 2>/dev/null || true
  echo "[mt5-terminal] Wrote Windows-accessible config → ${_WIN_CFG_LINUX}"

  _launch_terminal() {
    echo "[mt5-terminal] Launching MetaTrader 5 terminal..."
    # Pass /config: so the terminal reads our pre-written Login+Server ini on
    # startup.  This is the documented way to inject settings without relying
    # on the correct AppData hash-subdirectory being known in advance.
    # Combined with the pre-baked AppData (Dockerfile first-run layer) this
    # means the terminal starts in a Login form rather than the wizard.
    "$WINE_CMD" "$TERMINAL_EXE" \
      /config:"C:\\mt5-headless.ini" \
      "${CONTEXT_ARGS[@]}" \
      > "${LOGDIR}/mt5-terminal.log" 2>&1 &
    TERMINAL_PID=$!
    echo "[mt5-terminal] terminal pid=${TERMINAL_PID}"
  }
  _launch_terminal
  IPC_TIMEOUT_STREAK=0
  IPC_RESTART_COUNT=0

  # Fallback dialog dismisser: if a LiveUpdate domain was missed and the
  # "Restart to install" dialog still appears, dismiss it via xdotool.
  # Note: xdotool --name is a plain substring match — do NOT use | here
  # (e.g. "A|B" looks for a title literally containing a pipe character).
  # LiveUpdate dialogs are often centered; Restart/Later sit lower than
  # older wizard-nested coords (y ~400–430 on 1280x720), not y ~330.
  (
    _xd() { DISPLAY=:99 xdotool "$@" 2>/dev/null || true; }
    _uniq_ids() { awk 'NF' | sort -n -u; }
    sleep 8
    for _try in $(seq 1 120); do
      LIVE_IDS=$(
        { _xd search --onlyvisible --name LiveUpdate
          _xd search --onlyvisible --name "Welcome to"
        } | _uniq_ids
      )
      WIZ_IDS=$(
        { _xd search --onlyvisible --name "Select a company"
          _xd search --onlyvisible --name "open an account"
          _xd search --onlyvisible --name "MetaTrader 5"
          _xd search --onlyvisible --name "MetaTrader"
          _xd search --onlyvisible --name "Setup"
        } | _uniq_ids
      )
      WINIDS=$(_xd search --onlyvisible | _uniq_ids)
      ALL_IDS=$(printf '%s\n%s\n%s\n' "${LIVE_IDS}" "${WIZ_IDS}" "${WINIDS}" | _uniq_ids)
      for _wid in ${ALL_IDS}; do
        _xd windowactivate --sync "${_wid}"
        sleep 0.25
        # Click bands: LiveUpdate Restart/Later rows, legacy wizard Next rows,
        # and first-run broker-list item rows (critical: Next is disabled until
        # a company is selected from the list).
        for _xy in \
          "548 418" "638 418" "728 418" \
          "520 402" "600 428" "700 428" \
          "560 332" "530 330" "585 338" \
          "469 335" "445 328" \
          "724 488" "644 488" "972 183" \
          "400 210" "500 210" "640 210" \
          "400 245" "500 245" "640 245" \
          "400 275" "500 275" "640 275" \
          "640 520" "700 520" "640 540"; do
          set -- ${_xy}
          _xd mousemove --clearmodifiers "$1" "$2" click 1
          sleep 0.06
        done
        # Double-click on first broker list item to select + activate it.
        _xd mousemove --clearmodifiers "400" "245" click 1
        sleep 0.05
        _xd mousemove --clearmodifiers "400" "245" click 1
        sleep 0.1
        # Keystrokes scoped to this X window (avoids firing on wrong stack order).
        _xd key --window "${_wid}" --clearmodifiers Escape
        sleep 0.1
        _xd key --window "${_wid}" --clearmodifiers Return
        sleep 0.08
        _xd key --window "${_wid}" --clearmodifiers Tab
        sleep 0.06
        _xd key --window "${_wid}" --clearmodifiers Return
        sleep 0.06
        _xd key --window "${_wid}" --clearmodifiers Tab
        sleep 0.06
        _xd key --window "${_wid}" --clearmodifiers Return
        sleep 0.06
        _xd key --window "${_wid}" --clearmodifiers Return
        # For wizard windows: type the broker server name to filter the list,
        # then hit Return twice to select + advance.
        for _wzid in ${WIZ_IDS:-}; do
          if [[ "${_wid}" == "${_wzid}" ]]; then
            _xd type --clearmodifiers "${MT_SERVER:-MetaQuotes-Demo}"
            sleep 0.3
            _xd key --window "${_wid}" --clearmodifiers Return
            sleep 0.2
            _xd key --window "${_wid}" --clearmodifiers Return
            break
          fi
        done
      done
      sleep 5
    done
  ) &

  # Resolve Wine Python for IPC probe.
  # Use predictable known paths first; avoid 'find | head -1' which can
  # hang on Wine's large drive_c directory due to SIGPIPE + pipefail.
  FOUND_PYTHON=""
  echo "[mt5-probe] Resolving Wine python.exe path..." >&2

  if [[ -f "/opt/wine_python_exe.path" ]]; then
    PREBAKED=$(cat /opt/wine_python_exe.path 2>/dev/null | tr -d '\n\r') || true
    if [[ -n "$PREBAKED" ]] && [[ -f "$PREBAKED" ]]; then
      FOUND_PYTHON="$PREBAKED"
      echo "[mt5-probe] Using prebaked path: ${FOUND_PYTHON}" >&2
    fi
  fi

  # Try the common install locations directly.
  if [[ -z "$FOUND_PYTHON" ]]; then
    for _py_candidate in \
      "${WINEPREFIX}/drive_c/Program Files/Python39/python.exe" \
      "${WINEPREFIX}/drive_c/Program Files/Python310/python.exe" \
      "${WINEPREFIX}/drive_c/Program Files/Python311/python.exe" \
      "${WINEPREFIX}/drive_c/Python39/python.exe" \
      "${WINEPREFIX}/drive_c/Python310/python.exe"; do
      if [[ -f "$_py_candidate" ]]; then
        FOUND_PYTHON="$_py_candidate"
        echo "[mt5-probe] Found python at: ${FOUND_PYTHON}" >&2
        break
      fi
    done
  fi

  # Last-resort bounded find (5s timeout to prevent hang).
  if [[ -z "$FOUND_PYTHON" ]]; then
    echo "[mt5-probe] Trying bounded find (5s)..." >&2
    FOUND_PYTHON=$(timeout 5 find "${WINEPREFIX}/drive_c" -maxdepth 6 -name "python.exe" 2>/dev/null | head -1) || true
    echo "[mt5-probe] Bounded find result: '${FOUND_PYTHON}'" >&2
  fi

  if [[ -z "$FOUND_PYTHON" ]] || [[ ! -f "$FOUND_PYTHON" ]]; then
    echo "[mt5-terminal] Could not find Wine python.exe for IPC probe." >&2
    echo "failed: python_not_found" > "${IPC_STATUS_FILE}" 2>/dev/null || true
    touch "${IPC_FAILED_FILE}" 2>/dev/null || true
  else
    # Upgrade the MetaTrader5 Python package to match the running terminal build.
    # The pre-baked package (5.0.5735) is mismatched with terminal build 5800:
    # the IPC handshake protocol changed between builds, causing a -10005 loop
    # even though the IPC IS connecting (confirmed by +file NtReadFile/NtWriteFile trace).
    echo "[mt5-probe] Upgrading MetaTrader5 package to match terminal build..." >&2
    # Write to a temp FILE (not a pipe) — piping through `tail` keeps the pipe
    # open when `timeout` kills the Wine loader but wineserver survives, causing
    # `tail` to block indefinitely and silently stall the entire probe section.
    _PIP_TMP="/tmp/mt5-pip-upgrade-$$"
    WINEDEBUG="-all" timeout 120 "$WINE_CMD" "$FOUND_PYTHON" -m pip install \
      --upgrade MetaTrader5 --quiet > "${_PIP_TMP}" 2>&1 || true
    tail -5 "${_PIP_TMP}" >&2 2>/dev/null || true
    rm -f "${_PIP_TMP}" 2>/dev/null || true
    _VER_TMP="/tmp/mt5-ver-$$"
    WINEDEBUG="-all" timeout 20 "$WINE_CMD" "$FOUND_PYTHON" \
      -c "import MetaTrader5 as m; print(getattr(m,'__version__','?'))" \
      > "${_VER_TMP}" 2>&1 || true
    echo "[mt5-probe] MetaTrader5 package after upgrade: $(cat "${_VER_TMP}" 2>/dev/null || echo 'unknown')" >&2
    rm -f "${_VER_TMP}" 2>/dev/null || true

    # -----------------------------------------------------------------------
    # Gate: wait for MT5 IPC pipe to actually exist before probing.
    # On cold starts MT5 may be compiling/updating; IPC pipes can appear late.
    # -----------------------------------------------------------------------
    _PIPE_STATUS_TMP="/tmp/mt5-pipe-status-$$"
    _pipe_has_any() {
      # Returns 0 when \\.\pipe\ has any entries beyond the header.
      # Wine's `dir \\.\pipe\` prints a header even when empty.
      timeout 10 "$WINE_CMD" cmd /c "dir \\\\.\\pipe\\\\" 2>/dev/null \
        | tr -d '\r' \
        | awk '
            BEGIN{found=0}
            /^\\s*Directory of \\\\.\\\\pipe\\\\/ {next}
            /^\\s*Volume in drive/ {next}
            /^\\s*Volume Serial Number/ {next}
            /^\\s*File Not Found/ {next}
            /^[[:space:]]*$/ {next}
            {found=1}
            END{exit(found?0:1)}
          '
    }

    PIPE_WAITED=0
    PIPE_MAX=600   # 10 minutes
    while (( PIPE_WAITED < PIPE_MAX )); do
      if _pipe_has_any; then
        echo "[mt5-probe] Detected one or more Wine named pipes (\\.\pipe\\). Proceeding with IPC probe." >&2
        break
      fi
      echo "waiting: mt5_pipe_absent elapsed=${PIPE_WAITED}s" > "${IPC_STATUS_FILE}" 2>/dev/null || true
      sleep 5
      PIPE_WAITED=$((PIPE_WAITED + 5))
    done

    # Enable xtrace from here so every probe command is visible in the wrapper log.
    # This lets us pinpoint exactly which line fails in the probe loop.
    set -x

    # Probe MT5 IPC readiness using direct Wine Python initialize() calls.
    MAX_ATTEMPTS=40
    SLEEP_SECONDS=5
    ATTEMPT=0
    while (( ATTEMPT < MAX_ATTEMPTS )); do
      ATTEMPT=$((ATTEMPT + 1))
      # Snapshot visible X11 window titles — written BEFORE the probe fires so
      # the wrapper log shows what was on screen at each attempt.
      _WIN_SNAP=$(DISPLAY="${DISPLAY}" xdotool search --onlyvisible 2>/dev/null \
        | while IFS= read -r _wsid; do
            DISPLAY="${DISPLAY}" xdotool getwindowname "${_wsid}" 2>/dev/null || true
          done 2>/dev/null | paste -sd'|' - 2>/dev/null) || _WIN_SNAP=""
      { echo "[pre-probe ${ATTEMPT}/${MAX_ATTEMPTS}] windows=${_WIN_SNAP}"; } \
        >> "${IPC_PROBE_LOG}" 2>/dev/null || true
      unset _WIN_SNAP _wsid
      # Skip the terminal-pid liveness check: the terminal may restart itself
      # after applying a LiveUpdate. The probe will detect the new process.

      # Build a single-mode initialize() probe script.
      # We run each mode in an isolated subprocess so one hung initialize() call
      # cannot block the entire attempt sequence.
      { set +x; } 2>/dev/null || true
      PROBE_SCRIPT=$(cat <<PYEOF
import MetaTrader5 as mt5
import os
try:
    mt5_ver = getattr(mt5, '__version__', 'unknown')
except Exception:
    mt5_ver = 'error'
TERM_PATH = r'C:\\Program Files\\MetaTrader 5\\terminal64.exe'
PORTABLE_MODE = os.environ.get('PROBE_PORTABLE', 'default')
MODE = os.environ.get('PROBE_MODE', 'bare_no_path')
kwargs = {'timeout': 30000}  # 30 s — enough for a cold broker TCP handshake
if PORTABLE_MODE == 'portable':
    kwargs['portable'] = True
if MODE == 'bare_path':
    kwargs['path'] = TERM_PATH
ok = False
try:
    ok = mt5.initialize(**kwargs)
except Exception:
    ok = False
err = mt5.last_error()
mt5.shutdown()
print(f'mt5_pkg={mt5_ver} mode={MODE} ok={ok} err={err}')
PYEOF
)
      set -x
      PROBE_OK=0
      PROBE_SUMMARY="none"
      if [[ "${MT5_CONTEXT_MODE}" == "portable" ]]; then
        PROBE_PORTABLE_VALUES=(portable default)
      else
        PROBE_PORTABLE_VALUES=(default portable)
      fi
      for PROBE_PORTABLE in "${PROBE_PORTABLE_VALUES[@]}"; do
        for PROBE_MODE in bare_no_path bare_path; do
        PROBE_EXIT=0
        # Kill orphaned Wine-side python.exe from the previous probe.
        # wine taskkill targets ONLY the Windows process — it does NOT kill
        # wineserver, terminal64.exe, or the mt5linux RPyC server (-m mt5linux).
        WINEDEBUG="-all" "${WINE_CMD}" taskkill /F /IM python.exe > /dev/null 2>&1 || true
        pkill -f "wine.*python.*-c" > /dev/null 2>&1 || true
        sleep 1
        _PROBE_TMP="/tmp/mt5-probe-${ATTEMPT}-${PROBE_MODE}-${PROBE_PORTABLE}"
        rm -f "$_PROBE_TMP" 2>/dev/null || true
        PROBE_MODE="${PROBE_MODE}" PROBE_PORTABLE="${PROBE_PORTABLE}" WINEDEBUG="-all" timeout 35 "$WINE_CMD" "$FOUND_PYTHON" -c "$PROBE_SCRIPT" \
          > "$_PROBE_TMP" 2>&1 || PROBE_EXIT=$?
        PROBE_OUT=$(cat "$_PROBE_TMP" 2>/dev/null) || true
        rm -f "$_PROBE_TMP" 2>/dev/null || true
        PROBE_SUMMARY="portable_mode=${PROBE_PORTABLE} mode=${PROBE_MODE} exit=${PROBE_EXIT} output=${PROBE_OUT}"
        {
          echo "[attempt ${ATTEMPT}/${MAX_ATTEMPTS}] ${PROBE_SUMMARY}"
        } >> "${IPC_PROBE_LOG}" 2>/dev/null || true
        if [[ "$PROBE_OUT" == *"ok=True"* ]]; then
          PROBE_OK=1
          break
        fi
        done
        if [[ "${PROBE_OK}" -eq 1 ]]; then
          break
        fi
      done

      if [[ "${PROBE_OK}" -eq 1 ]]; then
        echo "ready: attempt=${ATTEMPT} ${PROBE_SUMMARY}" > "${IPC_STATUS_FILE}" 2>/dev/null || true
        touch "${IPC_READY_FILE}" 2>/dev/null || true
        break
      fi

      if [[ "${PROBE_SUMMARY}" == *"-10005"* ]]; then
        IPC_TIMEOUT_STREAK=$((IPC_TIMEOUT_STREAK + 1))
      else
        IPC_TIMEOUT_STREAK=0
      fi
      if (( IPC_TIMEOUT_STREAK >= 6 && IPC_RESTART_COUNT < 2 )); then
        IPC_RESTART_COUNT=$((IPC_RESTART_COUNT + 1))
        IPC_TIMEOUT_STREAK=0
        echo "[mt5-terminal] restarting terminal after repeated -10005 (restart=${IPC_RESTART_COUNT})" \
          >> "${IPC_PROBE_LOG}" 2>/dev/null || true
        kill "${TERMINAL_PID}" 2>/dev/null || true
        sleep 2
        _launch_terminal
        sleep 5
      fi

      echo "waiting: attempt=${ATTEMPT} ${PROBE_SUMMARY}" > "${IPC_STATUS_FILE}" 2>/dev/null || true
      sleep "${SLEEP_SECONDS}"
    done

    if [[ ! -f "${IPC_READY_FILE}" ]]; then
      echo "failed: attempts_exhausted output=$(tail -n 1 "${IPC_PROBE_LOG}" 2>/dev/null || echo none)" > "${IPC_STATUS_FILE}" 2>/dev/null || true
      touch "${IPC_FAILED_FILE}" 2>/dev/null || true
    fi
  fi

  # Keep wrapper attached to terminal lifecycle.
  wait "${TERMINAL_PID}" || true
) > /tmp/mt5-launch-wrapper.log 2>&1 &

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
