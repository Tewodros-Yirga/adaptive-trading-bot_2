# ============================================================
# mt5-bridge-base — pre-baked Wine + Python + MT5 base image
# ============================================================
# Builds on top of scottyhardy/docker-wine (Wine already installed).
#
# Installers are pre-downloaded on the GitHub Actions runner and
# COPY'd in — NO apt-get update needed, avoids all network issues.
#
# Build args:
#   WINE_BASE  - scottyhardy base image tag
#   HAS_MT5    - "true" if mt5setup.exe was pre-downloaded
# ============================================================

ARG WINE_BASE=scottyhardy/docker-wine:devel-11.6-20260412
FROM ${WINE_BASE}

ARG HAS_MT5=false

ENV DISPLAY=:99
ENV WINEPREFIX=/opt/wineprefix
ENV WINEDEBUG=-all
ENV WINEDLLOVERRIDES="mscoree,mshtml="

# ---------------------------------------------------------------------------
# 1. Copy pre-downloaded installers from the build context
#    (downloaded on the GHA runner — no apt/curl needed inside Docker)
# ---------------------------------------------------------------------------
COPY python-installer.exe /tmp/python-installer.exe
COPY mt5setup.exe         /tmp/mt5setup.exe

# ---------------------------------------------------------------------------
# 2. Create Wine prefix
# ---------------------------------------------------------------------------
RUN set -eux; \
    mkdir -p "${WINEPREFIX}"; \
    Xvfb :99 -screen 0 1280x720x24 & \
    sleep 3; \
    wineboot --init 2>/dev/null || true; \
    timeout 60 wine cmd /c ver || true; \
    sleep 3; \
    pkill Xvfb || true

# ---------------------------------------------------------------------------
# 3. Install Windows Python 3.9 inside the Wine prefix
# ---------------------------------------------------------------------------
RUN set -eux; \
    Xvfb :99 -screen 0 1280x720x24 & \
    sleep 3; \
    timeout 600 wine /tmp/python-installer.exe \
      /quiet InstallAllUsers=1 PrependPath=1 Shortcuts=0 Include_test=0 \
      || true; \
    sleep 5; \
    pkill Xvfb || true; \
    FOUND_PYTHON=$(find "${WINEPREFIX}/drive_c" -maxdepth 5 -name "python.exe" \
      2>/dev/null | head -1) || true; \
    echo "Found Python at: ${FOUND_PYTHON:-NOT FOUND}"; \
    test -n "${FOUND_PYTHON}" || (echo "ERROR: python.exe not found" && exit 1); \
    echo "${FOUND_PYTHON}" > /opt/wine_python_exe.path

# ---------------------------------------------------------------------------
# 4. pip install MetaTrader5 + mt5linux inside Wine Python
# ---------------------------------------------------------------------------
RUN set -eux; \
    WINE_PY=$(cat /opt/wine_python_exe.path); \
    Xvfb :99 -screen 0 1280x720x24 & \
    sleep 3; \
    timeout 300 wine "${WINE_PY}" -m pip install --upgrade --no-cache-dir pip || true; \
    timeout 600 wine "${WINE_PY}" -m pip install --no-cache-dir MetaTrader5 || true; \
    timeout 600 wine "${WINE_PY}" -m pip install --no-cache-dir "mt5linux>=0.1.9" || true; \
    timeout 300 wine "${WINE_PY}" -m pip install --no-cache-dir python-dateutil || true; \
    wine "${WINE_PY}" -c "import mt5linux; print('mt5linux OK')" \
      || echo "WARNING: mt5linux check failed"; \
    pkill Xvfb || true

# ---------------------------------------------------------------------------
# 5. Install MetaTrader 5 terminal (only if HAS_MT5=true)
# ---------------------------------------------------------------------------
RUN set -eux; \
    if [ "${HAS_MT5}" != "true" ]; then \
      echo "HAS_MT5 not true — skipping MT5 terminal install."; \
      exit 0; \
    fi; \
    Xvfb :99 -screen 0 1280x720x24 & \
    sleep 3; \
    timeout 900 wine /tmp/mt5setup.exe /auto || true; \
    sleep 5; \
    pkill Xvfb || true; \
    FOUND_TERM=$(find "${WINEPREFIX}/drive_c" -maxdepth 6 \
      -name "terminal64.exe" 2>/dev/null | head -1) || true; \
    echo "terminal64.exe: ${FOUND_TERM:-NOT FOUND}"; \
    if [ -n "${FOUND_TERM}" ]; then \
      echo "${FOUND_TERM}" > /opt/mt5_terminal_exe.path; \
      touch /opt/mt5_terminal.preinstalled; \
    fi

# ---------------------------------------------------------------------------
# 6. Cleanup temp installers
# ---------------------------------------------------------------------------
RUN rm -f /tmp/python-installer.exe /tmp/mt5setup.exe

WORKDIR /bridge
EXPOSE 5555
CMD ["bash"]
