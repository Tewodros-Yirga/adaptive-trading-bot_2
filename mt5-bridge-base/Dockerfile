# ============================================================
# mt5-bridge-base — pre-baked Wine + Python + MT5 base image
# ============================================================
# Builds on top of scottyhardy/docker-wine (which already has
# Wine, Xvfb, all Wine deps, and ca-certificates).
# We only add:
#   - Windows Python 3.9 (installed via wine)
#   - pip: MetaTrader5, mt5linux, python-dateutil (Wine-side)
#   - MT5 terminal (optional, via MT5_URL build arg)
#   - Linux Python3 venv at /opt/venv (for the FastAPI bridge)
#
# This avoids the Ubuntu apt flakiness / ca-certificates chicken-
# and-egg problem by starting with a fully configured Wine image.
#
# Build args:
#   WINE_BASE    - scottyhardy base image tag to build on
#   PYTHON_URL   - Windows Python 3.9 installer URL
#   MT5_URL      - MT5 setup.exe URL (leave empty to skip)
# ============================================================

ARG WINE_BASE=scottyhardy/docker-wine:devel-11.6-20260412
FROM ${WINE_BASE}

ARG PYTHON_URL=https://www.python.org/ftp/python/3.9.13/python-3.9.13-amd64.exe
ARG MT5_URL=

ENV DISPLAY=:99
ENV WINEPREFIX=/opt/wineprefix
ENV WINEDEBUG=-all
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
# Suppress Wine fixme/warnings
ENV WINEDLLOVERRIDES="mscoree,mshtml="

# ---------------------------------------------------------------------------
# 1. Linux-side: ensure python3-venv is available for the /opt/venv
#    (scottyhardy base already has curl, ca-certificates, python3, pip)
# ---------------------------------------------------------------------------
RUN set -eux; \
    # apt-get update may fail on noble-updates due to mirror sync races — that's OK. \
    # curl and python3-venv are in the base noble repo which downloads reliably. \
    apt-get update -qq || true; \
    apt-get install -y --no-install-recommends --fix-missing curl python3-venv; \
    rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# 2. Create Wine prefix
# ---------------------------------------------------------------------------
RUN set -eux; \
    mkdir -p "${WINEPREFIX}"; \
    Xvfb :99 -screen 0 1280x720x24 & \
    sleep 3; \
    wineboot --init 2>/dev/null || true; \
    # Run a trivial wine command to fully initialise the prefix. \
    timeout 60 wine cmd /c ver || true; \
    sleep 3; \
    pkill Xvfb || true

# ---------------------------------------------------------------------------
# 3. Install Windows Python 3.9 inside the Wine prefix
# ---------------------------------------------------------------------------
RUN set -eux; \
    curl -fsSL --retry 5 --retry-delay 5 --max-time 300 "${PYTHON_URL}" \
      -o /tmp/python-installer.exe; \
    Xvfb :99 -screen 0 1280x720x24 & \
    sleep 3; \
    timeout 600 wine /tmp/python-installer.exe \
      /quiet InstallAllUsers=1 PrependPath=1 Shortcuts=0 Include_test=0 \
      || true; \
    sleep 5; \
    pkill Xvfb || true; \
    rm -f /tmp/python-installer.exe; \
    FOUND_PYTHON=$(find "${WINEPREFIX}/drive_c" -maxdepth 5 -name "python.exe" \
      2>/dev/null | head -1) || true; \
    echo "Found Python at: ${FOUND_PYTHON:-NOT FOUND}"; \
    test -n "${FOUND_PYTHON}" || (echo "ERROR: python.exe not found after install" && exit 1); \
    echo "${FOUND_PYTHON}" > /opt/wine_python_exe.path

# ---------------------------------------------------------------------------
# 4. pip install MetaTrader5, mt5linux inside Wine Python
# ---------------------------------------------------------------------------
RUN set -eux; \
    WINE_PY=$(cat /opt/wine_python_exe.path); \
    Xvfb :99 -screen 0 1280x720x24 & \
    sleep 3; \
    timeout 300 wine "${WINE_PY}" -m pip install --upgrade --no-cache-dir pip || true; \
    timeout 600 wine "${WINE_PY}" -m pip install --no-cache-dir MetaTrader5 || true; \
    timeout 600 wine "${WINE_PY}" -m pip install --no-cache-dir "mt5linux>=0.1.9" || true; \
    timeout 300 wine "${WINE_PY}" -m pip install --no-cache-dir python-dateutil || true; \
    wine "${WINE_PY}" -c "import encodings; import mt5linux; print('mt5linux_ok')" \
      || echo "WARNING: mt5linux verification failed"; \
    pkill Xvfb || true

# ---------------------------------------------------------------------------
# 5. Install MetaTrader 5 terminal (only if MT5_URL build arg is provided)
# ---------------------------------------------------------------------------
RUN set -eux; \
    if [ -z "${MT5_URL}" ]; then \
      echo "MT5_URL not set — skipping MT5 terminal install."; \
      exit 0; \
    fi; \
    curl -fsSL --retry 5 --retry-delay 5 --max-time 600 "${MT5_URL}" \
      -o /tmp/mt5setup.exe; \
    Xvfb :99 -screen 0 1280x720x24 & \
    sleep 3; \
    timeout 900 wine /tmp/mt5setup.exe /auto || true; \
    sleep 5; \
    pkill Xvfb || true; \
    rm -f /tmp/mt5setup.exe; \
    FOUND_TERMINAL=$(find "${WINEPREFIX}/drive_c" -maxdepth 6 \
      -name "terminal64.exe" 2>/dev/null | head -1) || true; \
    echo "terminal64.exe: ${FOUND_TERMINAL:-NOT FOUND}"; \
    if [ -n "${FOUND_TERMINAL}" ]; then \
      echo "${FOUND_TERMINAL}" > /opt/mt5_terminal_exe.path; \
      touch /opt/mt5_terminal.preinstalled; \
    fi

# ---------------------------------------------------------------------------
# 6. Linux-side Python venv for the FastAPI bridge
# ---------------------------------------------------------------------------
RUN python3 -m venv "${VIRTUAL_ENV}" \
    && "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir --upgrade pip

WORKDIR /bridge
EXPOSE 5555
CMD ["bash"]
