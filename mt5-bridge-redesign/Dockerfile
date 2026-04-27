FROM ghcr.io/loriloha/mt5-bridge-base:latest

USER root

ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:99
ENV WINEPREFIX=/opt/wineprefix
ENV WINEDEBUG=-all
ENV HOME=/root
ENV LOGDIR=/var/log/mt5
ENV BRIDGE_PORT=18812
ENV NOVNC_PORT=6080
ENV VNC_PORT=5900

RUN set -eux; \
    apt-get update -qq; \
    apt-get install -y --no-install-recommends \
      supervisor xvfb openbox x11vnc novnc xdotool x11-utils \
      curl procps iproute2 net-tools ca-certificates; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt/mt5-redesign

COPY start.sh /opt/mt5-redesign/start.sh
COPY bootstrap.sh /opt/mt5-redesign/bootstrap.sh
COPY smoke-test.sh /opt/mt5-redesign/smoke-test.sh
COPY supervisord.conf /opt/mt5-redesign/supervisord.conf
COPY scripts /opt/mt5-redesign/scripts

RUN chmod +x \
    /opt/mt5-redesign/start.sh \
    /opt/mt5-redesign/bootstrap.sh \
    /opt/mt5-redesign/smoke-test.sh \
    /opt/mt5-redesign/scripts/*.sh

EXPOSE 18812 5900 6080

ENTRYPOINT []
CMD ["/opt/mt5-redesign/start.sh"]
