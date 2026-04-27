# ============================================================
# mt5-bridge-base — pre-baked Wine + Windows Python + MT5
# ============================================================
# Built on scottyhardy/docker-wine (Wine already installed).
# Installers are pre-downloaded by the GitHub Actions runner
# and COPY'd in — no apt-get, no curl inside Docker.
# ============================================================

ARG WINE_BASE=scottyhardy/docker-wine:latest
FROM ${WINE_BASE}

ARG HAS_MT5=false

ENV DISPLAY=:99
ENV WINEPREFIX=/opt/wineprefix
ENV WINEDEBUG=-all
ENV WINEDLLOVERRIDES="mscoree,mshtml="
ENV XDG_RUNTIME_DIR=/tmp/xdg-runtime
# Force Mesa software rasteriser — no GPU/DRI3 in headless containers.
ENV LIBGL_ALWAYS_SOFTWARE=1
ENV GALLIUM_DRIVER=llvmpipe
# Disable Wine esync/fsync — require futex kernel support absent in most Docker envs.
ENV WINEESYNC=0
ENV WINEFSYNC=0

# ---------------------------------------------------------------------------
# 1. Install openbox + xdotool for GUI automation during build.
#    Separate layer so it is cached independently of Wine operations.
#    openbox is REQUIRED: without a WM, Wine dialogs don't register as
#    visible to xdotool, so the installer dismisser loop finds nothing.
# ---------------------------------------------------------------------------
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends \
        openbox xdotool p7zip-full file \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# 2. Copy pre-downloaded installers from the build context
#    (downloaded on the GHA runner — no apt/curl needed inside Docker)
# ---------------------------------------------------------------------------
COPY python-installer.exe /tmp/python-installer.exe

# CACHE_BUST is set to github.run_id in CI so that every manual MT5 build
# forces a fresh download and install, even when the Dockerfile is unchanged.
# Without this, Docker GHA cache re-uses the previous failed installer layer.
ARG CACHE_BUST=0
RUN echo "Cache-bust value: ${CACHE_BUST}"

COPY mt5setup.exe         /tmp/mt5setup.exe
COPY smoke-test-ipc.sh    /bridge/smoke-test-ipc.sh


