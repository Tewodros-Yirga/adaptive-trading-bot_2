# ============================================================
# mt5-bridge-service
# ============================================================
# FROM the pre-built base image that already contains:
#   - Wine64 + Xvfb
#   - Windows Python 3.9 + MetaTrader5 + mt5linux (Wine-side)
#   - MetaTrader 5 terminal64.exe (if baked in with MT5_URL build arg)
#   - /opt/venv  Linux Python venv
#
# To use the pre-built base image (recommended, fast deploys):
#   Set BASE_IMAGE build arg or change the FROM line below to:
#   FROM ghcr.io/<your-github-username>/mt5-bridge-base:latest
#
# To build without the pre-built base (slower, installs at runtime):
#   FROM scottyhardy/docker-wine:devel-11.6-20260412
# ============================================================

ARG BASE_IMAGE=ghcr.io/loriloha/mt5-bridge-base:latest
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Install any system packages not in the base image.
# curl/wget/ca-certs are already there; only add bridge-specific extras.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends supervisor; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /bridge

# Install Linux-side Python packages (FastAPI bridge).
COPY requirements.txt /bridge/requirements.txt
RUN "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir -r /bridge/requirements.txt

COPY app /bridge/app
COPY supervisord.conf /bridge/supervisord.conf
COPY bootstrap-mt5.sh /bridge/bootstrap-mt5.sh
COPY start.sh /bridge/start.sh
RUN chmod +x /bridge/start.sh /bridge/bootstrap-mt5.sh

EXPOSE 5555

USER root

CMD ["/bridge/start.sh"]
