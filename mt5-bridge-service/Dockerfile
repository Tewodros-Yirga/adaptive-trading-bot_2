FROM scottyhardy/docker-wine:devel-11.6-20260412

ENV DEBIAN_FRONTEND=noninteractive

# Base image already includes Wine (wine32/wine64), xvfb, and wineboot.
# We only install the runtime helpers we need for the bridge container.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      supervisor \
      python3 \
      python3-pip \
      ca-certificates \
      curl \
      wget; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /bridge

COPY requirements.txt /bridge/requirements.txt
RUN pip3 install --no-cache-dir -r /bridge/requirements.txt

COPY app /bridge/app
COPY supervisord.conf /bridge/supervisord.conf
COPY start.sh /bridge/start.sh
RUN chmod +x /bridge/start.sh

EXPOSE 5555

CMD ["/bridge/start.sh"]
