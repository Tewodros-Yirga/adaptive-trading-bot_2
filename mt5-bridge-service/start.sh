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
WINESERVER_TIMELINE="${LOGDIR}/wineserver-timeline.log"

_ws_pids() {
  pgrep -fa wineserver 2>/dev/null | awk '{print $1}' | tr '\n' ',' | sed 's/,$//' || true
}

_log_ws() {
  local _tag="$1"
  local _stamp
  _stamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  local _pids
  _pids="$(_ws_pids)"
  echo "[${_stamp}] tag=${_tag} wineserver_pids=${_pids:-none}" >> "${WINESERVER_TIMELINE}" 2>/dev/null || true
}

echo "[start] wineserver_version=$(wineserver -v 2>/dev/null || echo unknown)"
_log_ws "start_sh_entry"

# ── Block MT5 LiveUpdate domains NOW — before Xvfb/Wine process any DNS ──────
# Must run here (before any subprocess) so the terminal binary never resolves
# update CDNs on startup. Covers the original set plus extra domains.
for _d in \
  live.mql5.com updates.mql5.com update.mql5.com \
  download.mql5.com cdn.mql5.com ec.mql5.com files.mql5.com \
  cloud.mql5.com gate.mql5.com api.mql5.com \
  www.mql5.com mql5.com \
  update.metatrader5.com updates.metatrader5.com \
  mt5-update.metaquotes.net metaquotes.net; do
  grep -qF "${_d}" /etc/hosts 2>/dev/null || \
    echo "0.0.0.0 ${_d}" >> /etc/hosts 2>/dev/null || true
done
unset _d

# ── Disable Wine UAC elevation prompts ────────────────────────────────────────
# LiveUpdate tries to run with admin privileges → Wine shows a UAC dialog
# ("For updating, LiveUpdate needs administration"). Deny all elevation
# requests by setting Wine's security policy to always run as user.
WINEDEBUG=-all wine reg add \
  'HKCU\Software\Wine\DllOverrides' /v 'winebus.sys' /t REG_SZ /d '' /f \
  2>/dev/null || true
# Kill LiveUpdate client if already running
WINEDEBUG=-all wineserver -k 2>/dev/null || true
sleep 1

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
# Configure Openbox to auto-focus new windows — without this, focus-stealing-
# prevention blocks the Login/Authorization dialog from getting X11 focus,
# causing xdotool key Return to fire on the wrong window.
mkdir -p "${HOME}/.config/openbox"
cat > "${HOME}/.config/openbox/rc.xml" << 'RCEOF'
<?xml version="1.0" encoding="UTF-8"?>
<openbox_config xmlns="http://openbox.org/3.4/rc" xmlns:xi="http://www.w3.org/2001/XInclude">
  <focus>
    <focusNew>yes</focusNew>
    <followMouse>yes</followMouse>
    <focusLast>yes</focusLast>
    <underMouse>no</underMouse>
    <focusDelay>0</focusDelay>
    <raiseOnFocus>no</raiseOnFocus>
  </focus>
  <mouse>
    <dragThreshold>1</dragThreshold>
    <doubleClickTime>200</doubleClickTime>
    <screenEdgeWarpTime>0</screenEdgeWarpTime>
    <screenEdgeWarpMouse>false</screenEdgeWarpMouse>
  </mouse>
