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
        openbox xdotool \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# 2. Copy pre-downloaded installers from the build context
#    (downloaded on the GHA runner — no apt/curl needed inside Docker)
# ---------------------------------------------------------------------------
COPY python-installer.exe /tmp/python-installer.exe
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
    # Even with /auto, mt5setup.exe shows a dialog under Wine that requires a \
    # click to proceed. Run a background xdotool dismisser to auto-click any \
    # dialogs that appear during installation. \
    if [ "${HAS_MT5}" = "true" ]; then \
      # openbox is now installed via the apt layer above — start it so \
      # Wine dialogs register as visible to xdotool. \
      DISPLAY=:99 openbox --sm-disable > /tmp/openbox-install.log 2>&1 & \
      OPENBOX_INSTALL_PID=$!; \
      sleep 2; \
      ( \
        sleep 15; \
        for _try in $(seq 1 180); do \
          # Use plain search (not --onlyvisible) because without a WM all \
          # Wine windows appear invisible to xdotool but are still clickable. \
          _IDS=$(DISPLAY=:99 xdotool search --name "" 2>/dev/null \
            | awk 'NF' | sort -n -u) || true; \
          for _wid in ${_IDS}; do \
            DISPLAY=:99 xdotool windowfocus --sync "${_wid}" 2>/dev/null || true; \
            sleep 0.2; \
            for _xy in "400 300" "500 300" "640 350" \
                       "400 380" "500 400" "640 400" \
                       "360 450" "500 450" "640 450"; do \
              set -- ${_xy}; \
              DISPLAY=:99 xdotool mousemove --window "${_wid}" \
                --clearmodifiers "$1" "$2" click 1 2>/dev/null || true; \
            done; \
            DISPLAY=:99 xdotool key --window "${_wid}" --clearmodifiers Return 2>/dev/null || true; \
          done; \
          sleep 5; \
        done; \
      ) & \
      INSTALLER_DISMISS_PID=$!; \
      # Run installer WITHOUT /auto so the GUI wizard appears and the \
      # xdotool dismisser can click through it. /auto does not reliably \
      # suppress all dialogs, causing a 900s hang. \
      timeout 900 wine /tmp/mt5setup.exe || true; \
      kill "${INSTALLER_DISMISS_PID}" 2>/dev/null || true; \
      kill "${OPENBOX_INSTALL_PID:-}" 2>/dev/null || true; \
      sleep 5; \
      TERM_EXE=$(find "${WINEPREFIX}/drive_c" -maxdepth 6 \
        -name "terminal64.exe" 2>/dev/null | head -1) || true; \
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
#    the IPC pipe to appear, then snapshot the populated AppData into the
#    image.  Containers that inherit this image skip the wizard entirely
#    and start with the IPC pump ready in < 30 s.
#
#    The registry tweak at the end enables "Allow Algorithmic Trading" in
#    the baked profile so mt5.initialize() is accepted immediately.
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
    # Remove any leftover X99 lock from previous RUN layer. \
    rm -f /tmp/.X99-lock; \
    Xvfb :99 -screen 0 1280x720x24 & \
    XVFB_PID=$!; \
    sleep 3; \
    # Start a window manager so xdotool can deliver focus/input to Wine dialogs. \
    openbox --sm-disable > /tmp/openbox-init.log 2>&1 & \
    OPENBOX_PID=$!; \
    sleep 2; \
    \
    # Block LiveUpdate CDNs so the terminal never downloads an update. \
    for _d in live.mql5.com updates.mql5.com update.mql5.com download.mql5.com \
               cdn.mql5.com ec.mql5.com files.mql5.com www.mql5.com mql5.com \
               update.metatrader5.com updates.metatrader5.com \
               mt5-update.metaquotes.net metaquotes.net; do \
      grep -qF "${_d}" /etc/hosts 2>/dev/null || \
        echo "0.0.0.0 ${_d}" >> /etc/hosts 2>/dev/null || true; \
    done; \
    \
    # Pre-write MetaQuotes-Demo server config to both candidate config paths. \
    # This tells the terminal which server to pre-select, converting the \
    # "Select a company" wizard into a simpler Login form. \
    for _cfg_dir in \
      "${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/config" \
      "${WINEPREFIX}/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal/Common/config"; do \
      mkdir -p "${_cfg_dir}" 2>/dev/null || true; \
      printf '[Common]\r\nLogin=\r\nServer=MetaQuotes-Demo\r\nNewsEnable=0\r\nAutoSync=0\r\n' \
        > "${_cfg_dir}/common.ini" 2>/dev/null || true; \
      echo "Pre-wrote server config: ${_cfg_dir}/common.ini"; \
    done; \
    \
    # Launch terminal — allow it to populate AppData and create IPC pipes. \
    echo "Launching terminal for first-run AppData initialisation..."; \
    WINEDEBUG="err+all" WINEESYNC=0 WINEFSYNC=0 \
      wine "${TERM_EXE}" > /tmp/mt5-firstrun.log 2>&1 & \
    TERM_PID=$!; \
    \
    # Dismiss LiveUpdate and first-run wizards for up to 3 minutes. \
    # Critical: click broker list rows BEFORE clicking Next because Next is \
    # disabled (greyed out) until a company is selected from the list. \
    ( \
      sleep 10; \
      for _try in $(seq 1 36); do \
        IDS=$( { \
          DISPLAY=:99 xdotool search --onlyvisible --name "LiveUpdate" 2>/dev/null; \
          DISPLAY=:99 xdotool search --onlyvisible --name "Select a company" 2>/dev/null; \
          DISPLAY=:99 xdotool search --onlyvisible --name "Welcome to" 2>/dev/null; \
          DISPLAY=:99 xdotool search --onlyvisible --name "MetaTrader 5" 2>/dev/null; \
          DISPLAY=:99 xdotool search --onlyvisible --name "MetaTrader" 2>/dev/null; \
        } | awk 'NF' | sort -n -u ); \
        ALL_IDS=$( DISPLAY=:99 xdotool search --onlyvisible 2>/dev/null | awk 'NF' | sort -n -u ); \
        WORK_IDS="$(printf '%s\n%s\n' "${IDS}" "${ALL_IDS}" | awk 'NF' | sort -n -u)"; \
        WIN_TITLES="$(DISPLAY=:99 xdotool search --onlyvisible 2>/dev/null \
          | while IFS= read -r _w; do DISPLAY=:99 xdotool getwindowname "$_w" 2>/dev/null || true; done \
          | paste -sd'|' - 2>/dev/null || true)"; \
        echo "[firstrun-dismiss try=${_try}] windows=${WIN_TITLES}"; \
        for _wid in ${WORK_IDS}; do \
          DISPLAY=:99 xdotool windowactivate --sync "${_wid}" 2>/dev/null || true; \
          sleep 0.25; \
          # Click broker list items first (enables the Next button). \
          for _xy in "400 210" "500 210" "640 210" \
                     "400 245" "500 245" "640 245" \
                     "400 275" "500 275" "640 275"; do \
            set -- ${_xy}; \
            DISPLAY=:99 xdotool mousemove --clearmodifiers "$1" "$2" click 1 2>/dev/null || true; \
            sleep 0.06; \
          done; \
          # Double-click the first broker list item to select it. \
          DISPLAY=:99 xdotool mousemove --clearmodifiers "400" "245" click 1 2>/dev/null || true; \
          sleep 0.05; \
          DISPLAY=:99 xdotool mousemove --clearmodifiers "400" "245" click 1 2>/dev/null || true; \
          sleep 0.1; \
          # Click Next/OK/Restart/Later button rows. \
          for _xy in "638 418" "724 488" "640 520" "560 332" "469 335"; do \
            set -- ${_xy}; \
            DISPLAY=:99 xdotool mousemove --clearmodifiers "$1" "$2" click 1 2>/dev/null || true; \
            sleep 0.06; \
          done; \
          DISPLAY=:99 xdotool key --window "${_wid}" --clearmodifiers Return 2>/dev/null || true; \
          sleep 0.1; \
          DISPLAY=:99 xdotool key --window "${_wid}" --clearmodifiers Escape 2>/dev/null || true; \
          sleep 0.1; \
          DISPLAY=:99 xdotool key --window "${_wid}" --clearmodifiers Return 2>/dev/null || true; \
        done; \
        sleep 5; \
      done; \
    ) & \
    DISMISS_PID=$!; \
    \
    # Wait for the IPC named pipe to appear (up to 3 minutes). \
    echo "Waiting for IPC named pipes to appear..."; \
    PIPE_WAITED=0; \
    IPC_PIPE_FOUND=false; \
    while [ "${PIPE_WAITED}" -lt 180 ]; do \
      PIPE_COUNT=$(timeout 8 wine cmd /c "dir \\\\.\\pipe\\" 2>/dev/null \
        | tr -d '\r' \
        | grep -vE '(Directory of|Volume|File Not Found|^$)' \
        | wc -l) || PIPE_COUNT=0; \
      if [ "${PIPE_COUNT}" -gt 0 ]; then \
        echo "IPC pipes detected (count=${PIPE_COUNT}) after ${PIPE_WAITED}s — AppData is populated."; \
        IPC_PIPE_FOUND=true; \
        break; \
      fi; \
      echo "No IPC pipes yet (waited ${PIPE_WAITED}s)..."; \
      sleep 5; \
      PIPE_WAITED=$((PIPE_WAITED + 5)); \
    done; \
    \
    # Give the terminal 10 more seconds to write all its AppData files \
    # even if the IPC pipe appeared immediately. \
    sleep 10; \
    \
    # Bake "Allow Algorithmic Trading" via Wine registry. \
    # This key enables the Python/IPC API in the terminal's setting store. \
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
    # Show what was created so the build log is self-documenting. \
    echo "=== AppData directories created ==="; \
    find "${WINEPREFIX}/drive_c/users/root/AppData/Roaming/MetaQuotes" \
      -maxdepth 4 -type d 2>/dev/null | head -40 || true; \
    echo "=== IPC assessment complete ==="; \
    echo "IPC_PIPE_FOUND=${IPC_PIPE_FOUND}"; \
    \
    # Gracefully stop the terminal and dismiss loop; allow wineserver to drain. \
    kill "${DISMISS_PID}" 2>/dev/null || true; \
    kill "${TERM_PID}" 2>/dev/null || true; \
    sleep 5; \
    wineserver -k 2>/dev/null || true; \
    sleep 3; \
    \
    # Stop Xvfb and WM. \
    kill "${OPENBOX_PID}" 2>/dev/null || true; \
    kill "${XVFB_PID}" 2>/dev/null || pkill Xvfb || true; \
    rm -f /tmp/.X99-lock /tmp/mt5-firstrun.log /tmp/openbox-init.log; \
    \
    echo "Terminal first-run initialisation complete."

WORKDIR /bridge
EXPOSE 5555
CMD ["bash"]
