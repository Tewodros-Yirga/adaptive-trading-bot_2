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

FROM ghcr.io/loriloha/mt5-bridge-base:latest

ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:99
ENV WINEPREFIX=/opt/wineprefix
ENV WINEDEBUG=-all
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
ENV XDG_RUNTIME_DIR=/tmp/xdg-runtime

# Install supervisor + python3-venv (Linux-side, for the FastAPI bridge).
# apt-get works fine in Render's build environment.
RUN set -eux; \
    apt-get update -qq || true; \
    apt-get install -y --no-install-recommends --fix-missing supervisor python3-venv; \
    rm -rf /var/lib/apt/lists/*

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

EXPOSE 5555

USER root

CMD ["/bridge/start.sh"]
