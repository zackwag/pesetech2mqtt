#!/bin/bash
set -euo pipefail

DATA_DIR="${DATA_DIR:-/data}"
MESH_JSON="${MESH_JSON:-/share/pesetech_mesh.json}"
CONFIG="$DATA_DIR/config.yaml"
STORE="$DATA_DIR/store.yaml"

mkdir -p "$DATA_DIR/mesh-storage" "$DATA_DIR/.cache/bluetooth-mesh"
export HOME="$DATA_DIR"

python3 -m app.import_mesh --ensure "$MESH_JSON" "$CONFIG" "$STORE"

if [[ ! -e /sys/class/bluetooth/hci0 ]]; then
  echo "Bluetooth adapter hci0 is not available." >&2
  exit 1
fi

MESHD_BIN=
for candidate in /usr/bin/bluetooth-meshd /usr/libexec/bluetooth/bluetooth-meshd /usr/lib/bluetooth/bluetooth-meshd; do
  if [[ -x "$candidate" ]]; then
    MESHD_BIN="$candidate"
    break
  fi
done
if [[ -z "$MESHD_BIN" ]]; then
  echo "bluetooth-meshd was not installed." >&2
  exit 1
fi

service dbus start

terminated=false
meshd_pid=
gateway_pid=

handle_signal() {
  terminated=true
  [[ -n "$gateway_pid" ]] && kill "$gateway_pid" 2>/dev/null || true
  [[ -n "$meshd_pid" ]] && kill "$meshd_pid" 2>/dev/null || true
}
trap handle_signal TERM INT

"$MESHD_BIN" --storage "$DATA_DIR/mesh-storage" &
meshd_pid=$!

sleep 2
if ! kill -0 "$meshd_pid" 2>/dev/null; then
  echo "bluetooth-meshd exited during startup." >&2
  wait "$meshd_pid" || true
  exit 1
fi

python3 -m app.gateway --data-dir "$DATA_DIR" &
gateway_pid=$!

set +e
wait -n "$meshd_pid" "$gateway_pid"
status=$?
set -e

kill "$gateway_pid" "$meshd_pid" 2>/dev/null || true
wait "$meshd_pid" 2>/dev/null || true
wait "$gateway_pid" 2>/dev/null || true

if [[ "$terminated" == true ]]; then
  exit 0
fi

echo "A required Pesetech process exited; Home Assistant Watchdog will restart the add-on." >&2
exit 1
