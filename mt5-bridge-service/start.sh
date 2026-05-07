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
      # Do NOT write mt5_ipc.ready here — the TCP gate (terminal launcher subshell)
      # is the sole writer. It enforces a grace period after terminal self-restart
      # before signalling the adapter, preventing -10003 (terminal not found).
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
  # Auto-detect portable installation: if terminal.ini exists alongside terminal64.exe,
  # the base image was baked in portable mode. Override MT5_CONTEXT_MODE to portable so
  # the terminal finds its pre-baked data dir (exe dir) instead of searching AppData
  # (which was never set up). Without this, the terminal shows the first-run wizard and
  # never creates the IPC named pipe → mt5.initialize() returns -10003 forever.
  _TERM_INI_PORTABLE="$(dirname "${TERMINAL_EXE}")/terminal.ini"
  if [[ "${MT5_CONTEXT_MODE}" == "default" ]] || [[ -z "${MT5_CONTEXT_MODE}" ]]; then
    # Prefer AppData/default mode for determinism.
    # Portable auto-force can be re-enabled only when explicitly requested.
    if [[ "${MT5_FORCE_PORTABLE_IF_TERMINAL_INI:-false}" == "true" ]] && [[ -f "${_TERM_INI_PORTABLE}" ]]; then
      echo "[mt5-terminal] Portable terminal.ini detected alongside exe — forcing portable mode (MT5_FORCE_PORTABLE_IF_TERMINAL_INI=true)"
      MT5_CONTEXT_MODE="portable"
    fi
  fi
  case "${MT5_CONTEXT_MODE}" in
    portable)
      CONTEXT_ARGS+=("/portable")
      echo "[mt5-terminal] MT5_CONTEXT_MODE=portable; using portable (exe-dir) mode"
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
    TERMINAL_CHILD_PID=$!
    TERMINAL_RUNTIME_PID="${TERMINAL_CHILD_PID}"
    echo "[mt5-terminal] terminal pid=${TERMINAL_RUNTIME_PID} (child_pid=${TERMINAL_CHILD_PID})"
    _log_ws "terminal_launch_pid_${TERMINAL_RUNTIME_PID}"
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
      # Step 4: Enumerate ALL windows owned by the terminal process (including
      # hidden/embedded child windows not found by --onlyvisible). This catches
      # the API authorization dialog that the terminal shows when mt5.initialize()
      # is called — it may not be visible to xdotool --onlyvisible but still
      # blocks the IPC pump. Log all found windows for diagnostics.
      if [ "${_try}" -ge 15 ]; then
        _TERM_PID=$(DISPLAY=:99 xdotool search --onlyvisible --class "explorer.exe" 2>/dev/null \
          | head -1 || true)
        # Get all wine-related pids (terminal64.exe is a wine child process)
        _WINE_PIDS=$(pgrep -f "terminal64" 2>/dev/null | head -5 || true)
        for _wpid in ${_WINE_PIDS}; do
          _W_WIDS=$(DISPLAY=:99 xdotool search --pid "${_wpid}" 2>/dev/null || true)
          for _ww in ${_W_WIDS}; do
            _wn=$(DISPLAY=:99 xdotool getwindowname "${_ww}" 2>/dev/null || echo "?")
            # Only log unnamed or auth-looking windows to avoid log spam
            case "${_wn}" in
              ""|"?"|\
              *"Allow"*|*"Deny"*|*"Authorization"*|*"Access"*|\
              *"Login"*|*"Connect"*)
                echo "[dismiss-tree] iter=${_try} pid=${_wpid} wid=${_ww} name=${_wn}" \
                  >> "${DISMISS_LOG}"
                # Click at center of the window (likely the Allow/OK button area)
                eval "$(_xd getwindowgeometry --shell "${_ww}" 2>/dev/null || true)"
                if [ -n "${WIDTH:-}" ] && [ "${WIDTH}" -gt 50 ] 2>/dev/null; then
                  _cx=$(( X + WIDTH / 2 ))
                  _cy=$(( Y + HEIGHT * 65 / 100 ))
                  echo "[dismiss-tree] clicking Allow area at ${_cx},${_cy} wid=${_ww}" \
                    >> "${DISMISS_LOG}"
                  _xd windowactivate --sync "${_ww}"; sleep 0.1
                  _xd mousemove --clearmodifiers "${_cx}" "${_cy}"; sleep 0.05
                  _xd click 1; sleep 0.05
                  _xd key --window "${_ww}" --clearmodifiers Return
                fi
                ;;
            esac
          done
        done
      fi
      # Step 5: Re-press Return on focused window (catches any auth dialog that
      # gained focus during steps 2-4).
      _xd key --clearmodifiers Return
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

    # ── Multi-method terminal build detection ──────────────────────────────
    # Method 1: PE version info (most reliable — reads the embedded version
    # resource from terminal64.exe without depending on string patterns).
    _TERM_EXE="${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/terminal64.exe"
    _TERM_BUILD=""

    # Method 1a: parse "FileVersion: X.Y.Z.BUILD" from exiftool.
    if [[ -z "${_TERM_BUILD}" ]] && command -v exiftool &>/dev/null; then
      _TERM_BUILD=$(exiftool -FileVersion "${_TERM_EXE}" 2>/dev/null \
        | grep -oE '[0-9]+$' || true)
      [[ -n "${_TERM_BUILD}" ]] && \
        echo "[mt5-probe] Build detected via exiftool: ${_TERM_BUILD}" >&2
    fi

    # Method 1b: strings (ASCII) — look for "build NNNN".
    if [[ -z "${_TERM_BUILD}" ]]; then
      _TERM_BUILD=$(strings "${_TERM_EXE}" 2>/dev/null \
        | grep -oE 'build [0-9]{4,5}' | tail -1 | sed 's/build //' || true)
      [[ -n "${_TERM_BUILD}" ]] && \
        echo "[mt5-probe] Build detected via strings(ASCII): ${_TERM_BUILD}" >&2
    fi

    # Method 1c: strings UTF-16LE — Windows PE stores version strings as
    # wide chars (UTF-16LE). The default `strings` only finds ASCII.
    if [[ -z "${_TERM_BUILD}" ]]; then
      _TERM_BUILD=$(strings -e l "${_TERM_EXE}" 2>/dev/null \
        | grep -oE 'build [0-9]{4,5}' | tail -1 | sed 's/build //' || true)
      [[ -n "${_TERM_BUILD}" ]] && \
        echo "[mt5-probe] Build detected via strings(UTF16LE): ${_TERM_BUILD}" >&2
    fi

    # Method 1d: strings UTF-16BE fallback.
    if [[ -z "${_TERM_BUILD}" ]]; then
      _TERM_BUILD=$(strings -e b "${_TERM_EXE}" 2>/dev/null \
        | grep -oE 'build [0-9]{4,5}' | tail -1 | sed 's/build //' || true)
      [[ -n "${_TERM_BUILD}" ]] && \
        echo "[mt5-probe] Build detected via strings(UTF16BE): ${_TERM_BUILD}" >&2
    fi

    # Method 2: Wine-based PE version info query.
    if [[ -z "${_TERM_BUILD}" ]]; then
      echo "[mt5-probe] Trying Wine PE version query..." >&2
      _WIN_PATH=$(echo "${_TERM_EXE}" | sed "s|${WINEPREFIX}/drive_c/|C:\\\\|;s|/|\\\\|g")
      _PE_VER=$(WINEDEBUG="-all" timeout 15 "${WINE_CMD:-wine}" cmd /c \
        "wmic datafile where \"name='${_WIN_PATH}'\" get version /format:list" \
        2>/dev/null | tr -d '\r' | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)
      if [[ -n "${_PE_VER}" ]]; then
        _TERM_BUILD=$(echo "${_PE_VER}" | awk -F. '{print $NF}')
        [[ -n "${_TERM_BUILD}" ]] && \
          echo "[mt5-probe] Build detected via Wine wmic: ${_TERM_BUILD} (ver=${_PE_VER})" >&2
      fi
    fi

    # Method 3: Wait for Journal log (terminal writes it ~10-20s after launch).
    if [[ -z "${_TERM_BUILD}" ]]; then
      echo "[mt5-probe] Waiting up to 30s for Journal log entry..." >&2
      _JOURNAL_FOUND=false
      for _jw in $(seq 1 6); do
        for _jd in \
          "${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/Logs" \
          $(find "${WINEPREFIX}/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal" \
            -mindepth 2 -maxdepth 2 -type d -name "Logs" 2>/dev/null || true); do
          if [[ -d "${_jd}" ]]; then
            _TERM_BUILD=$(grep -rhoE 'build [0-9]{4,5}' "${_jd}" 2>/dev/null \
              | tail -1 | sed 's/build //' || true)
            if [[ -n "${_TERM_BUILD}" ]]; then
              echo "[mt5-probe] Build detected via Journal: ${_TERM_BUILD} (after ${_jw}x5s)" >&2
              _JOURNAL_FOUND=true
              break 2
            fi
          fi
        done
        sleep 5
      done
    fi

    if [[ -z "${_TERM_BUILD}" ]]; then
      echo "[mt5-probe] WARNING: Could not detect terminal build via any method" >&2
      echo "[mt5-probe] Dumping first 20 strings from binary for diagnostics:" >&2
      strings "${_TERM_EXE}" 2>/dev/null | head -20 >&2 || true
      echo "[mt5-probe] Dumping first 20 UTF-16LE strings:" >&2
      strings -e l "${_TERM_EXE}" 2>/dev/null | head -20 >&2 || true
    fi


    # ── Install matching MetaTrader5 Python package ────────────────────────
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

    # ── Read installed package version ─────────────────────────────────────
    _VER_TMP="/tmp/mt5-ver-$$"
    WINEDEBUG="-all" timeout 20 "$WINE_CMD" "$FOUND_PYTHON" \
      -c "import MetaTrader5 as m; print(getattr(m,'__version__','?'))" \
      > "${_VER_TMP}" 2>&1 || true
    _PKG_VER_INSTALLED=$(cat "${_VER_TMP}" 2>/dev/null | tr -d '\r\n' || echo "unknown")
    echo "[mt5-probe] MetaTrader5 pkg version: ${_PKG_VER_INSTALLED}" >&2
    rm -f "${_VER_TMP}" 2>/dev/null || true

    # ── Build-mismatch guard ───────────────────────────────────────────────
    # Extract build number from installed package version (e.g. "5.0.5735" → "5735").
    _PKG_BUILD=$(echo "${_PKG_VER_INSTALLED}" | grep -oE '[0-9]+$' || true)
    _MISMATCH_FILE="${LOGDIR}/build_mismatch"
    if [[ -n "${_TERM_BUILD}" ]] && [[ -n "${_PKG_BUILD}" ]] \
       && [[ "${_TERM_BUILD}" != "${_PKG_BUILD}" ]]; then
      echo "[mt5-probe] ╔════════════════════════════════════════════════════╗" >&2
      echo "[mt5-probe] ║  FATAL: TERMINAL / PACKAGE BUILD MISMATCH         ║" >&2
      echo "[mt5-probe] ║  Terminal build: ${_TERM_BUILD}                          ║" >&2
      echo "[mt5-probe] ║  Package build:  ${_PKG_BUILD}                          ║" >&2
      echo "[mt5-probe] ║                                                    ║" >&2
      echo "[mt5-probe] ║  mt5.initialize() will return -10005 every time.   ║" >&2
      echo "[mt5-probe] ║  Rebuild the base image with a matching terminal.  ║" >&2
      echo "[mt5-probe] ╚════════════════════════════════════════════════════╝" >&2
      echo "terminal=${_TERM_BUILD} package=${_PKG_BUILD}" > "${_MISMATCH_FILE}" 2>/dev/null || true
    else
      rm -f "${_MISMATCH_FILE}" 2>/dev/null || true
      if [[ -n "${_TERM_BUILD}" ]] && [[ -n "${_PKG_BUILD}" ]]; then
        echo "[mt5-probe] ✓ Builds match: terminal=${_TERM_BUILD} package=${_PKG_BUILD}" >&2
      fi
    fi


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
    TERMINAL_STABLE_AFTER=0  # grace period: gate can only pass once TCP_WAITED >= this
    LAST_TERMINAL_RESTART_AT=0
    MT5LINUX_PORT_STABLE_FOR=0
    # Require a sustained-open mt5linux port window to avoid false "ready"
    # right after terminal self-restart.
    MT5LINUX_PORT_STABLE_REQUIRED="${MT5LINUX_PORT_STABLE_REQUIRED:-30}"
    # Named-pipe verification: ensure MT5 IPC is actually registered inside Wine.
    # This prevents "TCP ready but IPC not attachable" which surfaces as
    # terminal_not_found / -10003 on /account.
    IPC_PIPES_REQUIRED_HITS="${IPC_PIPES_REQUIRED_HITS:-2}"
    IPC_PIPES_HITS=0
    IPC_PIPES_LAST_EVIDENCE=""
    IPC_PIPES_LAST_PRESENT=false

    _wine_has_mt5_pipes() {
      # Query Wine's pipe namespace (does not rely on Windows IPC attach).
      # We match only the presence of MT5-family pipes to avoid false positives.
      local _out _ev
      # Use single quotes to avoid backslash-escaping pitfalls in bash.
      _out="$(timeout 8s wine cmd /c 'dir \\.\pipe\\' 2>/dev/null || true)"
      # Evidence: first relevant line containing known MT5 keywords.
      _ev="$(echo "${_out}" | grep -Ei 'metatrader|metaquotes|mt5|terminal' | head -n 1 | tr -d '\r' || true)"
      if [[ -n "${_ev}" ]]; then
        IPC_PIPES_LAST_EVIDENCE="${_ev}"
        IPC_PIPES_LAST_PRESENT=true
        return 0
      fi
      IPC_PIPES_LAST_EVIDENCE=""
      IPC_PIPES_LAST_PRESENT=false
      return 1
    }
    # Headless deployments can keep MT5 functional without an exposed X11
    # window. Keep strict mode available for interactive debugging.
    MT5_REQUIRE_X11_WINDOW="${MT5_REQUIRE_X11_WINDOW:-0}"

    while (( TCP_WAITED < TCP_MAX )); do
      # Crash / self-restart guard.
      if ! kill -0 "${TERMINAL_RUNTIME_PID}" 2>/dev/null; then
        echo "[mt5-probe] terminal64.exe (PID ${TERMINAL_RUNTIME_PID}) has exited" >&2
        NEW_TERM_PID=$(pgrep -f 'terminal64.exe' 2>/dev/null | head -1) || true
        if [[ -n "${NEW_TERM_PID}" ]] && [[ "${NEW_TERM_PID}" != "${TERMINAL_RUNTIME_PID}" ]]; then
          echo "[mt5-probe] New terminal PID detected: ${NEW_TERM_PID} (self-restart after update)" >&2
          TERMINAL_RUNTIME_PID="${NEW_TERM_PID}"
          if [[ -n "${TERMINAL_CHILD_PID:-}" ]] && [[ "${TERMINAL_RUNTIME_PID}" != "${TERMINAL_CHILD_PID}" ]]; then
            echo "[mt5-probe] Terminal PID moved to adopted process (runtime_pid=${TERMINAL_RUNTIME_PID}, child_pid=${TERMINAL_CHILD_PID})" >&2
          fi
          # Enforce 30s grace period before gate can pass — terminal needs time
          # to set up its IPC named pipes after restarting.
          TERMINAL_STABLE_AFTER=$(( TCP_WAITED + 30 ))
          LAST_TERMINAL_RESTART_AT="${TCP_WAITED}"
          MT5LINUX_PORT_STABLE_FOR=0
          echo "[mt5-probe] Grace period: gate will not pass until ${TERMINAL_STABLE_AFTER}s elapsed" >&2
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
      if (( MT5LINUX_PORT > 0 )); then
        MT5LINUX_PORT_STABLE_FOR=$(( MT5LINUX_PORT_STABLE_FOR + 10 ))
      else
        MT5LINUX_PORT_STABLE_FOR=0
      fi

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
      _TERM_WINDOWS=0
      if [[ -n "${TERMINAL_RUNTIME_PID:-}" ]]; then
        _TERM_WINDOWS=$(DISPLAY="${DISPLAY}" xdotool search --pid "${TERMINAL_RUNTIME_PID}" 2>/dev/null | wc -l || echo 0)
      fi

      { echo "[tcp-gate elapsed=${TCP_WAITED}s] established=${ESTABLISHED} mt5linux_port=${MT5LINUX_PORT} mt5linux_port_stable_for=${MT5LINUX_PORT_STABLE_FOR}s term_windows=${_TERM_WINDOWS} windows=${_WIN_SNAP:-none}"; } \
        >> "${IPC_PROBE_LOG}" 2>/dev/null || true
      echo "waiting: tcp_check elapsed=${TCP_WAITED}s established=${ESTABLISHED} mt5linux=${MT5LINUX_PORT} mt5linux_stable_for=${MT5LINUX_PORT_STABLE_FOR}s term_windows=${_TERM_WINDOWS}" \
        > "${IPC_STATUS_FILE}" 2>/dev/null || true
      _log_ws "tcp_gate_${TCP_WAITED}s"

      if (( MT5LINUX_PORT > 0 )) || (( ESTABLISHED > 0 )); then
        _X11_WINDOW_READY=1
        if [[ "${MT5_REQUIRE_X11_WINDOW}" == "1" ]] && (( _TERM_WINDOWS == 0 )); then
          _X11_WINDOW_READY=0
        fi
        if (( TCP_WAITED < TERMINAL_STABLE_AFTER )); then
          echo "[mt5-probe] TCP gate ready but in grace period (${TCP_WAITED}s < ${TERMINAL_STABLE_AFTER}s) — waiting for terminal to stabilize" >&2
        elif (( MT5LINUX_PORT_STABLE_FOR < MT5LINUX_PORT_STABLE_REQUIRED )); then
          echo "[mt5-probe] TCP gate ready but mt5linux port not stable yet (${MT5LINUX_PORT_STABLE_FOR}s < ${MT5LINUX_PORT_STABLE_REQUIRED}s) — waiting" >&2
        elif (( _X11_WINDOW_READY == 0 )); then
          echo "[mt5-probe] TCP gate ready but terminal has no X11 windows yet (pid=${TERMINAL_RUNTIME_PID}) and MT5_REQUIRE_X11_WINDOW=1 — waiting" >&2
        else
          # Soft readiness criteria passed: TCP is stable and grace elapsed.
          # Now verify that Wine has registered MT5 IPC named pipes.
          if _wine_has_mt5_pipes; then
            IPC_PIPES_HITS=$(( IPC_PIPES_HITS + 1 ))
            echo "[mt5-probe] IPC pipe evidence hit ${IPC_PIPES_HITS}/${IPC_PIPES_REQUIRED_HITS}: ${IPC_PIPES_LAST_EVIDENCE}" >&2
            if (( IPC_PIPES_HITS >= IPC_PIPES_REQUIRED_HITS )); then
              _SINCE_RESTART=$(( TCP_WAITED - LAST_TERMINAL_RESTART_AT ))
              _READY_MODE="x11_confirmed"
              if (( _TERM_WINDOWS == 0 )); then
                _READY_MODE="headless_tcp_no_x11"
                echo "[mt5-probe] TCP gate PASSING in headless mode (no X11 terminal windows detected, MT5_REQUIRE_X11_WINDOW=0)" >&2
              fi
              echo "[mt5-probe] TCP gate PASSED — terminal has ${ESTABLISHED} external TCP connection(s), mt5linux port stable for ${MT5LINUX_PORT_STABLE_FOR}s, restart_age=${_SINCE_RESTART}s, ipc_pipes=present"
              { echo "[tcp-gate PASSED elapsed=${TCP_WAITED}s mode=${_READY_MODE} ipc_pipes_hits=${IPC_PIPES_HITS}/${IPC_PIPES_REQUIRED_HITS}] established=${ESTABLISHED} last_evidence=${IPC_PIPES_LAST_EVIDENCE}"; } \
                >> "${IPC_PROBE_LOG}" 2>/dev/null || true
              TCP_GATE_PASSED=true
              break
            fi
          else
            if (( IPC_PIPES_HITS > 0 )); then
              echo "[mt5-probe] IPC pipe evidence disappeared — resetting hits to 0" >&2
            fi
            IPC_PIPES_HITS=0
            echo "[mt5-probe] TCP gate soft-ready but MT5 IPC named pipes not visible in Wine yet — waiting (required_hints=${IPC_PIPES_REQUIRED_HITS})" >&2
          fi
        fi
      fi

      sleep 10
      TCP_WAITED=$(( TCP_WAITED + 10 ))
    done

    if [[ "${TCP_GATE_PASSED}" == "true" ]]; then
      _READY_STATUS_MODE="x11_confirmed"
      if (( _TERM_WINDOWS == 0 )); then
        _READY_STATUS_MODE="headless_tcp_no_x11"
      fi
      echo "ready: tcp_connected elapsed=${TCP_WAITED}s mode=${_READY_STATUS_MODE} mt5linux_port_stable_for=${MT5LINUX_PORT_STABLE_FOR}s require_x11=${MT5_REQUIRE_X11_WINDOW} ipc_pipes=present ipc_pipes_hits=${IPC_PIPES_HITS}/${IPC_PIPES_REQUIRED_HITS} last_evidence=${IPC_PIPES_LAST_EVIDENCE}" > "${IPC_STATUS_FILE}" 2>/dev/null || true
      touch "${IPC_READY_FILE}" 2>/dev/null || true
      echo "[mt5-probe] mt5_ipc.ready written — adapter will connect via mt5linux TCP bridge"
      {
        echo "=== TCP gate PASSED diagnostics ==="
        echo "elapsed=${TCP_WAITED}s established=${ESTABLISHED:-?}"
        echo "mode=${_READY_STATUS_MODE} term_windows=${_TERM_WINDOWS:-?} require_x11=${MT5_REQUIRE_X11_WINDOW}"
        echo "ipc_pipes=present ipc_pipes_hits=${IPC_PIPES_HITS}/${IPC_PIPES_REQUIRED_HITS} last_evidence=${IPC_PIPES_LAST_EVIDENCE}"
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
          echo "DISPLAY=${DISPLAY:-unset}"
          echo "MT5_REQUIRE_X11_WINDOW=${MT5_REQUIRE_X11_WINDOW}"
          echo "ipc_pipes_required_hits=${IPC_PIPES_REQUIRED_HITS}"
          echo "xdotool_path=$(command -v xdotool 2>/dev/null || echo missing)"
          echo "wmctrl_path=$(command -v wmctrl 2>/dev/null || echo missing)"
          echo "ipc_pipes_last_present=${IPC_PIPES_LAST_PRESENT}"
          echo "ipc_pipes_last_evidence=${IPC_PIPES_LAST_EVIDENCE}"
          echo "=== visible windows snapshot ==="
          DISPLAY="${DISPLAY}" xdotool search --onlyvisible 2>/dev/null \
            | while IFS= read -r _wsid; do
                _wname=$(DISPLAY="${DISPLAY}" xdotool getwindowname "${_wsid}" 2>/dev/null || echo "?")
                echo "${_wsid}:${_wname}"
              done || true
          echo "=== terminal pid / process snapshot ==="
          echo "terminal_pid=${TERMINAL_RUNTIME_PID:-unknown}"
          echo "terminal_child_pid=${TERMINAL_CHILD_PID:-unknown}"
          ps -eo pid,ppid,comm,args 2>/dev/null | grep -E 'terminal64\.exe|wineserver|wine|Xvfb|fluxbox|python|mt5linux' || true
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
  if [[ -n "${TERMINAL_CHILD_PID:-}" ]] && kill -0 "${TERMINAL_CHILD_PID}" 2>/dev/null; then
    echo "[mt5-lifecycle] mode=wait_child child_pid=${TERMINAL_CHILD_PID} runtime_pid=${TERMINAL_RUNTIME_PID:-unknown}"
    wait "${TERMINAL_CHILD_PID}" || true
  elif [[ -n "${TERMINAL_RUNTIME_PID:-}" ]] && kill -0 "${TERMINAL_RUNTIME_PID}" 2>/dev/null; then
    echo "[mt5-lifecycle] mode=poll_runtime_pid runtime_pid=${TERMINAL_RUNTIME_PID} child_pid=${TERMINAL_CHILD_PID:-none}"
    while kill -0 "${TERMINAL_RUNTIME_PID}" 2>/dev/null; do sleep 5; done
    echo "[mt5-lifecycle] runtime_pid_exited pid=${TERMINAL_RUNTIME_PID}"
  else
    echo "[mt5-lifecycle] mode=no_active_terminal child_pid=${TERMINAL_CHILD_PID:-none} runtime_pid=${TERMINAL_RUNTIME_PID:-none}"
  fi
) 2>&1 | tee /tmp/mt5-launch-wrapper.log &

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
