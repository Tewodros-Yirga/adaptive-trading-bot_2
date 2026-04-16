FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN dpkg --add-architecture i386 && apt-get update && apt-get install -y \
    wget \
    curl \
    xvfb \
    supervisor \
    wine64 \
    wine32 \
    ca-certificates \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /bridge

COPY requirements.txt /bridge/requirements.txt
RUN pip3 install --no-cache-dir -r /bridge/requirements.txt

COPY app /bridge/app
COPY supervisord.conf /bridge/supervisord.conf
COPY start.sh /bridge/start.sh
RUN chmod +x /bridge/start.sh

EXPOSE 5555

CMD ["/bridge/start.sh"]
