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
        openbox xdotool p7zip-full file winetricks \
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
    timeout 600 wine "${WINE_PY}" -m pip install --no-cache-dir MetaTrader5==5.0.5640 || true; \
    timeout 600 wine "${WINE_PY}" -m pip install --upgrade --no-cache-dir "mt5linux>=0.1.9" || true; \
    timeout 300 wine "${WINE_PY}" -m pip install --no-cache-dir python-dateutil || true; \
    wine "${WINE_PY}" -c "import MetaTrader5 as m; print('MetaTrader5', m.__version__)" \
      || echo "WARNING: MetaTrader5 verification failed"; \
    wine "${WINE_PY}" -c "import mt5linux; print('mt5linux OK')" \
      || echo "WARNING: mt5linux verification failed"; \
    \
    # -- Install real MSVC runtime DLLs via winetricks (vcrun2019). -- \
    # IMPORTANT: This must run AFTER Python is installed. \
    # vcrun2019 sets ucrtbase to native,builtin which breaks the Python installer \
    # (the installer exits silently in ~1s when ucrtbase is native-overridden). \
    # These DLLs are only needed at runtime when MetaTrader5.dll loads, not during \
    # Python installation or pip installs. \
    # wine-mono is already excluded via ENV WINEDLLOVERRIDES="mscoree,mshtml=" \
    # Reference: FXPIP ONE "How to run MT5 on Linux" (2025). \
    echo "Installing vcrun2019 (real MSVC runtime DLLs) via winetricks..."; \
    DISPLAY=:99 WINEDEBUG=-all winetricks -q --optout vcrun2019 2>/dev/null || true; \
    echo "winetricks vcrun2019 done (exit $?)"; \
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
        _MT5_TARGET="${_MT5_PARENT}/MetaTrader 5"; \
        _TMP_EXTRACT="/tmp/mt5-extract-$$"; \
        mkdir -p "${_MT5_PARENT}" "${_TMP_EXTRACT}"; \
        7z x /tmp/mt5setup.exe -o"${_TMP_EXTRACT}" -y > /tmp/7z-extract.log 2>&1 || true; \
        echo "=== 7z extraction log (last 10 lines) ==="; \
        tail -10 /tmp/7z-extract.log 2>/dev/null || true; \
        echo "=== Extracted contents ==="; \
        ls -la "${_TMP_EXTRACT}/" 2>/dev/null || true; \
        \
        # Check if this is a flat ZIP (terminal64.exe at root) or nested \
        # (inside a subdirectory like "MetaTrader 5/"). \
        if [ -f "${_TMP_EXTRACT}/terminal64.exe" ]; then \
          echo "Flat ZIP detected — moving files into '${_MT5_TARGET}'"; \
          mkdir -p "${_MT5_TARGET}"; \
          mv "${_TMP_EXTRACT}"/* "${_MT5_TARGET}/" 2>/dev/null || true; \
        else \
          # Nested: find the dir containing terminal64.exe and rename it \
          _NESTED_EXE=$(find "${_TMP_EXTRACT}" -maxdepth 5 \
            -name "terminal64.exe" 2>/dev/null | head -1) || true; \
          if [ -n "${_NESTED_EXE}" ]; then \
            _NESTED_DIR=$(dirname "${_NESTED_EXE}"); \
            echo "Nested ZIP detected — moving '${_NESTED_DIR}' to '${_MT5_TARGET}'"; \
            mv "${_NESTED_DIR}" "${_MT5_TARGET}" 2>/dev/null || true; \
          else \
            echo "WARNING: terminal64.exe not found in extracted archive"; \
          fi; \
        fi; \
        rm -rf "${_TMP_EXTRACT}"; \
        TERM_EXE=$(find "${_MT5_TARGET}" -maxdepth 1 \
          -name "terminal64.exe" 2>/dev/null | head -1) || true; \
        echo "terminal64.exe after normalization: ${TERM_EXE:-NOT FOUND}"; \
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
    TERM_DIR="$(dirname "${TERM_EXE}")"; \
    \
    rm -f /tmp/.X99-lock; \
    Xvfb :99 -screen 0 1280x720x24 & \
    XVFB_PID=$!; \
    sleep 3; \
    openbox --sm-disable > /tmp/openbox-init.log 2>&1 & \
    OPENBOX_PID=$!; \
    sleep 2; \
    \
    # Lock terminal exes READ-ONLY before first-run launch so that even if \
    # the terminal checks for a newer build, it cannot overwrite itself. \
    echo "Locking terminal exes (read-only) to prevent self-update during init..."; \
    find "${TERM_DIR}" \( -name '*.exe' -o -name '*.dll' \) \
      -exec chmod a-w {} \; 2>/dev/null || true; \
    echo "Exes locked."; \
    \
    # Block ONLY the terminal update-check domains so the update check fails \
    # instantly (ECONNREFUSED) instead of hanging for 10+ minutes downloading \
    # an update that can't be installed (exe is locked). \
    # Data CDNs (cdn.mql5.com, files.mql5.com etc.) are deliberately left \
    # unblocked so the terminal can download MQL5/ indicators/scripts on first run. \
    WINE_HOSTS="${WINEPREFIX}/drive_c/windows/system32/drivers/etc/hosts"; \
    mkdir -p "$(dirname "${WINE_HOSTS}")" 2>/dev/null || true; \
    for _d in update.mql5.com updates.mql5.com \
               update.metatrader5.com updates.metatrader5.com \
               mt5-update.metaquotes.net; do \
      echo "127.0.0.1 ${_d}" >> "${WINE_HOSTS}" 2>/dev/null || true; \
    done; \
    echo "Update-check domains blocked; data CDNs remain open."; \
    \
    # Pre-write portable config and terminal.ini so the first-run wizard \
    # is skipped and broker login is attempted automatically on startup. \
    mkdir -p "${TERM_DIR}/config" 2>/dev/null || true; \
    printf '[Common]\r\nLogin=435609450\r\nPassword=Mznxbcv12#\r\nServer=Exness-MT5Trial9\r\nNewsEnable=0\r\nAutoSync=0\r\nAutoUpdate=0\r\n' \
      > "${TERM_DIR}/config/common.ini" 2>/dev/null || true; \
    printf '[Startup]\r\nAutoStart=0\r\n\r\n[Common]\r\nLogin=435609450\r\nPassword=Mznxbcv12#\r\nServer=Exness-MT5Trial9\r\nNewsEnable=0\r\nAutoSync=0\r\nAutoUpdate=0\r\n\r\n[Experts]\r\nEnabled=1\r\nAllowLiveTrading=1\r\n\r\n[LiveUpdate]\r\nEnabled=0\r\nNextUpdate=9999999999\r\n' \
      > "${TERM_DIR}/terminal.ini" 2>/dev/null || true; \
    echo "Pre-wrote portable terminal.ini and config/common.ini"; \
    \
    # Remove liveme.exe — it blocks the main thread with a dialog on startup. \
    echo "Removing liveme.exe (blocks IPC if present)..."; \
    find "${TERM_DIR}" -maxdepth 1 -iname "liveme*.exe" -delete 2>/dev/null || true; \
    ls -la "${TERM_DIR}/liveme.exe" 2>/dev/null \
      && echo "WARNING: liveme.exe still present!" \
      || echo "liveme.exe removed (or was never present)"; \
    \
    echo "Launching terminal in portable mode (internet-accessible for data download)..."; \
    DISPLAY=:99 WINEDEBUG="err+all" WINEESYNC=0 WINEFSYNC=0 \
      wine "${TERM_EXE}" /portable > /tmp/mt5-firstrun.log 2>&1 & \
    TERM_PID=$!; \
    \
    # Background dismiss loop: actively dismiss wizard/LiveUpdate dialogs that
    # appear during the first run. Without this, any dialog blocks the wait loop
    # indefinitely because nothing is pressing buttons.
    ( \
      while true; do \
        for _name in "Select a company" "Welcome to" "LiveUpdate" "Setup" "Login"; do \
          _wids=$(DISPLAY=:99 xdotool search --onlyvisible --name "${_name}" 2>/dev/null || true); \
          for _wid in ${_wids}; do \
            eval "$(DISPLAY=:99 xdotool getwindowgeometry --shell "${_wid}" 2>/dev/null || true)"; \
            if [ -n "${WIDTH:-}" ] && [ -n "${HEIGHT:-}" ]; then \
              _cx=$(( X + WIDTH  / 2 )); \
              _by=$(( Y + HEIGHT * 75 / 100 )); \
              DISPLAY=:99 xdotool windowactivate --sync "${_wid}" 2>/dev/null || true; \
              DISPLAY=:99 xdotool mousemove "${_cx}" "${_by}" click 1 2>/dev/null || true; \
              DISPLAY=:99 xdotool key --window "${_wid}" Escape 2>/dev/null || true; \
              DISPLAY=:99 xdotool key --window "${_wid}" Return 2>/dev/null || true; \
            fi; \
          done; \
        done; \
        sleep 5; \
      done \
    ) & \
    DISMISS_PID=$!; \
    \
    # Measure pre-written terminal.ini size so we can detect when the terminal
    # overwrites it (healthy startup rewrites it from ~300B to >1KB).
    PRE_TINI_SIZE=$(wc -c < "${TERM_DIR}/terminal.ini" 2>/dev/null || echo 0); \
    \
    # Wait up to 120s for terminal to complete initialization.
    # Success conditions (any one is sufficient):
    #   1. terminal.ini grew beyond pre-written size (terminal rewrote it)
    #   2. Recursive file count > 50 (pre-packaged ZIP — MQL5/ data already present)
    #   3. MQL5/Experts directory exists anywhere under TERM_DIR
    echo "Waiting for portable init (up to 120 s)..."; \
    INIT_WAITED=0; \
    while [ "${INIT_WAITED}" -lt 120 ]; do \
      CUR_TINI_SIZE=$(wc -c < "${TERM_DIR}/terminal.ini" 2>/dev/null || echo 0); \
      PFILES=$(find "${TERM_DIR}" -type f 2>/dev/null | wc -l) || PFILES=0; \
      echo "Waiting for portable init (${INIT_WAITED}s, total_files=${PFILES}, tini=${CUR_TINI_SIZE}B)..."; \
      if [ "${CUR_TINI_SIZE}" -gt "${PRE_TINI_SIZE}" ]; then \
        echo "terminal.ini grown: ${PRE_TINI_SIZE}->${CUR_TINI_SIZE} bytes after ${INIT_WAITED}s"; \
        break; \
      fi; \
      if [ "${PFILES}" -gt 50 ]; then \
        echo "${PFILES} files present after ${INIT_WAITED}s — pre-packaged ZIP detected, init complete"; \
        break; \
      fi; \
      if find "${TERM_DIR}" -name "Experts" -type d 2>/dev/null | grep -q .; then \
        echo "MQL5/Experts dir found after ${INIT_WAITED}s — portable init done"; \
        break; \
      fi; \
      sleep 5; \
      INIT_WAITED=$(( INIT_WAITED + 5 )); \
    done; \
    kill "${DISMISS_PID}" 2>/dev/null || true; \
    \
    # Extra settle time for config dirs to flush. \
    sleep 10; \
    \
    # Ensure [Experts] section is in terminal.ini. \
    grep -q '\[Experts\]' "${TERM_DIR}/terminal.ini" 2>/dev/null \
      || printf '\r\n[Experts]\r\nEnabled=1\r\nAllowLiveTrading=1\r\n' \
           >> "${TERM_DIR}/terminal.ini" 2>/dev/null || true; \
    echo "Patched portable terminal.ini with [Experts]"; \
    \
    # Mirror config into AppData hash dirs for non-portable runtime. \
    find "${WINEPREFIX}/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal" \
      -mindepth 1 -maxdepth 1 -type d 2>/dev/null \
      | while IFS= read -r _hdir; do \
          _bn=$(basename "${_hdir}"); \
          case "${_bn}" in Common|Community|Help) continue ;; esac; \
          mkdir -p "${_hdir}/config" 2>/dev/null || true; \
          printf '[Common]\r\nLogin=435609450\r\nPassword=Mznxbcv12#\r\nServer=Exness-MT5Trial9\r\nNewsEnable=0\r\nAutoSync=0\r\n' \
            > "${_hdir}/config/common.ini" 2>/dev/null || true; \
          grep -q '\[Experts\]' "${_hdir}/terminal.ini" 2>/dev/null \
            || printf '\r\n[Experts]\r\nEnabled=1\r\nAllowLiveTrading=1\r\n' \
                 >> "${_hdir}/terminal.ini" 2>/dev/null || true; \
          # Also add LiveUpdate=0 to suppress update dialog in AppData hash dirs. \
          grep -q '\[LiveUpdate\]' "${_hdir}/terminal.ini" 2>/dev/null \
            || printf '\r\n[LiveUpdate]\r\nEnabled=0\r\nNextUpdate=9999999999\r\n' \
                 >> "${_hdir}/terminal.ini" 2>/dev/null || true; \
          echo "Patched AppData hash-dir: ${_hdir}"; \
        done; \
    \
    # Bake Allow-Algo-Trading + LiveUpdate=disabled into Wine registry. \
    wine reg add "HKCU\\Software\\MetaQuotes\\Terminal5" \
      /v "ExpertAdvisors" /t REG_DWORD /d 1 /f 2>/dev/null || true; \
    wine reg add "HKCU\\Software\\MetaQuotes\\Terminal5" \
      /v "AlgoTradingEnabled" /t REG_DWORD /d 1 /f 2>/dev/null || true; \
    wine reg add "HKCU\\Software\\MetaQuotes\\Terminal5" \
      /v "LiveUpdate" /t REG_DWORD /d 0 /f 2>/dev/null || true; \
    wine reg add "HKCU\\Software\\MetaQuotes\\Terminal5\\Settings" \
      /v "LiveUpdate" /t REG_DWORD /d 0 /f 2>/dev/null || true; \
    \
    echo "=== Install dir files ==="; \
    find "${TERM_DIR}" -maxdepth 1 -type f 2>/dev/null | head -20 || true; \
    echo "=== AppData dirs ==="; \
    find "${WINEPREFIX}/drive_c/users/root/AppData/Roaming/MetaQuotes" \
      -maxdepth 4 -type d 2>/dev/null | head -20 || true; \
    \
    kill "${TERM_PID}" 2>/dev/null || true; \
    sleep 5; \
    wineserver -k 2>/dev/null || true; \
    sleep 3; \
    \
    kill "${OPENBOX_PID}" 2>/dev/null || true; \
    kill "${XVFB_PID}" 2>/dev/null || pkill Xvfb || true; \
    rm -f /tmp/.X99-lock /tmp/mt5-firstrun.log /tmp/openbox-init.log; \
    \
    echo "Terminal first-run initialisation complete (portable mode)."

WORKDIR /bridge
EXPOSE 5555
CMD ["bash"]
