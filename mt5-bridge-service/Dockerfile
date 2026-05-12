# ============================================================
# mt5-bridge-service
# ============================================================
# Uses the pre-baked base image that already contains:
#   - Wine64 + Xvfb
#   - Windows Python 3.9 + MetaTrader5 + mt5linux (Wine-side)
#   - MetaTrader 5 terminal64.exe pre-installed
#   - /opt/wine_python_exe.path  (path to python.exe in Wine)
#   - /opt/mt5_terminal_exe.path (path to terminal64.exe)
#   - /opt/mt5_terminal.preinstalled (sentinel)
# ============================================================

# IMPORTANT: This tag MUST point to a base image whose terminal build
# matches a MetaTrader5 PyPI package. The 'latest' tag was rebuilt on
# 2026-05-11 with terminal build 5430 from the portable zip.
ARG BASE_IMAGE_TAG=latest
FROM ghcr.io/loriloha/mt5-bridge-base:${BASE_IMAGE_TAG}

# Cache-bust: forces HF to re-pull base image on rebuild (bump to trigger)
ARG CACHE_BUST=warmup-90s-v2
# Build and run as root. The wine prefix was also created by root (docker
# builds always run as root unless a USER instruction changes them — the
# scottyhardy base does NOT have a USER instruction so build is root).
# At runtime we stay root so Wine's ownership check passes.
USER root

ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:99
ENV WINEPREFIX=/opt/wineprefix
ENV WINEDEBUG=-all
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
ENV XDG_RUNTIME_DIR=/tmp/xdg-runtime
ENV HOME=/root

# Install supervisor + python3-venv (Linux-side, for the FastAPI bridge).
RUN set -eux; \
    apt-get update -qq || true; \
    apt-get install -y --no-install-recommends --fix-missing \
        supervisor python3-venv scrot xdotool x11-utils openbox curl \
        libimage-exiftool-perl iptables; \
    rm -rf /var/lib/apt/lists/*

# Ensure /opt/wineprefix is owned by root (matching runtime user).
RUN chown -R root:root /opt/wineprefix 2>/dev/null || true

# Create the Linux-side Python venv for the FastAPI bridge.
RUN python3 -m venv "${VIRTUAL_ENV}"

WORKDIR /bridge

# Install Linux-side Python packages (FastAPI bridge).
COPY requirements.txt /bridge/requirements.txt
RUN "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir --upgrade pip \
    && "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir -r /bridge/requirements.txt

COPY app /bridge/app
COPY supervisord.conf /bridge/supervisord.conf
COPY bootstrap-mt5.sh /bridge/bootstrap-mt5.sh
COPY start.sh /bridge/start.sh
RUN chmod +x /bridge/start.sh /bridge/bootstrap-mt5.sh

# ── Embed Exness servers.dat ─────────────────────────────────────────────────
# The generic MetaQuotes installer's servers.dat does NOT contain Exness
# server endpoints.  Without a valid servers.dat, the terminal cannot resolve
# "Exness-MT5Trial9" → IP:port and never establishes a broker TCP connection.
# Result: established=0, empty Market Watch, no IPC pipes, -10005 forever.
#
# The file is gzipped + base64-encoded (text) because HuggingFace rejects
# binary files in git pushes.  Decoded at build time.
COPY config/servers.dat.b64 /bridge/servers.dat.b64
RUN set -eux; \
    base64 -d /bridge/servers.dat.b64 | gunzip > /bridge/servers.dat; \
    MT5_DIR="/opt/wineprefix/drive_c/Program Files/MetaTrader 5"; \
    APPDATA_DIR="/opt/wineprefix/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal"; \
    mkdir -p "${MT5_DIR}/config" && \
    cp /bridge/servers.dat "${MT5_DIR}/config/servers.dat"; \
    mkdir -p "${APPDATA_DIR}/Common/config" && \
    cp /bridge/servers.dat "${APPDATA_DIR}/Common/config/servers.dat"; \
    find "${APPDATA_DIR}" -mindepth 2 -maxdepth 2 -type d -name "config" \
      2>/dev/null | while IFS= read -r d; do \
        cp /bridge/servers.dat "${d}/servers.dat" || true; \
    done; \
    echo "servers.dat installed ($(wc -c < /bridge/servers.dat) bytes)"

EXPOSE 5555

# Clear scottyhardy's gosu entrypoint so start.sh runs directly as root.
ENTRYPOINT []
CMD ["/bridge/start.sh"]
