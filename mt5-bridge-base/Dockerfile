FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Base image used to avoid re-installing Wine/Xvfb/Supervisor system dependencies
# on every redeploy of the MT5 bridge service.
RUN set -eux; \
    dpkg --add-architecture i386; \
    rm -rf /var/lib/apt/lists/*; \
    apt-get clean; \
    for i in 1 2 3 4 5; do \
      apt-get update && break || { echo "apt-get update failed (attempt $i), retrying..."; sleep $((i * 5)); rm -rf /var/lib/apt/lists/*; }; \
    done; \
    apt-get install -y --no-install-recommends \
      xvfb \
      supervisor \
      wine \
      wine64 \
      wine32 \
      ca-certificates \
      python3 \
      python3-pip; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /bridge

EXPOSE 5555

CMD ["bash"]