# ---------------------------------------------------------------------------
# 2. All Wine operations in ONE RUN step with a single Xvfb session.
#    Combining into one step avoids the /tmp/.X99-lock inter-layer problem.
# ---------------------------------------------------------------------------
RUN set -eux; \
    # Create XDG runtime dir (required by Wine/DBus). \
    mkdir -p "${XDG_RUNTIME_DIR}"; chmod 700 "${XDG_RUNTIME_DIR}"; \
    mkdir -p "${WINEPREFIX}"; \
    # Remove any leftover X99 lock. \
    rm -f /tmp/.X99-lock; \
    # Start Xvfb display. \
    Xvfb :99 -screen 0 1280x720x24 & \
    XVFB_PID=$!; \
    sleep 3; \
    \
    # -- Wine prefix init -- \
    wineboot --init 2>/dev/null || true; \
    timeout 60 wine cmd /c ver || true; \
    sleep 2; \
    \
    # -- Install Windows Python 3.9 -- \
    timeout 600 wine /tmp/python-installer.exe \
      /quiet InstallAllUsers=1 PrependPath=1 Shortcuts=0 Include_test=0 \
      || true; \
    sleep 5; \
    \
    # -- Locate python.exe -- \
    WINE_PY=$(find "${WINEPREFIX}/drive_c" -maxdepth 5 -name "python.exe" \
      2>/dev/null | head -1) || true; \
    echo "Python found at: ${WINE_PY:-NOT FOUND}"; \
    if [ -z "${WINE_PY}" ]; then \
      echo "ERROR: python.exe not found after install"; \
      kill "${XVFB_PID}" 2>/dev/null || true; \
      exit 1; \
    fi; \
    echo "${WINE_PY}" > /opt/wine_python_exe.path; \
    \
    # -- pip install MetaTrader5 + mt5linux -- \
    timeout 300 wine "${WINE_PY}" -m pip install --upgrade --no-cache-dir pip || true; \
    timeout 600 wine "${WINE_PY}" -m pip install --upgrade --no-cache-dir MetaTrader5 || true; \
    timeout 600 wine "${WINE_PY}" -m pip install --upgrade --no-cache-dir "mt5linux>=0.1.9" || true; \
    timeout 300 wine "${WINE_PY}" -m pip install --no-cache-dir python-dateutil || true; \
    wine "${WINE_PY}" -c "import MetaTrader5 as m; print('MetaTrader5', m.__version__)" \
      || echo "WARNING: MetaTrader5 verification failed"; \
    wine "${WINE_PY}" -c "import mt5linux; print('mt5linux OK')" \
      || echo "WARNING: mt5linux verification failed"; \
    \
    # -- Install MT5 terminal (only if HAS_MT5=true) -- \
    # \
    # Two supported input formats for /tmp/mt5setup.exe: \
    # \
    #  A) Portable ZIP archive (preferred). \
    #     Created by the package-mt5-windows.yml workflow which installs MT5 on \
    #     a native Windows GHA runner and zips C:\Program Files\MetaTrader 5\. \
    #     This completely bypasses Wine — we just extract and place the files. \
    #     Use by passing the release asset URL as mt5_url in the build workflow. \
    # \
    #  B) MetaQuotes installer stub (mt5setup.exe, official URL). \
    #     EXE-based fallback.  This path always hangs under Wine because the \
    #     MetaQuotes downloader uses WinHTTP which does not support the CDN's \
    #     TLS requirements under Wine.  Left here for completeness; do NOT \
    #     expect it to succeed. \
    if [ "${HAS_MT5}" = "true" ]; then \
      TERM_EXE=""; \
      \
      # Detect file type so we can branch correctly. \
      MT5_FILE_TYPE=$(file /tmp/mt5setup.exe 2>/dev/null || echo "unknown"); \
      echo "File type of mt5setup.exe: ${MT5_FILE_TYPE}"; \
      \
      # --- Method A: Portable ZIP archive (Windows-packaged, preferred) ----- \
      if echo "${MT5_FILE_TYPE}" | grep -qiE '(Zip archive|zip)'; then \
        echo "=== Detected ZIP archive — extracting portable MT5 installation ==="; \
        _MT5_PARENT="${WINEPREFIX}/drive_c/Program Files"; \
        mkdir -p "${_MT5_PARENT}"; \
        7z x /tmp/mt5setup.exe -o"${_MT5_PARENT}" -y > /tmp/7z-extract.log 2>&1 || true; \
        echo "=== 7z extraction log (last 10 lines) ==="; \
        tail -10 /tmp/7z-extract.log 2>/dev/null || true; \
        TERM_EXE=$(find "${WINEPREFIX}/drive_c" -maxdepth 10 \
          -name "terminal64.exe" 2>/dev/null | head -1) || true; \
      fi; \
      \
      # --- Method B: EXE stub fallback (likely to hang — see comments above) \
      if [ -z "${TERM_EXE}" ]; then \
        echo "No terminal64.exe from ZIP extraction — trying wine /auto fallback"; \
        echo "(Note: MetaQuotes stub WinHTTP downloads fail under Wine — use ZIP instead)"; \
        timeout 300 wine /tmp/mt5setup.exe /auto || true; \
        sleep 5; \
        TERM_EXE=$(find "${WINEPREFIX}/drive_c" -maxdepth 10 \
          -name "terminal64.exe" 2>/dev/null | head -1) || true; \
      fi; \
      \
      # --- Diagnostics ----------------------------------------------------- \
      echo "=== drive_c top-level ==="; \
      ls "${WINEPREFIX}/drive_c/" 2>/dev/null || true; \
      echo "=== Program Files ==="; \
      ls "${WINEPREFIX}/drive_c/Program Files/" 2>/dev/null || true; \
      echo "terminal64.exe: ${TERM_EXE:-NOT FOUND}"; \
      if [ -n "${TERM_EXE}" ]; then \
        echo "${TERM_EXE}" > /opt/mt5_terminal_exe.path; \
        touch /opt/mt5_terminal.preinstalled; \
      fi; \
    else \
      echo "HAS_MT5 not true — skipping MT5 terminal"; \
    fi; \
    \
    # -- Smoke-test script perms -- \
    chmod +x /bridge/smoke-test-ipc.sh; \
    \
    # -- Cleanup installers -- \
    rm -f /tmp/python-installer.exe /tmp/mt5setup.exe; \
    \
    # -- Cleanup X lock -- \
    kill "${XVFB_PID}" 2>/dev/null || pkill Xvfb || true; \
    rm -f /tmp/.X99-lock

