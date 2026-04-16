FROM scottyhardy/docker-wine:devel-11.6-20260412

ENV DEBIAN_FRONTEND=noninteractive
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Base image already includes Wine (wine32/wine64), xvfb, and wineboot.
# We only install the runtime helpers we need for the bridge container.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      supervisor \
      python3 \
      python3-pip \
      python3-venv \
      ca-certificates \
      curl \
      wget; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /bridge

COPY requirements.txt /bridge/requirements.txt
RUN python3 -m venv "${VIRTUAL_ENV}" \
    && "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir --upgrade pip \
    && "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir -r /bridge/requirements.txt

COPY app /bridge/app
COPY supervisord.conf /bridge/supervisord.conf
COPY bootstrap-mt5.sh /bridge/bootstrap-mt5.sh
COPY start.sh /bridge/start.sh
RUN chmod +x /bridge/start.sh /bridge/bootstrap-mt5.sh

EXPOSE 5555

USER root

CMD ["/bridge/start.sh"]
