FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN set -eux; \
    dpkg --add-architecture i386; \
    rm -rf /var/lib/apt/lists/*; \
    apt-get clean; \
    for i in 1 2 3 4 5; do \
      apt-get update && break || { echo "apt-get update failed (attempt $i), retrying..."; sleep $((i * 5)); rm -rf /var/lib/apt/lists/*; }; \
    done; \
    apt-get install -y --no-install-recommends \
      wget \
      curl \
      xvfb \
      supervisor \
      wine64 \
      wine32 \
      ca-certificates \
      python3 \
      python3-pip; \
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