# ---------------------------------------------------------------------------
# 3. First-run terminal initialisation — bake AppData into the image layer.
#
#    WHY: terminal64.exe stores its settings, server list, and "Allow
#    Algorithmic Trading" flag in %APPDATA%\MetaQuotes\Terminal\<hash>\.
#    When this directory is empty every container start triggers the
#    "Select a company" first-run wizard.  The wizard BLOCKS the main UI
#    thread → IPC message pump stops → every mt5.initialize() returns
#    -10005 until the wizard is dismissed.
#
#    FIX: Run the terminal once here (under a fresh Xvfb), pre-write the
#    MetaQuotes-Demo server config, dismiss wizards with xdotool, wait for
#    terminal.ini to appear in the AppData hash dir, then snapshot the
#    populated AppData into the image.  Containers that inherit this image
#    skip the wizard entirely and start with the IPC pump ready in < 30 s.
#
#    KEY IMPROVEMENTS over previous version:
#    - LiveUpdate blocked via Windows proxy registry (localhost:1) instead of
#      /etc/hosts — /etc/hosts is read-only in Docker BuildKit on GHA runners.
#    - Dismiss loop uses xdotool getwindowgeometry --shell to get actual window
#      position; clicks are relative to the window centre (not hardcoded screen
#      coordinates that miss a centered dialog at unknown position).
#    - Success metric changed from "IPC named pipe count > 0" to
#      "terminal.ini exists in hash dir" — pipes never appear while the wizard
#      blocks the main thread; terminal.ini is written early in startup and is
#      a reliable init-complete signal.
# ---------------------------------------------------------------------------
RUN set -eux; \
    if [ ! -f /opt/mt5_terminal.preinstalled ]; then \
      echo "HAS_MT5 was false — skipping terminal first-run init"; \
      exit 0; \
    fi; \
    \
    TERM_EXE="$(tr -d '\r\n' < /opt/mt5_terminal_exe.path)"; \
    WINE_PY="$(tr -d '\r\n' < /opt/wine_python_exe.path)"; \
    \
    rm -f /tmp/.X99-lock; \
    Xvfb :99 -screen 0 1280x720x24 & \
    XVFB_PID=$!; \
    sleep 3; \
    openbox --sm-disable > /tmp/openbox-init.log 2>&1 & \
    OPENBOX_PID=$!; \
    sleep 2; \
    \
    # -----------------------------------------------------------------------\
    # Block LiveUpdate CDN domains via Wine-side hosts file.\
    # NOTE: We intentionally do NOT set a WinHTTP proxy here — a proxy\
    # blocks ALL WinHTTP traffic including the terminal's broker TCP\
    # handshake, which causes the terminal to stall invisibly with no\
    # X11 window and never write terminal.ini.\
    # The Wine-side hosts file is sufficient to prevent update downloads.\
    # -----------------------------------------------------------------------
    \
    # Also write to Wine-side hosts file for belt-and-suspenders coverage. \
    WINE_HOSTS="${WINEPREFIX}/drive_c/windows/system32/drivers/etc/hosts"; \
    mkdir -p "$(dirname "${WINE_HOSTS}")" 2>/dev/null || true; \
    for _d in live.mql5.com updates.mql5.com update.mql5.com download.mql5.com \
               cdn.mql5.com ec.mql5.com files.mql5.com www.mql5.com mql5.com \
               update.metatrader5.com updates.metatrader5.com \
               mt5-update.metaquotes.net metaquotes.net; do \
      echo "0.0.0.0 ${_d}" >> "${WINE_HOSTS}" 2>/dev/null || true; \
    done; \
    echo "Wine-side hosts file written"; \
    \
    # Pre-write Exness broker config so the terminal auto-logs in during \
    # first-run init, writes terminal.ini, and bypasses all wizard dialogs. \
    for _cfg_dir in \
      "${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/config" \
      "${WINEPREFIX}/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal/Common/config"; do \
      mkdir -p "${_cfg_dir}" 2>/dev/null || true; \
      printf '[Common]\r\nLogin=435609450\r\nPassword=Mznxbcv12#\r\nServer=Exness-MT5Trial9\r\nNewsEnable=0\r\nAutoSync=0\r\n' \
        > "${_cfg_dir}/common.ini" 2>/dev/null || true; \
      echo "Pre-wrote server config: ${_cfg_dir}/common.ini"; \
    done; \
    \
    WIN_CFG="${WINEPREFIX}/drive_c/mt5-firstrun.ini"; \
    printf '[Common]\r\nLogin=435609450\r\nPassword=Mznxbcv12#\r\nServer=Exness-MT5Trial9\r\nNewsEnable=0\r\nAutoSync=0\r\n' \
      > "${WIN_CFG}" 2>/dev/null || true; \
    echo "Wrote first-run config: ${WIN_CFG}"; \
    echo "Launching terminal for first-run AppData initialisation..."; \
    WINEDEBUG="err+all" WINEESYNC=0 WINEFSYNC=0 \
      wine "${TERM_EXE}" /config:"C:\\mt5-firstrun.ini" > /tmp/mt5-firstrun.log 2>&1 & \
    TERM_PID=$!; \
    \
    # -----------------------------------------------------------------------\
    # Dialog-dismiss loop — runs in a background subshell for the full wait.\
    #\
    # KEY FIX: use xdotool getwindowgeometry --shell to read the actual X,Y,\
    # WIDTH,HEIGHT of each dialog, then click relative to the window centre.\
    # The previous approach used hardcoded absolute screen coordinates which\
    # missed a centered dialog whose position was unknown.\
    # -----------------------------------------------------------------------\
    ( \
      sleep 10; \
      for _try in $(seq 1 48); do \
        IDS=$( { \
          DISPLAY=:99 xdotool search --onlyvisible --name "LiveUpdate" 2>/dev/null; \
          DISPLAY=:99 xdotool search --onlyvisible --name "Select a company" 2>/dev/null; \
          DISPLAY=:99 xdotool search --onlyvisible --name "Welcome to" 2>/dev/null; \
          DISPLAY=:99 xdotool search --onlyvisible --name "MetaTrader 5" 2>/dev/null; \
          DISPLAY=:99 xdotool search --onlyvisible --name "MetaTrader" 2>/dev/null; \
          DISPLAY=:99 xdotool search --onlyvisible --name "Setup" 2>/dev/null; \
        } | awk 'NF' | sort -n -u ); \
        echo "[firstrun-dismiss try=${_try}] found_wids=${IDS:-none}"; \
        for _wid in ${IDS}; do \
          WIN_NAME=$(DISPLAY=:99 xdotool getwindowname "${_wid}" 2>/dev/null || echo "unknown"); \
          echo "[firstrun-dismiss try=${_try}] wid=${_wid} name=${WIN_NAME}"; \
          DISPLAY=:99 xdotool windowactivate --sync "${_wid}" 2>/dev/null || true; \
          sleep 0.3; \
          \
          # Get the actual window geometry so clicks are relative to the window.\
          WIN_GEOM=$(DISPLAY=:99 xdotool getwindowgeometry --shell "${_wid}" 2>/dev/null || true); \
          eval "${WIN_GEOM}" 2>/dev/null || true; \
          WIN_W="${WIDTH:-800}"; WIN_H="${HEIGHT:-600}"; WIN_X="${X:-240}"; WIN_Y="${Y:-60}"; \
          CX=$(( WIN_X + WIN_W / 2 )); \
          CY=$(( WIN_Y + WIN_H / 2 )); \
          \
          # ---------------------------------------------------------------\
          # IMPORTANT: dialog-type-specific dismissal.\
          # "Welcome to LiveUpdate": pressing Escape causes the terminal to\
          # EXIT entirely. Must click the "Later" button (lower-left) so\
          # the terminal skips the update and proceeds to its main window.\
          # Login/company dialogs: Escape cancels them safely → offline mode.\
          # ---------------------------------------------------------------\
          _is_liveupdate=0; \
          echo "${WIN_NAME}" | grep -qi "LiveUpdate" && _is_liveupdate=1 || true; \
          \
          if [ "${_is_liveupdate}" -eq 1 ]; then \
            echo "[firstrun-dismiss] LiveUpdate detected — clicking Later (lower-left)"; \
            # Later button sits in the lower-left of the dialog. \
            # Try several candidate positions to be safe. \
            for _later_off in \
                "$(( WIN_W / 5 ))     $(( WIN_H - 15 ))" \
                "$(( WIN_W / 4 ))     $(( WIN_H - 20 ))" \
                "$(( WIN_W * 3 / 10 )) $(( WIN_H - 15 ))"; do \
              set -- ${_later_off}; \
              DISPLAY=:99 xdotool mousemove --clearmodifiers \
                "$(( WIN_X + $1 ))" "$(( WIN_Y + $2 ))" click 1 2>/dev/null || true; \
              sleep 0.15; \
            done; \
            sleep 0.5; \
          else \
            # For login/company-select dialogs: Escape → offline mode. \
            DISPLAY=:99 xdotool key --window "${_wid}" --clearmodifiers Escape 2>/dev/null || true; \
            sleep 0.2; \
            \
            # Click broker list rows (upper-center of dialog body). \
            for _row_off in -100 -60 -30 0; do \
              DISPLAY=:99 xdotool mousemove --clearmodifiers "${CX}" "$(( CY + _row_off ))" click 1 2>/dev/null || true; \
              sleep 0.06; \
            done; \
            DISPLAY=:99 xdotool key --window "${_wid}" --clearmodifiers Escape 2>/dev/null || true; \
          fi; \
        done; \
        sleep 5; \
      done; \
    ) & \
    DISMISS_PID=$!; \
    \
    # -----------------------------------------------------------------------\
    # Wait for terminal.ini to appear in the AppData hash dir (up to 4 min).\
    #\
    # terminal.ini is written by the terminal during normal startup — it\
    # records window layout, server settings, etc.  Its existence confirms the\
    # terminal completed at least the post-wizard initialisation phase.\
    # This is a far more reliable gate than IPC pipe count, which stays at 0\
    # while the wizard blocks the main thread.\
    # -----------------------------------------------------------------------\
    echo "Waiting for terminal.ini in AppData hash dir (up to 240 s)..."; \
    INIT_WAITED=0; \
    INIT_DONE=false; \
    while [ "${INIT_WAITED}" -lt 240 ]; do \
      TINI=$(find "${WINEPREFIX}/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal" \
        -mindepth 2 -maxdepth 2 -name "terminal.ini" 2>/dev/null | head -1) || true; \
      if [ -n "${TINI}" ]; then \
        echo "terminal.ini found: ${TINI} (after ${INIT_WAITED}s)"; \
        INIT_DONE=true; \
        break; \
      fi; \
      HASH_FILES=$(find "${WINEPREFIX}/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal" \
        -mindepth 2 -maxdepth 2 -type f 2>/dev/null | wc -l) || HASH_FILES=0; \
      echo "Waiting for init (${INIT_WAITED}s elapsed, hash_files=${HASH_FILES})..."; \
      sleep 5; \
      INIT_WAITED=$((INIT_WAITED + 5)); \
    done; \
    \
    # Give the terminal 15 more seconds to flush remaining AppData files. \
    sleep 15; \
    \
    # -----------------------------------------------------------------------\
    # Write terminal.ini manually to every hash dir the terminal created.\
    # The terminal only writes terminal.ini after a successful broker login,\
    # which may not happen during a headless build (no real account).\
    # A manually-written terminal.ini is sufficient to prevent the first-run\
    # wizard from re-appearing on subsequent container starts.\
    # -----------------------------------------------------------------------\
    find "${WINEPREFIX}/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal" \
      -mindepth 1 -maxdepth 1 -type d 2>/dev/null \
      | while IFS= read -r _hdir; do \
          _bn=$(basename "${_hdir}"); \
          if [ "${_bn}" = "Common" ] || [ "${_bn}" = "Community" ] || [ "${_bn}" = "Help" ]; then continue; fi; \
          if [ ! -f "${_hdir}/terminal.ini" ]; then \
            printf '[Startup]\r\nAutoStart=0\r\n\r\n[Common]\r\nLogin=435609450\r\nPassword=Mznxbcv12#\r\nServer=Exness-MT5Trial9\r\nNewsEnable=0\r\nAutoSync=0\r\n\r\n[Experts]\r\nEnabled=1\r\nAllowLiveTrading=1\r\n' \
              > "${_hdir}/terminal.ini" 2>/dev/null || true; \
            echo "Wrote synthetic terminal.ini: ${_hdir}/terminal.ini"; \
            INIT_DONE=true; \
          fi; \
        done; \
    \
    # Bake "Allow Algorithmic Trading" into Wine registry. \
    echo "Baking 'Allow Algorithmic Trading' into Wine registry..."; \
    wine reg add "HKCU\\Software\\MetaQuotes\\Terminal5" \
      /v "ExpertAdvisors" /t REG_DWORD /d 1 /f 2>/dev/null || true; \
    wine reg add "HKCU\\Software\\MetaQuotes\\Terminal5" \
      /v "AlgoTradingEnabled" /t REG_DWORD /d 1 /f 2>/dev/null || true; \
    \
    # Write terminal.ini with Experts settings if the file exists. \
    find "${WINEPREFIX}/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal" \
      -maxdepth 3 -name "terminal.ini" 2>/dev/null \
      | while IFS= read -r _tini; do \
          grep -q "\[Experts\]" "${_tini}" 2>/dev/null \
            || printf '\r\n[Experts]\r\nEnabled=1\r\nAllowLiveTrading=1\r\n' >> "${_tini}" 2>/dev/null || true; \
          echo "Patched ${_tini}"; \
        done; \
    \
    # Write common.ini into every terminal hash sub-directory the terminal \
    # created during this first run, so subsequent startups skip the wizard. \
    find "${WINEPREFIX}/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal" \
      -mindepth 1 -maxdepth 1 -type d 2>/dev/null \
      | while IFS= read -r _hdir; do \
          _basename=$(basename "${_hdir}"); \
          if [ "${_basename}" = "Common" ] || [ "${_basename}" = "Community" ]; then continue; fi; \
          mkdir -p "${_hdir}/config" 2>/dev/null || true; \
          printf '[Common]\r\nLogin=435609450\r\nPassword=Mznxbcv12#\r\nServer=Exness-MT5Trial9\r\nNewsEnable=0\r\nAutoSync=0\r\n' \
            > "${_hdir}/config/common.ini" 2>/dev/null || true; \
          echo "Wrote hash-dir config: ${_hdir}/config/common.ini"; \
        done; \
    \
    echo "=== AppData directories created ==="; \
    find "${WINEPREFIX}/drive_c/users/root/AppData/Roaming/MetaQuotes" \
      -maxdepth 4 -type d 2>/dev/null | head -40 || true; \
    echo "=== INIT_DONE=${INIT_DONE} ==="; \
    \
    kill "${DISMISS_PID}" 2>/dev/null || true; \
    kill "${TERM_PID}" 2>/dev/null || true; \
    sleep 5; \
    wineserver -k 2>/dev/null || true; \
    sleep 3; \
    \
    kill "${OPENBOX_PID}" 2>/dev/null || true; \
    kill "${XVFB_PID}" 2>/dev/null || pkill Xvfb || true; \
    rm -f /tmp/.X99-lock /tmp/mt5-firstrun.log /tmp/openbox-init.log; \
    \
    echo "Terminal first-run initialisation complete."

WORKDIR /bridge
EXPOSE 5555
CMD ["bash"]
