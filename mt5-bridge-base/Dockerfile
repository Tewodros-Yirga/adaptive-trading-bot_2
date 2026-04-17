# ============================================================
# mt5-bridge-base — pre-baked Wine + Python + MT5 base image
# ============================================================
# Push this to GitHub Container Registry (ghcr.io) ONCE.
# The bridge service Dockerfile will FROM this image so that
# Render deploys never need to install Wine, Python, or MT5 at runtime.
#
# Build & push (run once from your dev machine or CI):
#   IMAGE=ghcr.io/<your-github-username>/mt5-bridge-base:latest
#   docker build --pull -t $IMAGE ./mt5-bridge-base
#   docker push $IMAGE
#
# Build args (override via --build-arg):
#   PYTHON_URL   - Windows Python 3.9 installer URL
#   MT5_URL      - MT5 setup.exe URL (leave empty to skip MT5 terminal install)
#
# The resulting image contains:
#   - Wine64 + Xvfb
#   - Windows Python 3.9 at C:\Program Files\Python39\python.exe
#   - MetaTrader5 + mt5linux + python-dateutil pip packages
#   - MetaTrader 5 terminal64.exe (if MT5_URL is provided at build time)
#   - Linux Python 3 venv at /opt/venv (for the FastAPI bridge)
# ============================================================

FROM ubuntu:22.04

ARG PYTHON_URL=https://www.python.org/ftp/python/3.9.13/python-3.9.13-amd64.exe
ARG MT5_URL=

ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:99
ENV WINEPREFIX=/opt/wineprefix
ENV WINEDEBUG=-all
ENV HOME=/root
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# ---------------------------------------------------------------------------
# 1. System packages: Wine, Xvfb, Python3, curl
# ---------------------------------------------------------------------------
RUN set -eux; \
    dpkg --add-architecture i386; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      wget \
      winbind \
      wine \
      wine64 \
      wine32 \
      xvfb \
      supervisor \
      python3 \
      python3-pip \
      python3-venv; \
    rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# 2. Create Wine prefix (with a running Xvfb)
# ---------------------------------------------------------------------------
RUN set -eux; \
    mkdir -p "${WINEPREFIX}"; \
    Xvfb :99 -screen 0 1280x720x24 & \
    sleep 2; \
    wineboot --init 2>/dev/null || true; \
    # Run a trivial wine command to fully initialise the prefix. \
    timeout 60 wine cmd /c ver || true; \
    sleep 3; \
    pkill Xvfb || true

# ---------------------------------------------------------------------------
# 3. Install Windows Python 3.9 inside the Wine prefix
# ---------------------------------------------------------------------------
RUN set -eux; \
    curl -fsSL --retry 3 --max-time 300 "${PYTHON_URL}" \
      -o /tmp/python-installer.exe; \
    Xvfb :99 -screen 0 1280x720x24 & \
    sleep 2; \
    timeout 600 wine /tmp/python-installer.exe \
      /quiet InstallAllUsers=1 PrependPath=1 Shortcuts=0 Include_test=0 \
      || true; \
    sleep 5; \
    pkill Xvfb || true; \
    rm -f /tmp/python-installer.exe; \
    # Verify Python is usable. \
    FOUND_PYTHON=$(find "${WINEPREFIX}/drive_c" -maxdepth 5 -name "python.exe" \
      2>/dev/null | head -1) || true; \
    echo "Found Python at: ${FOUND_PYTHON}"; \
    test -n "${FOUND_PYTHON}" || (echo "ERROR: python.exe not found after install" && exit 1)

# ---------------------------------------------------------------------------
# 4. pip install MetaTrader5, mt5linux inside Wine Python
# ---------------------------------------------------------------------------
RUN set -eux; \
    FOUND_PYTHON=$(find "${WINEPREFIX}/drive_c" -maxdepth 5 -name "python.exe" \
      2>/dev/null | head -1); \
    WINE_PY=$(winepath -w "${FOUND_PYTHON}" 2>/dev/null || echo "${FOUND_PYTHON}"); \
    Xvfb :99 -screen 0 1280x720x24 & \
    sleep 2; \
    timeout 300 wine "${WINE_PY}" -m pip install --upgrade --no-cache-dir pip || true; \
    timeout 600 wine "${WINE_PY}" -m pip install --no-cache-dir MetaTrader5 || true; \
    timeout 600 wine "${WINE_PY}" -m pip install --no-cache-dir "mt5linux>=0.1.9" || true; \
    timeout 300 wine "${WINE_PY}" -m pip install --no-cache-dir python-dateutil || true; \
    # Sanity check. \
    wine "${WINE_PY}" -c "import encodings; import mt5linux; print('mt5linux_ok')" || \
      (echo "ERROR: mt5linux not importable after install" && exit 1); \
    pkill Xvfb || true; \
    # Write the discovered python path for runtime scripts. \
    echo "${FOUND_PYTHON}" > /opt/wine_python_exe.path

# ---------------------------------------------------------------------------
# 5. Install MetaTrader 5 terminal (only if MT5_URL build arg is provided)
# ---------------------------------------------------------------------------
RUN set -eux; \
    if [ -z "${MT5_URL}" ]; then \
      echo "MT5_URL not set — skipping MT5 terminal install (will be installed at runtime if MT5_INSTALLER_URL env is set)."; \
      exit 0; \
    fi; \
    curl -fsSL --retry 3 --max-time 600 "${MT5_URL}" -o /tmp/mt5setup.exe; \
    Xvfb :99 -screen 0 1280x720x24 & \
    sleep 2; \
    # /auto is MetaTrader's own silent install flag (not NSIS /silent). \
    timeout 900 wine /tmp/mt5setup.exe /auto || true; \
    sleep 5; \
    pkill Xvfb || true; \
    rm -f /tmp/mt5setup.exe; \
    FOUND_TERMINAL=$(find "${WINEPREFIX}/drive_c" -maxdepth 6 -name "terminal64.exe" \
      2>/dev/null | head -1) || true; \
    echo "Found terminal64.exe at: ${FOUND_TERMINAL:-NOT FOUND}"; \
    # Write path for runtime scripts. \
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
