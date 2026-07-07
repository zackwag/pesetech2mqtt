FROM python:3.10-bullseye

ENV DEBIAN_FRONTEND=noninteractive

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

# install BlueZ with mesh support
WORKDIR /opt/build
COPY docker/scripts/install-ell.sh .
RUN sh ./install-ell.sh

WORKDIR /opt/build
COPY docker/scripts/install-json-c.sh .
RUN sh ./install-json-c.sh

WORKDIR /opt/build
COPY docker/scripts/install-bluez.sh .
RUN sh ./install-bluez.sh

# install bridge
WORKDIR /opt/hass-ble-mesh
COPY . .
RUN pip3 install -r requirements.txt

# mount config
WORKDIR /config
VOLUME /config

# run bluetooth service and bridge
WORKDIR /opt/hass-ble-mesh/gateway
COPY docker/scripts/entrypoint.sh .
ENTRYPOINT [ "/bin/bash", "entrypoint.sh" ]