</openbox_config>
RCEOF
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

    # Start mt5linux immediately — do NOT wait for the TCP gate's ipc_ready sentinel.
    # The TCP gate (terminal launcher subshell) ALSO checks port 18812 to write ipc_ready,
    # creating a circular deadlock: each waits for the other. Fix: mt5linux starts first,
    # then the TCP gate (or this launcher) writes ipc_ready once the port is open.
    echo "[mt5linux-launcher] Launching mt5linux RPyC server on 127.0.0.1:18812"
    wine "${FOUND_PYTHON}" -m mt5linux --host 127.0.0.1 --port 18812 2>&1 \
      | tee "${LOGDIR}/mt5linux.log" &

    # Wait for the RPyC port to open (up to 60s).
    MT5LINUX_PORT_OPEN=false
    for _ in $(seq 1 60); do
      if (echo > /dev/tcp/127.0.0.1/18812) > /dev/null 2>&1; then
        MT5LINUX_PORT_OPEN=true
        break
      fi
      sleep 1
    done

    if [[ "$MT5LINUX_PORT_OPEN" == "true" ]]; then
      echo "[mt5linux-launcher] mt5linux RPyC port is OPEN on 127.0.0.1:18812"
      # Write ipc_ready directly — this is the definitive signal.
      # The TCP gate may have already timed out (ipc_failed); clear it and set ready.
      rm -f "${LOGDIR}/mt5_ipc.failed" 2>/dev/null || true
      echo "ready: mt5linux_port_open" > "${LOGDIR}/mt5_ipc.status" 2>/dev/null || true
      touch "${LOGDIR}/mt5_ipc.ready" 2>/dev/null || true
      echo "[mt5linux-launcher] mt5_ipc.ready written — adapter cleared to connect."
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
  DISMISS_LOG="${LOGDIR}/mt5-dismiss.log"
  CONTEXT_STATUS_FILE="${LOGDIR}/mt5_context.status"
  rm -f "${IPC_READY_FILE}" "${IPC_FAILED_FILE}" "${IPC_STATUS_FILE}" "${IPC_PROBE_LOG}" "${DISMISS_LOG}" "${CONTEXT_STATUS_FILE}" 2>/dev/null || true
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

  # ── Lock terminal binaries READ-ONLY before launch ──────────────────────────
  # LiveUpdate can download a newer build (e.g. 5833) that has NO matching
  # MetaTrader5 Python package on PyPI (latest is 5.0.5735). When the terminal
  # and Python package build numbers don't match, mt5.initialize() returns
  # -10005 (IPC timeout) on every call. Locking the .exe/.dll files prevents
  # LiveUpdate from overwriting them; the terminal runs fine but stays at the
  # pre-baked build that matches the installed Python package.
  _TERM_DIR="${WINEPREFIX}/drive_c/Program Files/MetaTrader 5"
  echo "[mt5-terminal] Locking terminal binaries to prevent LiveUpdate version drift..."
  find "${_TERM_DIR}" \( -name '*.exe' -o -name '*.dll' \) \
    -exec chmod a-w {} \; 2>/dev/null || true
  echo "[mt5-terminal] Terminal binaries locked (read-only)."
  unset _TERM_DIR

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
    _log_ws "terminal_launch_pid_${TERMINAL_PID}"
  }
  _launch_terminal
  LAST_WS_PIDS="$(_ws_pids)"
  echo "[mt5-terminal] wineserver_initial_pids=${LAST_WS_PIDS:-none}"
  IPC_TIMEOUT_STREAK=0
  IPC_RESTART_COUNT=0

  # Fallback dialog dismisser: if a LiveUpdate domain was missed and the
  # "Restart to install" dialog still appears, dismiss it via xdotool.
  # Note: xdotool --name is a plain substring match — do NOT use | here
  # (e.g. "A|B" looks for a title literally containing a pipe character).
  # LiveUpdate dialogs are often centered; Restart/Later sit lower than
  # older wizard-nested coords (y ~400–430 on 1280x720), not y ~330.
  DISMISS_LOG="${LOGDIR}/mt5-dismiss.log"
  (
    # Disable pipefail inside subshell — xdotool exits non-zero when no
    # windows are found, which would abort the loop under set -e.
    set +euo pipefail || true
    _xd() { DISPLAY=:99 xdotool "$@" 2>/dev/null || true; }
    _uniq_ids() { awk 'NF' | sort -n -u; }
    trap 'echo "[dismiss-loop] exit=$?" >> "${DISMISS_LOG}"' EXIT
    sleep 8
    for _try in $(seq 1 120); do
      # Step 1: Press Return on the currently focused window.
      # This dismisses the IPC Login/Auth dialog which is an embedded
      # Win32 modal inside the MT5 main X11 window (not a separate
      # X11 window). Do NOT do a coordinate sweep — blind clicks at
      # fixed coords land inside other dialogs (e.g. 'Select a company')
      # and accidentally advance through wizards.
      _xd key Return; sleep 0.1
      _ACTIVE_WID=$(_xd getactivewindow 2>/dev/null || echo "none")
      _ACTIVE_NAME=$(DISPLAY=:99 xdotool getwindowname "${_ACTIVE_WID}" 2>/dev/null || echo "?")
      echo "[dismiss-heartbeat] iter=${_try} focus=${_ACTIVE_WID}(${_ACTIVE_NAME})" >> "${DISMISS_LOG}"
      # Step 2: LiveUpdate dialogs (including UAC elevation prompt).
      # "Updating MetaTrader 5" = Wine UAC/admin dialog — click CANCEL.
      for _wid in $({ _xd search --onlyvisible --name "Updating MetaTrader"
                      _xd search --onlyvisible --name LiveUpdate
                      _xd search --onlyvisible --name "Welcome to"; } | _uniq_ids); do
        _NAME=$(_xd getwindowname "${_wid}" 2>/dev/null || echo "?")
        echo "[dismiss-action] iter=${_try} wid=${_wid} name=${_NAME}" >> "${DISMISS_LOG}"
        _xd windowactivate --sync "${_wid}"; sleep 0.2
        if echo "${_NAME}" | grep -qi "Updating"; then
          # Click Cancel button (right button, x≈598 y≈377 on 1280x720)
          # and press Escape — we do NOT want to authorize the update.
          _xd mousemove --clearmodifiers 598 377; sleep 0.1
          _xd click 1; sleep 0.1
          _xd key --window "${_wid}" --clearmodifiers Escape
          echo "[dismiss-action] Cancelled LiveUpdate UAC dialog" >> "${DISMISS_LOG}"
        else
          for _xy in "548 418" "638 418" "728 418" "520 402" "600 428"; do
            set -- ${_xy}; _xd mousemove --clearmodifiers "$1" "$2" click 1; sleep 0.06
          done
          _xd key --window "${_wid}" --clearmodifiers Return
        fi
      done
      # Step 3: First-run wizard dialogs — press Escape to CANCEL them.
      # Do NOT type server names or click Next — the terminal's pre-baked
      # AppData config handles auth automatically. Advancing the wizard
      # causes a MetaQuotes account setup flow that blocks IPC.
      for _wid in $({ _xd search --onlyvisible --name "Select a company"
                      _xd search --onlyvisible --name "open an account"
                      _xd search --onlyvisible --name "Setup"
                      _xd search --onlyvisible --name "Open an Account"; } | _uniq_ids); do
        _WIZ_NAME=$(_xd getwindowname "${_wid}" 2>/dev/null || echo "?")
        echo "[dismiss-action] iter=${_try} Escaping wizard wid=${_wid} name=${_WIZ_NAME}" >> "${DISMISS_LOG}"
        _xd windowactivate --sync "${_wid}"; sleep 0.2
        _xd key --window "${_wid}" --clearmodifiers Escape; sleep 0.1
        _xd key --window "${_wid}" --clearmodifiers Escape
      done
      # Step 4: Re-press Return (catches Login dialog that appeared during steps 2/3).
      _xd key Return
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
    # Upgrade MetaTrader5 package inside Wine so it matches the terminal build.
    # A version mismatch in the Windows-side package still matters because the
    # mt5linux RPyC server (wine python.exe -m mt5linux) calls mt5.initialize()
    # from within Wine — same wineserver as the terminal — which IS reliable.
    echo "[mt5-probe] Upgrading MetaTrader5 package to match terminal build..." >&2
    _PIP_TMP="/tmp/mt5-pip-upgrade-$$"
    # Detect the terminal build number from the binary so we can install the
    # exact matching MetaTrader5 Python package. Build N maps to package 5.0.N.
    # If that exact version is not on PyPI yet, fall back to --upgrade (latest).
    _TERM_BUILD=$(strings "${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/terminal64.exe" \
      2>/dev/null | grep -oP 'build \K[0-9]{4,5}' | tail -1 || true)
    if [[ -n "${_TERM_BUILD}" ]]; then
      _PKG_VER="5.0.${_TERM_BUILD}"
      echo "[mt5-probe] Terminal build=${_TERM_BUILD} → trying MetaTrader5==${_PKG_VER}" >&2
      WINEDEBUG="-all" timeout 120 "$WINE_CMD" "$FOUND_PYTHON" -m pip install \
        "MetaTrader5==${_PKG_VER}" --quiet >"${_PIP_TMP}" 2>&1 \
        || WINEDEBUG="-all" timeout 120 "$WINE_CMD" "$FOUND_PYTHON" -m pip install \
             --upgrade MetaTrader5 --quiet >>"${_PIP_TMP}" 2>&1 || true
    else
      echo "[mt5-probe] Could not detect terminal build; installing latest MetaTrader5" >&2
      WINEDEBUG="-all" timeout 120 "$WINE_CMD" "$FOUND_PYTHON" -m pip install \
        --upgrade MetaTrader5 --quiet >"${_PIP_TMP}" 2>&1 || true
    fi
    tail -5 "${_PIP_TMP}" >&2 2>/dev/null || true
    rm -f "${_PIP_TMP}" 2>/dev/null || true
    _VER_TMP="/tmp/mt5-ver-$$"
    WINEDEBUG="-all" timeout 20 "$WINE_CMD" "$FOUND_PYTHON" \
      -c "import MetaTrader5 as m; print(getattr(m,'__version__','?'))" \
      > "${_VER_TMP}" 2>&1 || true
    echo "[mt5-probe] MetaTrader5 pkg version: $(cat "${_VER_TMP}" 2>/dev/null || echo 'unknown')" >&2
    rm -f "${_VER_TMP}" 2>/dev/null || true

    # -----------------------------------------------------------------------
    # TCP Connectivity Gate — replaces the cross-process wine-python probe.
    #
    # WHY THE CROSS-PROCESS PROBE WAS REMOVED:
    # The MetaTrader5 Python package uses Windows named pipes + shared memory
    # (CreateFileMapping / MapViewOfFile) for its IPC handshake. Wine's
    # emulation of MapViewOfFile across SEPARATE processes is incomplete —
    # even in the same wineprefix/wineserver. Every ephemeral
    # "wine python.exe -c mt5.initialize()" probe returns -10005 regardless
    # of terminal state, dialog state, or timeout length. This is a Wine
    # architectural limitation, not a configuration problem.
    #
    # WHY mt5linux WORKS:
    # The mt5linux RPyC server (wine python.exe -m mt5linux) is a PERSISTENT
    # Wine process sharing the same wineserver session as terminal64.exe.
    # IPC within a single wineserver session is reliable. The Linux adapter
    # connects via TCP loopback (no Windows IPC from Linux at all).
    #
    # GATE LOGIC:
    # When terminal64.exe has established external TCP connections to the
    # broker, it is stable and IPC-ready. Write mt5_ipc.ready to unblock
    # the adapter — it will then connect via mt5linux (TCP → Wine RPyC →
    # Windows IPC → terminal), which is the correct production path.
    # -----------------------------------------------------------------------
    echo "[mt5-probe] Starting TCP connectivity gate (replaces cross-process IPC probe)..."
    TCP_GATE_PASSED=false
    TCP_WAITED=0
    TCP_MAX=600   # 10 minutes max

    while (( TCP_WAITED < TCP_MAX )); do
      # Crash / self-restart guard.
      if ! kill -0 "${TERMINAL_PID}" 2>/dev/null; then
        echo "[mt5-probe] terminal64.exe (PID ${TERMINAL_PID}) has exited" >&2
        NEW_TERM_PID=$(pgrep -f 'terminal64.exe' 2>/dev/null | head -1) || true
        if [[ -n "${NEW_TERM_PID}" ]] && [[ "${NEW_TERM_PID}" != "${TERMINAL_PID}" ]]; then
          echo "[mt5-probe] New terminal PID detected: ${NEW_TERM_PID} (self-restart after update)" >&2
          TERMINAL_PID="${NEW_TERM_PID}"
        else
          echo "[mt5-probe] FATAL: terminal exited and did not restart." >&2
          echo "failed: terminal_exited_during_tcp_gate elapsed=${TCP_WAITED}s" \
            > "${IPC_STATUS_FILE}" 2>/dev/null || true
          touch "${IPC_FAILED_FILE}" 2>/dev/null || true
          break
        fi
      fi

      # Primary gate: mt5linux RPyC server on port 18812.
      # The adapter connects Linux→TCP:18812→Wine RPyC→Windows IPC→terminal.
      # This port opens once the mt5linux launcher (parallel subshell) starts
      # wine python.exe -m mt5linux inside the same wineserver as terminal64.exe.
      MT5LINUX_PORT=0
      if (echo > /dev/tcp/127.0.0.1/18812) > /dev/null 2>&1; then MT5LINUX_PORT=1; fi

      # Secondary gate: external broker TCP connections (belt + suspenders).
      # On HF Spaces the broker (MetaQuotes) may be unreachable, so this is
      # NOT required — mt5linux alone is sufficient.
      ESTABLISHED=$(ss -tn state established 2>/dev/null \
        | tail -n +2 \
        | grep -v '127\.' \
        | grep -v ' ::1[: ]' \
        | wc -l) || ESTABLISHED=0

      # Snapshot visible windows for diagnostics.
      _WIN_SNAP=$(DISPLAY="${DISPLAY}" xdotool search --onlyvisible 2>/dev/null \
        | while IFS= read -r _wsid; do
            DISPLAY="${DISPLAY}" xdotool getwindowname "${_wsid}" 2>/dev/null || true
          done 2>/dev/null | paste -sd'|' - 2>/dev/null) || _WIN_SNAP=""

      { echo "[tcp-gate elapsed=${TCP_WAITED}s] established=${ESTABLISHED} mt5linux_port=${MT5LINUX_PORT} windows=${_WIN_SNAP:-none}"; } \
        >> "${IPC_PROBE_LOG}" 2>/dev/null || true
      echo "waiting: tcp_check elapsed=${TCP_WAITED}s established=${ESTABLISHED} mt5linux=${MT5LINUX_PORT}" \
        > "${IPC_STATUS_FILE}" 2>/dev/null || true
      _log_ws "tcp_gate_${TCP_WAITED}s"

      if (( MT5LINUX_PORT > 0 )) || (( ESTABLISHED > 0 )); then
        echo "[mt5-probe] TCP gate PASSED — terminal has ${ESTABLISHED} external TCP connection(s)"
        { echo "[tcp-gate PASSED elapsed=${TCP_WAITED}s] established=${ESTABLISHED}"; } \
          >> "${IPC_PROBE_LOG}" 2>/dev/null || true
        TCP_GATE_PASSED=true
        break
      fi

      sleep 10
      TCP_WAITED=$(( TCP_WAITED + 10 ))
    done

    if [[ "${TCP_GATE_PASSED}" == "true" ]]; then
      echo "ready: tcp_connected elapsed=${TCP_WAITED}s" > "${IPC_STATUS_FILE}" 2>/dev/null || true
      touch "${IPC_READY_FILE}" 2>/dev/null || true
      echo "[mt5-probe] mt5_ipc.ready written — adapter will connect via mt5linux TCP bridge"
      {
        echo "=== TCP gate PASSED diagnostics ==="
        echo "elapsed=${TCP_WAITED}s established=${ESTABLISHED:-?}"
        echo "=== dismiss log tail ==="
        tail -n 40 "${DISMISS_LOG}" 2>/dev/null || true
        echo "=== wineserver timeline tail ==="
        tail -n 20 "${WINESERVER_TIMELINE}" 2>/dev/null || true
      } >> "${IPC_PROBE_LOG}" 2>/dev/null || true
    else
      if [[ ! -f "${IPC_FAILED_FILE}" ]]; then
        echo "failed: tcp_gate_timeout elapsed=${TCP_WAITED}s" \
          > "${IPC_STATUS_FILE}" 2>/dev/null || true
        {
          echo "=== TCP gate TIMEOUT diagnostics ==="
          echo "elapsed=${TCP_WAITED}s"
          echo "=== dismiss log tail ==="
          tail -n 80 "${DISMISS_LOG}" 2>/dev/null || true
          echo "=== wineserver timeline tail ==="
          tail -n 40 "${WINESERVER_TIMELINE}" 2>/dev/null || true
          echo "=== terminal.log tail ==="
          tail -n 100 "${LOGDIR}/mt5-terminal.log" 2>/dev/null || true
        } >> "${IPC_PROBE_LOG}" 2>/dev/null || true
        touch "${IPC_FAILED_FILE}" 2>/dev/null || true
      fi
    fi
  fi

  # Keep wrapper attached to terminal lifecycle.
  wait "${TERMINAL_PID}" || true
) 2>&1 | tee /tmp/mt5-launch-wrapper.log &

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
