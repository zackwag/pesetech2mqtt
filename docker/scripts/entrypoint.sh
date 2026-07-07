#!/bin/bash
set -euo pipefail

export HOME="${GATEWAY_HOME:-/config}"
mkdir -p "$HOME" /config/mesh-storage /config/.cache/bluetooth-mesh

find_bluetooth_meshd() {
  for candidate in \
    /usr/libexec/bluetooth/bluetooth-meshd \
    /usr/lib/bluetooth/bluetooth-meshd
  do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done

  command -v bluetooth-meshd 2>/dev/null
}

if ! MESHD_BIN="$(find_bluetooth_meshd)"; then
  echo "bluetooth-meshd was not found; the BlueZ mesh build did not install the daemon." >&2
  exit 1
fi

if [ ! -e /sys/class/bluetooth/hci0 ]; then
  echo "Warning: /sys/class/bluetooth/hci0 was not found; bluetooth-meshd usually needs a Linux Bluetooth adapter exposed as hci0." >&2
fi

service dbus start
"$MESHD_BIN" --storage /config/mesh-storage &
MESHD_PID="$!"

MESHD_STARTUP_TIMEOUT="${MESHD_STARTUP_TIMEOUT:-5}"
for _ in $(seq 1 "$MESHD_STARTUP_TIMEOUT"); do
  sleep 1
  if ! kill -0 "$MESHD_PID" >/dev/null 2>&1; then
    echo "bluetooth-meshd exited during startup; check that the host bluetooth service is stopped and hci0 is available." >&2
    exit 1
  fi
done

GATEWAY_MODE="${GATEWAY_MODE:-shell}"
GATEWAY_ARGS="${GATEWAY_ARGS:---basedir /config --reload}"

case "$GATEWAY_MODE" in
  service)
    exec python3 gateway.py $GATEWAY_ARGS
    ;;
  shell)
    exec /bin/bash
    ;;
  *)
    echo "Invalid GATEWAY_MODE: $GATEWAY_MODE. Use shell or service." >&2
    exit 2
    ;;
esac
