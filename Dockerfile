FROM python:3.10-bullseye

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    automake \
    build-essential \
    cmake \
    autoconf \
    dbus \
    git \
    libtool \
    libdbus-1-dev \
    libglib2.0-dev \
    libudev-dev \
    libical-dev \
    libreadline-dev \
    pkg-config \
    python3-docutils \
    systemd \
    udev \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/build
COPY build/install-ell.sh ./
RUN sh ./install-ell.sh

WORKDIR /opt/build
COPY build/install-json-c.sh ./
RUN sh ./install-json-c.sh

WORKDIR /opt/build
COPY build/install-bluez.sh ./
RUN sh ./install-bluez.sh

WORKDIR /opt/pesetech2mqtt
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app ./app
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /data

HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
    CMD python3 -c "import os,time; s=os.stat('/tmp/gateway.healthy'); exit(0 if time.time()-s.st_mtime < 120 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
