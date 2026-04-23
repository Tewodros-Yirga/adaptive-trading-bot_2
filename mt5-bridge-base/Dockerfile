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
ENV MESA_GL_VERSION_OVERRIDE=4.5
ENV MESA_GLSL_VERSION_OVERRIDE=450

# ---------------------------------------------------------------------------
# 1. Copy pre-downloaded installers from the build context
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
    if [ "${HAS_MT5}" = "true" ]; then \
      timeout 900 wine /tmp/mt5setup.exe /auto || true; \
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
    # -- Cleanup -- \
    kill "${XVFB_PID}" 2>/dev/null || pkill Xvfb || true; \
    rm -f /tmp/python-installer.exe /tmp/mt5setup.exe /tmp/.X99-lock

WORKDIR /bridge
EXPOSE 5555
CMD ["bash"]
