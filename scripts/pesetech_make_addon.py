#!/usr/bin/env python3
import argparse
import shutil
import tarfile
import textwrap
from pathlib import Path

from pesetech_make_bundle import bundle_files


DEFAULT_SLUG = "pesetech_ble_mesh"
DEFAULT_VERSION = "0.1.0"


def repo_root():
    return Path(__file__).resolve().parents[1]


def is_inside(path, parent):
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
    except ValueError:
        return False
    return True


def write_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


def copy_source(root, destination, output_root=None):
    root = Path(root).resolve()
    destination = Path(destination).resolve()
    output_root = Path(output_root).resolve() if output_root else None

    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    for relative_path in bundle_files(root):
        source = root / relative_path
        if output_root and is_inside(source, output_root):
            continue

        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def addon_image_name(image):
    if not image:
        return None
    name = str(image).strip()
    if "@" in name:
        return name
    slash = name.rfind("/")
    colon = name.rfind(":")
    if colon > slash:
        return name[:colon]
    return name


def addon_config(slug=DEFAULT_SLUG, version=DEFAULT_VERSION, image=None):
    image_name = addon_image_name(image)
    image_line = f'image: "{image_name}"' if image_name else ""
    return f"""
    name: "Pesetech BLE Mesh Gateway"
    description: "Control a Pesetech/Lepu artificial skylight through Bluetooth Mesh and MQTT discovery."
    version: "{version}"
    slug: "{slug}"
    {image_line}
    url: "https://community.home-assistant.io/t/pesetech-artificial-skylight-my-first-attempt-at-creating-an-integration/579060"
    arch:
      - aarch64
      - amd64
    startup: services
    boot: manual
    init: false
    stage: experimental
    homeassistant_api: true
    services:
      - mqtt:want
    ports:
      8766/tcp: 8766
    host_network: true
    udev: true
    full_access: true
    privileged:
      - NET_ADMIN
      - NET_RAW
      - SYS_ADMIN
      - SYS_RAWIO
    apparmor: false
    map:
      - type: share
        read_only: false
    options:
      operation: "runtime-check"
      mqtt_from_supervisor: true
      mqtt_broker: ""
      mqtt_port: 1883
      mqtt_username: ""
      mqtt_password: ""
      discovery_prefix: "homeassistant"
      node_id: "mqtt_mesh"
      device_id: "skylight"
      skylight_name: "Pesetech Skylight"
      skylight_uuid: ""
      mesh_json_path: "/share/pesetech_mesh.json"
      import_mesh_candidate: 0
      import_node_uuid: ""
      import_node_unicast: ""
      import_local_address: ""
      import_force: false
      cloud_region: "europe"
      cloud_base_url: ""
      cloud_token: ""
      cloud_username: ""
      cloud_password: ""
      cloud_token_path: "/share/pesetech_cloud_token.txt"
      cloud_username_path: "/share/pesetech_cloud_username.txt"
      cloud_password_path: "/share/pesetech_cloud_password.txt"
      cloud_output_path: "/share/pesetech_mesh.json"
      cloud_raw_output_path: ""
      cloud_report_path: "/share/pesetech_cloud_fetch_report.json"
      cloud_candidate: 0
      cloud_home_id: ""
      mesh_io: ""
      mesh_debug: false
      mesh_startup_timeout: 5
      mesh_adapter_power_off: false
      mesh_adapter_power_off_delay: 1
      mesh_scan_seconds: 10
      mesh_scan_repeat: 1
      ble_scan_seconds: 20
      ha_url: "http://supervisor/core"
      ha_entity_id: "light.skylight"
      relay: false
      dev_source_path: ""
      dev_source_archive_url: ""
      raw_command: "raw"
      raw_node: ""
      raw_opcode: ""
      raw_payload: ""
      raw_brightness: 32768
      raw_address: ""
      raw_address_model: "lightness"
      raw_retransmissions: 10
      raw_send_interval_ms: 75
      raw_timeout: 20
      raw_read_after: true
      skylight_programs_enabled: false
      skylight_programs_path: "/share/pesetech_skylight_programs.json"
      skylight_programs_dry_run: true
      diagnostic_monitor_enabled: false
      diagnostic_monitor_path: "/share/pesetech-command-monitor.jsonl"
      diagnostic_monitor_summary_interval_seconds: 60
      diagnostic_export_enabled: false
      diagnostic_export_port: 8766
      diagnostic_export_tail_bytes: 1048576
      btmon_monitor_enabled: false
      btmon_monitor_adapter: ""
      btmon_monitor_raw_path: "/share/pesetech-btmon.log"
      btmon_monitor_events_path: "/share/pesetech-btmon-events.jsonl"
      btmon_monitor_summary_path: "/share/pesetech-btmon-summary.jsonl"
      btmon_monitor_summary_interval_seconds: 60
      btmon_monitor_max_bytes: 26214400
      btmon_monitor_max_files: 3
      btmon_monitor_events_max_bytes: 5242880
      btmon_monitor_events_max_files: 3
      btmon_monitor_summary_max_bytes: 5242880
      btmon_monitor_summary_max_files: 3
    schema:
      operation: "list(service|runtime-check|mesh-daemon-check|ble-scan|status|preflight|scan|provision|configure|cloud-fetch|import-check|import|readiness-test|read-state|model-scope|raw-command|skylight-programs|move-test|ha-api-check|ha-service-test|proof-test|diagnostics|list)"
      mqtt_from_supervisor: "bool"
      mqtt_broker: "str"
      mqtt_port: "int(1,65535)"
      mqtt_username: "str?"
      mqtt_password: "password?"
      discovery_prefix: "str"
      node_id: "str"
      device_id: "str"
      skylight_name: "str"
      skylight_uuid: "str"
      mesh_json_path: "str"
      import_mesh_candidate: "int(0,100)"
      import_node_uuid: "str?"
      import_node_unicast: "str?"
      import_local_address: "str?"
      import_force: "bool"
      cloud_region: "list(asia|europe)"
      cloud_base_url: "str?"
      cloud_token: "password?"
      cloud_username: "str?"
      cloud_password: "password?"
      cloud_token_path: "str"
      cloud_username_path: "str"
      cloud_password_path: "str"
      cloud_output_path: "str"
      cloud_raw_output_path: "str?"
      cloud_report_path: "str"
      cloud_candidate: "int(0,100)"
      cloud_home_id: "str?"
      mesh_io: "str?"
      mesh_debug: "bool"
      mesh_startup_timeout: "int(1,60)"
      mesh_adapter_power_off: "bool"
      mesh_adapter_power_off_delay: "int(0,10)"
      mesh_scan_seconds: "int(5,300)"
      mesh_scan_repeat: "int(1,60)"
      ble_scan_seconds: "int(5,120)"
      ha_url: "str"
      ha_entity_id: "str"
      relay: "bool"
      dev_source_path: "str?"
      dev_source_archive_url: "str?"
      raw_command: "list(raw|pesetech-brightness)"
      raw_node: "str?"
      raw_opcode: "str?"
      raw_payload: "str?"
      raw_brightness: "int(0,65535)"
      raw_address: "str?"
      raw_address_model: "list(unicast|onoff|lightness|ctl|ctl-temperature)"
      raw_retransmissions: "int(1,20)"
      raw_send_interval_ms: "int(0,5000)"
      raw_timeout: "int(1,120)"
      raw_read_after: "bool"
      skylight_programs_enabled: "bool"
      skylight_programs_path: "str"
      skylight_programs_dry_run: "bool"
      diagnostic_monitor_enabled: "bool"
      diagnostic_monitor_path: "str"
      diagnostic_monitor_summary_interval_seconds: "int(5,3600)"
      diagnostic_export_enabled: "bool"
      diagnostic_export_port: "int(1,65535)"
      diagnostic_export_tail_bytes: "int(1024,104857600)"
      btmon_monitor_enabled: "bool"
      btmon_monitor_adapter: "str?"
      btmon_monitor_raw_path: "str"
      btmon_monitor_events_path: "str"
      btmon_monitor_summary_path: "str"
      btmon_monitor_summary_interval_seconds: "int(5,3600)"
      btmon_monitor_max_bytes: "int(1048576,1073741824)"
      btmon_monitor_max_files: "int(1,20)"
      btmon_monitor_events_max_bytes: "int(1048576,1073741824)"
      btmon_monitor_events_max_files: "int(1,20)"
      btmon_monitor_summary_max_bytes: "int(1048576,1073741824)"
      btmon_monitor_summary_max_files: "int(1,20)"
    """


def dockerfile(version=DEFAULT_VERSION):
    return f"""
    FROM python:3.10-bullseye

    ENV DEBIAN_FRONTEND=noninteractive

    RUN apt-get update && apt-get install -y --no-install-recommends \\
        automake \\
        build-essential \\
        cmake \\
        autoconf \\
        dbus \\
        git \\
        libtool \\
        libdbus-1-dev \\
        libglib2.0-dev \\
        libudev-dev \\
        libical-dev \\
        libreadline-dev \\
        pkg-config \\
        python3-docutils \\
        systemd \\
        udev \\
        wget \\
        && rm -rf /var/lib/apt/lists/*

    WORKDIR /opt/build
    COPY source/docker/scripts/install-ell.sh .
    RUN sh ./install-ell.sh

    WORKDIR /opt/build
    COPY source/docker/scripts/install-json-c.sh .
    RUN sh ./install-json-c.sh

    WORKDIR /opt/build
    COPY source/docker/scripts/install-bluez.sh .
    RUN sh ./install-bluez.sh

    WORKDIR /opt/hass-ble-mesh
    COPY source/ .
    RUN pip3 install -r requirements.txt

    ARG BUILD_VERSION={version}
    ARG BUILD_ARCH=amd64

    LABEL \\
        io.hass.version="${{BUILD_VERSION}}" \\
        io.hass.type="app" \\
        io.hass.arch="${{BUILD_ARCH}}"

    COPY run.sh /run.sh
    RUN chmod a+x /run.sh

    CMD [ "/run.sh" ]
    """


def run_script():
    return """
    #!/bin/bash
    set -euo pipefail

    DATA_DIR="${DATA_DIR:-/data}"
    OPTIONS_PATH="${OPTIONS_PATH:-$DATA_DIR/options.json}"
    OPERATION_OVERRIDE_PATH="${OPERATION_OVERRIDE_PATH:-/share/pesetech_next_operation.json}"
    CONFIG_PATH="$DATA_DIR/config.yaml"
    export HOME="$DATA_DIR"

    mkdir -p "$DATA_DIR" "$DATA_DIR/mesh-storage" "$DATA_DIR/.cache/bluetooth-mesh"

    APP_ROOT="/opt/hass-ble-mesh"
    read_dev_option() {
      python3 - "$OPTIONS_PATH" "$1" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
key = sys.argv[2]
if not path.exists():
    print("")
    raise SystemExit(0)

with path.open("r", encoding="utf-8") as handle:
    options = json.load(handle)

value = str(options.get(key) or "").strip()
if "\\n" in value:
    raise SystemExit(f"{key} must be blank or a single value")
print(value)
PY
    }

    DEV_SOURCE_PATH="$(read_dev_option dev_source_path)"
    DEV_SOURCE_ARCHIVE_URL="$(read_dev_option dev_source_archive_url)"
    if [ -n "$DEV_SOURCE_ARCHIVE_URL" ]; then
      if [ -z "$DEV_SOURCE_PATH" ]; then
        DEV_SOURCE_PATH="/share/pesetech-dev-source"
      fi
      DEV_SOURCE_TMP="$(mktemp -d /tmp/pesetech-dev-source.XXXXXX)"
      DEV_SOURCE_ARCHIVE="$DEV_SOURCE_TMP/source.tar.gz"
      echo "Refreshing Pesetech dev source overlay from $DEV_SOURCE_ARCHIVE_URL."
      wget -q -O "$DEV_SOURCE_ARCHIVE" "$DEV_SOURCE_ARCHIVE_URL"
      mkdir -p "$DEV_SOURCE_TMP/extract"
      tar -xzf "$DEV_SOURCE_ARCHIVE" -C "$DEV_SOURCE_TMP/extract"
      if [ ! -d "$DEV_SOURCE_TMP/extract/scripts" ] || [ ! -d "$DEV_SOURCE_TMP/extract/gateway" ]; then
        DEV_SOURCE_CHILD="$(find "$DEV_SOURCE_TMP/extract" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
        if [ -n "$DEV_SOURCE_CHILD" ] && [ -d "$DEV_SOURCE_CHILD/scripts" ] && [ -d "$DEV_SOURCE_CHILD/gateway" ]; then
          rm -rf "$DEV_SOURCE_TMP/normalized"
          mv "$DEV_SOURCE_CHILD" "$DEV_SOURCE_TMP/normalized"
          rm -rf "$DEV_SOURCE_TMP/extract"
          mv "$DEV_SOURCE_TMP/normalized" "$DEV_SOURCE_TMP/extract"
        fi
      fi
      if [ ! -d "$DEV_SOURCE_TMP/extract/scripts" ] || [ ! -d "$DEV_SOURCE_TMP/extract/gateway" ]; then
        echo "Downloaded dev source archive does not look like a Pesetech gateway checkout." >&2
        exit 2
      fi
      rm -rf "$DEV_SOURCE_PATH"
      mkdir -p "$(dirname "$DEV_SOURCE_PATH")"
      mv "$DEV_SOURCE_TMP/extract" "$DEV_SOURCE_PATH"
      rm -rf "$DEV_SOURCE_TMP"
    fi
    if [ -n "$DEV_SOURCE_PATH" ]; then
      if [ ! -d "$DEV_SOURCE_PATH/scripts" ] || [ ! -d "$DEV_SOURCE_PATH/gateway" ]; then
        echo "dev_source_path is set to $DEV_SOURCE_PATH, but it does not look like a Pesetech gateway checkout." >&2
        exit 2
      fi
      APP_ROOT="$DEV_SOURCE_PATH"
      export PYTHONPATH="$APP_ROOT${PYTHONPATH:+:$PYTHONPATH}"
      echo "Using Pesetech dev source overlay at $APP_ROOT."
    fi

    copy_report_to_share() {
      local source_path="$1"
      local share_name="$2"
      local share_path="/share/$share_name"
      if [ -f "$source_path" ] && [ -d /share ] && [ -w /share ]; then
        if cp "$source_path" "$share_path"; then
          echo "Copied key-free add-on report to $share_path."
        else
          echo "Warning: could not copy $source_path to $share_path." >&2
        fi
      fi
    }

    find_bluetooth_meshd() {
      for candidate in \\
        /usr/libexec/bluetooth/bluetooth-meshd \\
        /usr/lib/bluetooth/bluetooth-meshd
      do
        if [ -x "$candidate" ]; then
          echo "$candidate"
          return 0
        fi
      done

      command -v bluetooth-meshd 2>/dev/null
    }

    find_btmgmt() {
      for candidate in \\
        /usr/bin/btmgmt \\
        /opt/build/bluez-5.66/tools/btmgmt
      do
        if [ -x "$candidate" ]; then
          echo "$candidate"
          return 0
        fi
      done

      command -v btmgmt 2>/dev/null
    }

    mesh_io_indexes() {
      local io="$1"
      if [[ "$io" =~ hci([0-9]+) ]]; then
        echo "${BASH_REMATCH[1]}"
      elif [[ "$io" =~ :([0-9]+)$ ]]; then
        echo "${BASH_REMATCH[1]}"
      else
        for adapter in "${BLUETOOTH_ADAPTERS[@]}"; do
          basename "$adapter" | sed 's/^hci//'
        done
      fi
    }

    if ! CONFIG_EXPORTS="$(python3 $APP_ROOT/scripts/pesetech_addon_config.py --options "$OPTIONS_PATH" --override "$OPERATION_OVERRIDE_PATH" --output "$CONFIG_PATH" --shell)"; then
      echo "Failed to render add-on options; fix the app configuration and restart." >&2
      exit 2
    fi
    eval "$CONFIG_EXPORTS"
    echo "Pesetech operation gate: $PESETECH_OPERATION"

    if [ "$PESETECH_OPERATION" != "runtime-check" ] && [ "$PESETECH_OPERATION" != "mesh-daemon-check" ] && [ "$PESETECH_OPERATION" != "ble-scan" ] && [ "$PESETECH_OPERATION" != "status" ] && [ "$PESETECH_OPERATION" != "cloud-fetch" ]; then
      case "$PESETECH_MQTT_SOURCE" in
        supervisor)
          echo "MQTT config source: Home Assistant Supervisor MQTT service."
          echo "For workstation prove-ha-addon, use an externally reachable MQTT broker host and matching credentials; the Supervisor service host may be internal to Home Assistant."
          ;;
        supervisor_pending)
          echo "MQTT config source: Home Assistant Supervisor MQTT service when gateway config is next rendered."
          ;;
        persisted)
          echo "MQTT config source: existing persisted $CONFIG_PATH."
          ;;
        manual)
          echo "MQTT config source: manual add-on MQTT options."
          ;;
        unset)
          echo "MQTT config source: unset; configure manual MQTT fields or enable mqtt_from_supervisor." >&2
          ;;
      esac
    fi

    write_runtime_report() {
      local status="$1"
      local exit_code="$2"
      local message="$3"
      local report_path="$DATA_DIR/pesetech-runtime-check.json"
      python3 - "$report_path" "$status" "$exit_code" "$message" <<'PY'
import json
import sys
import time

report_path, status, exit_code, message = sys.argv[1:]
report = {
    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "operation": "runtime-check",
    "status": status,
    "exit_code": int(exit_code),
    "message": message,
    "sent_light_commands": False,
    "published_mqtt": False,
    "started_bluetooth_meshd": False,
    "provisioned": False,
    "imported": False,
}
with open(report_path, "w", encoding="utf-8") as output:
    json.dump(report, output, indent=2, sort_keys=True)
    output.write("\\n")
PY
    }

    if [ "$PESETECH_OPERATION" = "runtime-check" ]; then
      set +e
      python3 $APP_ROOT/scripts/pesetech_runtime_check.py
      RUNTIME_CHECK_EXIT="$?"
      set -e
      if [ "$RUNTIME_CHECK_EXIT" -eq 0 ]; then
        write_runtime_report "passed" "$RUNTIME_CHECK_EXIT" "Runtime check passed"
      else
        write_runtime_report "failed" "$RUNTIME_CHECK_EXIT" "Runtime check failed"
      fi
      copy_report_to_share "$DATA_DIR/pesetech-runtime-check.json" "pesetech-runtime-check.json"
      exit "$RUNTIME_CHECK_EXIT"
    fi

    if [ "$PESETECH_OPERATION" = "status" ]; then
      STATUS_REPORT="$DATA_DIR/pesetech-status.json"
      SHARE_STATUS_REPORT="/share/pesetech-status.json"
      set +e
      python3 $APP_ROOT/scripts/pesetech_addon_status.py \\
        --mesh-json "$PESETECH_MESH_JSON" \\
        --config "$CONFIG_PATH" \\
        --store "$DATA_DIR/store.yaml" \\
        --cloud-report "$PESETECH_CLOUD_REPORT" \\
        --import-check-report "$DATA_DIR/pesetech-import-check.json" \\
        --runtime-report "$DATA_DIR/pesetech-runtime-check.json" \\
        --mesh-daemon-report "$DATA_DIR/pesetech-mesh-daemon-check.json" \\
        --mesh-scan-report "$DATA_DIR/pesetech-mesh-scan.json" \\
        --ble-scan-report "$DATA_DIR/pesetech-ble-scan.json" \\
        --preflight-report "$DATA_DIR/pesetech-preflight.json" \\
        --readiness-report "$DATA_DIR/pesetech-readiness.json" \\
        --proof-log "$DATA_DIR/pesetech-move-test.jsonl" \\
        --ha-proof-log "$DATA_DIR/pesetech-ha-service-proof.jsonl" \\
        --final-audit-report "$DATA_DIR/pesetech-final-audit.json" \\
        --ha-url "$PESETECH_HA_URL" \\
        --ha-entity-id "$PESETECH_HA_ENTITY_ID" \\
        --mqtt-source "$PESETECH_MQTT_SOURCE" \\
        --mqtt-broker "$PESETECH_MQTT_BROKER" \\
        --mqtt-port "$PESETECH_MQTT_PORT" \\
        --discovery-prefix "$PESETECH_DISCOVERY_PREFIX" \\
        --mesh-topic "$PESETECH_NODE_ID" \\
        --device-id "$PESETECH_DEVICE_ID" \\
        --import-mesh-candidate "$PESETECH_IMPORT_MESH_CANDIDATE" \\
        --import-node-uuid "$PESETECH_IMPORT_NODE_UUID" \\
        --import-node-unicast "$PESETECH_IMPORT_NODE_UNICAST" \\
        --import-local-address "$PESETECH_IMPORT_LOCAL_ADDRESS" \\
        --output-json "$STATUS_REPORT"
      STATUS_EXIT=$?
      set -e
      if [ -f "$STATUS_REPORT" ] && [ -d /share ] && [ -w /share ]; then
        cp "$STATUS_REPORT" "$SHARE_STATUS_REPORT"
        echo "Copied key-free status report to $SHARE_STATUS_REPORT."
      fi
      exit "$STATUS_EXIT"
    fi

    if [ "$PESETECH_OPERATION" = "cloud-fetch" ]; then
      CLOUD_SECRET_FILES=()
      cleanup_cloud_secret_files() {
        if [ "${#CLOUD_SECRET_FILES[@]}" -gt 0 ]; then
          rm -f "${CLOUD_SECRET_FILES[@]}"
        fi
      }
      trap cleanup_cloud_secret_files EXIT

      write_inline_cloud_secret() {
        local prefix="$1"
        local value="$2"
        local target
        target="$(mktemp "/tmp/${prefix}.XXXXXX")"
        chmod 600 "$target"
        printf '%s' "$value" > "$target"
        CLOUD_SECRET_FILES+=("$target")
        echo "$target"
      }

      CLOUD_ARGS=(
        --output "$PESETECH_CLOUD_OUTPUT"
        --region "$PESETECH_CLOUD_REGION"
      )
      if [ -n "$PESETECH_CLOUD_BASE_URL" ]; then
        CLOUD_ARGS+=(--base-url "$PESETECH_CLOUD_BASE_URL")
      fi
      if [ "$PESETECH_CLOUD_CANDIDATE" != "0" ]; then
        CLOUD_ARGS+=(--candidate "$PESETECH_CLOUD_CANDIDATE")
      fi
      if [ -n "$PESETECH_CLOUD_HOME_ID" ]; then
        CLOUD_ARGS+=(--home-id "$PESETECH_CLOUD_HOME_ID")
      fi
      if [ -n "$PESETECH_CLOUD_RAW_OUTPUT" ]; then
        CLOUD_ARGS+=(--raw-output "$PESETECH_CLOUD_RAW_OUTPUT")
      fi
      if [ -n "$PESETECH_CLOUD_REPORT" ]; then
        CLOUD_ARGS+=(--report-output "$PESETECH_CLOUD_REPORT")
      fi
      if [ -n "$PESETECH_CLOUD_TOKEN" ]; then
        INLINE_CLOUD_TOKEN_FILE="$(write_inline_cloud_secret pesetech-cloud-token "$PESETECH_CLOUD_TOKEN")"
        CLOUD_ARGS+=(--token-file "$INLINE_CLOUD_TOKEN_FILE")
        echo "Using Pesetech cloud token from add-on configuration."
      elif [ -n "$PESETECH_CLOUD_USERNAME" ] || [ -n "$PESETECH_CLOUD_PASSWORD" ]; then
        if [ -z "$PESETECH_CLOUD_USERNAME" ] || [ -z "$PESETECH_CLOUD_PASSWORD" ]; then
          echo "cloud_username and cloud_password must both be set when using add-on configuration credentials." >&2
          exit 2
        fi
        INLINE_CLOUD_USERNAME_FILE="$(write_inline_cloud_secret pesetech-cloud-username "$PESETECH_CLOUD_USERNAME")"
        INLINE_CLOUD_PASSWORD_FILE="$(write_inline_cloud_secret pesetech-cloud-password "$PESETECH_CLOUD_PASSWORD")"
        CLOUD_ARGS+=(--username-file "$INLINE_CLOUD_USERNAME_FILE" --password-file "$INLINE_CLOUD_PASSWORD_FILE")
        echo "Using Pesetech cloud username/password from add-on configuration."
      elif [ -n "$PESETECH_CLOUD_TOKEN_FILE" ] && [ -s "$PESETECH_CLOUD_TOKEN_FILE" ]; then
        CLOUD_ARGS+=(--token-file "$PESETECH_CLOUD_TOKEN_FILE")
        echo "Using Pesetech cloud token file $PESETECH_CLOUD_TOKEN_FILE."
      elif [ -n "$PESETECH_CLOUD_USERNAME_FILE" ] && [ -s "$PESETECH_CLOUD_USERNAME_FILE" ] && [ -n "$PESETECH_CLOUD_PASSWORD_FILE" ] && [ -s "$PESETECH_CLOUD_PASSWORD_FILE" ]; then
        CLOUD_ARGS+=(--username-file "$PESETECH_CLOUD_USERNAME_FILE" --password-file "$PESETECH_CLOUD_PASSWORD_FILE")
        echo "Using Pesetech cloud username/password files."
      else
        echo "cloud-fetch needs cloud_token, both cloud_username/cloud_password, $PESETECH_CLOUD_TOKEN_FILE, or both $PESETECH_CLOUD_USERNAME_FILE and $PESETECH_CLOUD_PASSWORD_FILE." >&2
        exit 2
      fi
      echo "Fetching Pesetech cloud mesh for region $PESETECH_CLOUD_REGION into $PESETECH_CLOUD_OUTPUT."
      echo "cloud-fetch does not start D-Bus, BlueZ, or Bluetooth Mesh."
      set +e
      python3 $APP_ROOT/scripts/pesetech_fetch_cloud_mesh.py "${CLOUD_ARGS[@]}"
      CLOUD_FETCH_EXIT=$?
      set -e
      if [ -n "$PESETECH_CLOUD_REPORT" ]; then
        echo "Key-free cloud fetch report was written to $PESETECH_CLOUD_REPORT."
        python3 $APP_ROOT/scripts/pesetech_cloud_report_summary.py "$PESETECH_CLOUD_REPORT" || true
      fi
      if [ -n "$PESETECH_CLOUD_RAW_OUTPUT" ]; then
        echo "Raw cloud response was written to $PESETECH_CLOUD_RAW_OUTPUT; treat it like a secret because it can contain mesh keys."
      fi
      if [ "$CLOUD_FETCH_EXIT" -ne 0 ]; then
        echo "cloud-fetch failed with exit code $CLOUD_FETCH_EXIT." >&2
        exit "$CLOUD_FETCH_EXIT"
      fi
      echo "Cloud mesh fetch completed. Next set operation to import-check, then import, using mesh_json_path $PESETECH_CLOUD_OUTPUT."
      exit 0
    fi

    if [ "$PESETECH_OPERATION" = "ble-scan" ]; then
      RAW_BLE_SCAN="$DATA_DIR/pesetech-ble-scan.txt"
      BLE_SCAN_REPORT="$DATA_DIR/pesetech-ble-scan.json"
      if ! BTMGMT_BIN="$(find_btmgmt)"; then
        echo "btmgmt was not found; cannot run a generic BLE advertisement scan." > "$RAW_BLE_SCAN"
        python3 - "$BLE_SCAN_REPORT" "$RAW_BLE_SCAN" <<'PY'
import json
import sys
import time

report_path, raw_path = sys.argv[1:]
report = {
    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "operation": "ble-scan",
    "status": "failed",
    "exit_code": 1,
    "message": "btmgmt was not found",
    "raw_log": raw_path,
    "dev_found_count": 0,
    "unique_address_count": 0,
    "addresses": [],
    "names": [],
    "sent_light_commands": False,
    "published_mqtt": False,
    "started_bluetooth_meshd": False,
    "provisioned": False,
    "imported": False,
}
with open(report_path, "w", encoding="utf-8") as output:
    json.dump(report, output, indent=2, sort_keys=True)
    output.write("\\n")
PY
        copy_report_to_share "$BLE_SCAN_REPORT" "pesetech-ble-scan.json"
        copy_report_to_share "$RAW_BLE_SCAN" "pesetech-ble-scan.txt"
        exit 1
      fi

      set +e
      python3 $APP_ROOT/scripts/pesetech_ble_scan.py \
        --btmgmt "$BTMGMT_BIN" \
        --mesh-io "$PESETECH_MESH_IO" \
        --seconds "$PESETECH_BLE_SCAN_SECONDS" \
        --raw-output "$RAW_BLE_SCAN" \
        --report-output "$BLE_SCAN_REPORT"
      BLE_SCAN_EXIT="$?"
      set -e
      copy_report_to_share "$BLE_SCAN_REPORT" "pesetech-ble-scan.json"
      copy_report_to_share "$RAW_BLE_SCAN" "pesetech-ble-scan.txt"
      if [ -f "$BLE_SCAN_REPORT" ]; then
        python3 - "$BLE_SCAN_REPORT" <<'PY'
import json
import sys

report_path = sys.argv[1]
with open(report_path, "r", encoding="utf-8") as handle:
    report = json.load(handle)
online = report.get("online_status_probe") or {}
print(
    "BLE scan completed: "
    f"{report.get('dev_found_count', 0)} dev_found event(s), "
    f"{report.get('unique_address_count', 0)} unique address(es), "
    f"names={report.get('names', [])}"
)
print(
    "Online-status summary: "
    f"status={online.get('status')} "
    f"message={online.get('message')} "
    f"records={len(online.get('records') or [])} "
    f"decoded_packets={len(online.get('decoded_packets') or [])} "
    f"failed_packets={len(online.get('failed_packets') or [])} "
    f"attempts={len(online.get('attempts') or [])}"
)
for attempt in online.get("attempts") or []:
    target = attempt.get("target") or {}
    print(
        "  online-status attempt: "
        f"{attempt.get('adapter')} round={attempt.get('round')} "
        f"target={target.get('name') or target.get('unicast')} "
        f"addr={target.get('address')} "
        f"rc={attempt.get('return_code')} "
        f"value_handle={attempt.get('value_handle') or '-'} "
        f"packets={attempt.get('packet_count')} "
        f"decoded={attempt.get('decoded_packet_count')} "
        f"failed={attempt.get('failed_packet_count')}"
    )
PY
      fi
      echo "BLE scan raw transcript tail:"
      tail -n 240 "$RAW_BLE_SCAN" || true
      exit "$BLE_SCAN_EXIT"
    fi

    write_preflight_report() {
      local status="$1"
      local exit_code="$2"
      local message="$3"
      local report_path="$DATA_DIR/pesetech-preflight.json"
      python3 - "$report_path" "$status" "$exit_code" "$message" "$CONFIG_PATH" "$DATA_DIR/store.yaml" "$PESETECH_MQTT_SOURCE" <<'PY'
import json
import sys
import time

report_path, status, exit_code, message, config_path, store_path, mqtt_source = sys.argv[1:]
report = {
    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "operation": "preflight",
    "status": status,
    "exit_code": int(exit_code),
    "message": message,
    "config": config_path,
    "store": store_path,
    "mqtt_source": mqtt_source,
    "sent_light_commands": False,
    "published_mqtt": False,
    "started_bluetooth_meshd": False,
    "provisioned": False,
    "imported": False,
}
with open(report_path, "w", encoding="utf-8") as output:
    json.dump(report, output, indent=2, sort_keys=True)
    output.write("\\n")
PY
    }

    if [ "$PESETECH_OPERATION" = "preflight" ]; then
      if [ ! -s "$CONFIG_PATH" ]; then
        echo "No gateway config found at $CONFIG_PATH. Run provision or import before preflight." >&2
        write_preflight_report "failed" "2" "No gateway config found"
        exit 2
      fi
      set +e
      python3 $APP_ROOT/scripts/pesetech_preflight.py \\
        --config "$CONFIG_PATH" \\
        --store "$DATA_DIR/store.yaml" \\
        --host \\
        --skip-docker \\
        --check-mqtt
      PREFLIGHT_EXIT="$?"
      set -e
      if [ "$PREFLIGHT_EXIT" -eq 0 ]; then
        write_preflight_report "passed" "$PREFLIGHT_EXIT" "Preflight passed"
      else
        write_preflight_report "failed" "$PREFLIGHT_EXIT" "Preflight failed"
      fi
      copy_report_to_share "$DATA_DIR/pesetech-preflight.json" "pesetech-preflight.json"
      exit "$PREFLIGHT_EXIT"
    fi

    if [ "$PESETECH_OPERATION" = "ha-api-check" ]; then
      exec python3 $APP_ROOT/scripts/pesetech_ha_service_smoke.py \\
        --url "$PESETECH_HA_URL" \\
        --entity-id "$PESETECH_HA_ENTITY_ID" \\
        --check-api \\
        --check-entity \\
        --entity-timeout 30 \\
        --candidate-search "${PESETECH_HA_ENTITY_ID#light.}"
    fi

    if [ "$PESETECH_OPERATION" = "diagnostics" ]; then
      DIAG_OUTPUT_DIR="$DATA_DIR"
      if [ -d /share ] && [ -w /share ]; then
        DIAG_OUTPUT_DIR="/share"
      fi
      echo "Collecting redacted diagnostics into $DIAG_OUTPUT_DIR."
      exec python3 $APP_ROOT/scripts/pesetech_diagnostics.py \\
        --config "$CONFIG_PATH" \\
        --store "$DATA_DIR/store.yaml" \\
        --proof-log "$DATA_DIR/pesetech-move-test.jsonl" \\
        --ha-proof-log "$DATA_DIR/pesetech-ha-service-proof.jsonl" \\
        --final-audit-report "$DATA_DIR/pesetech-final-audit.json" \\
        --readiness-report "$DATA_DIR/pesetech-readiness.json" \\
        --status-report "$DATA_DIR/pesetech-status.json" \\
        --runtime-report "$DATA_DIR/pesetech-runtime-check.json" \\
        --mesh-daemon-report "$DATA_DIR/pesetech-mesh-daemon-check.json" \\
        --preflight-report "$DATA_DIR/pesetech-preflight.json" \\
        --import-check-report "$DATA_DIR/pesetech-import-check.json" \\
        --cloud-output "$PESETECH_CLOUD_OUTPUT" \\
        --cloud-raw-output "$PESETECH_CLOUD_RAW_OUTPUT" \\
        --cloud-report "$PESETECH_CLOUD_REPORT" \\
        --cloud-token-file "$PESETECH_CLOUD_TOKEN_FILE" \\
        --cloud-username-file "$PESETECH_CLOUD_USERNAME_FILE" \\
        --cloud-password-file "$PESETECH_CLOUD_PASSWORD_FILE" \\
        --cloud-region "$PESETECH_CLOUD_REGION" \\
        --cloud-candidate "$PESETECH_CLOUD_CANDIDATE" \\
        --cloud-home-id "$PESETECH_CLOUD_HOME_ID" \\
        --import-mesh-candidate "$PESETECH_IMPORT_MESH_CANDIDATE" \\
        --ha-url "$PESETECH_HA_URL" \\
        --ha-entity-id "$PESETECH_HA_ENTITY_ID" \\
        --ha-api-context \\
        --ha-candidate-search "${PESETECH_HA_ENTITY_ID#light.}" \\
        --ha-require-attributes \\
        --ha-require-mqtt-state \\
        --ha-require-mqtt-attributes \\
        --mqtt-source "$PESETECH_MQTT_SOURCE" \\
        --compose-dir $APP_ROOT/docker \\
        --output-dir "$DIAG_OUTPUT_DIR" \\
        --live-discovery \\
        --skip-docker
    fi

    if [ "$PESETECH_OPERATION" = "import-check" ]; then
      if [ "$PESETECH_IMPORT_FORCE" != "true" ] && [ -s "$CONFIG_PATH" ] && [ -s "$DATA_DIR/store.yaml" ]; then
        echo "Existing imported gateway config and store found; import-check would not rewrite them. Real import would skip and start the gateway service. Set import_force to true to validate an intentional overwrite."
        exit 0
      fi
      IMPORT_ARGS=(
        "$PESETECH_MESH_JSON"
        --config "$CONFIG_PATH"
        --store "$DATA_DIR/store.yaml"
        --device-id "$PESETECH_DEVICE_ID"
        --device-name "$PESETECH_DEVICE_NAME"
        --default-entity-id "$PESETECH_HA_ENTITY_ID"
      )
      if [ "$PESETECH_IMPORT_MESH_CANDIDATE" != "0" ]; then
        IMPORT_ARGS+=(--mesh-candidate "$PESETECH_IMPORT_MESH_CANDIDATE")
      fi
      if [ -n "$PESETECH_IMPORT_NODE_UUID" ]; then
        IMPORT_ARGS+=(--node-uuid "$PESETECH_IMPORT_NODE_UUID")
      fi
      if [ -n "$PESETECH_IMPORT_NODE_UNICAST" ]; then
        IMPORT_ARGS+=(--node-unicast "$PESETECH_IMPORT_NODE_UNICAST")
      fi
      if [ -n "$PESETECH_IMPORT_LOCAL_ADDRESS" ]; then
        IMPORT_ARGS+=(--local-address "$PESETECH_IMPORT_LOCAL_ADDRESS")
      fi
      if [ "$PESETECH_IMPORT_FORCE" = "true" ]; then
        IMPORT_ARGS+=(--force)
      fi
      echo "Validating mesh.json import plan without writing config or store."
      set +e
      python3 $APP_ROOT/scripts/pesetech_import_telink_mesh.py "${IMPORT_ARGS[@]}" --dry-run --report-output "$DATA_DIR/pesetech-import-check.json"
      IMPORT_CHECK_EXIT="$?"
      set -e
      copy_report_to_share "$DATA_DIR/pesetech-import-check.json" "pesetech-import-check.json"
      exit "$IMPORT_CHECK_EXIT"
    fi

    if [ "$PESETECH_OPERATION" = "import" ]; then
      if [ "$PESETECH_IMPORT_FORCE" != "true" ] && [ -s "$CONFIG_PATH" ] && [ -s "$DATA_DIR/store.yaml" ]; then
        echo "Existing imported gateway config and store found; skipping mesh.json import. Set import_force to true to overwrite."
        exit 0
      fi
      IMPORT_ARGS=(
        "$PESETECH_MESH_JSON"
        --config "$CONFIG_PATH"
        --store "$DATA_DIR/store.yaml"
        --device-id "$PESETECH_DEVICE_ID"
        --device-name "$PESETECH_DEVICE_NAME"
        --default-entity-id "$PESETECH_HA_ENTITY_ID"
      )
      if [ "$PESETECH_IMPORT_MESH_CANDIDATE" != "0" ]; then
        IMPORT_ARGS+=(--mesh-candidate "$PESETECH_IMPORT_MESH_CANDIDATE")
      fi
      if [ -n "$PESETECH_IMPORT_NODE_UUID" ]; then
        IMPORT_ARGS+=(--node-uuid "$PESETECH_IMPORT_NODE_UUID")
      fi
      if [ -n "$PESETECH_IMPORT_NODE_UNICAST" ]; then
        IMPORT_ARGS+=(--node-unicast "$PESETECH_IMPORT_NODE_UNICAST")
      fi
      if [ -n "$PESETECH_IMPORT_LOCAL_ADDRESS" ]; then
        IMPORT_ARGS+=(--local-address "$PESETECH_IMPORT_LOCAL_ADDRESS")
      fi
      if [ "$PESETECH_IMPORT_FORCE" = "true" ]; then
        IMPORT_ARGS+=(--force)
      fi
      python3 $APP_ROOT/scripts/pesetech_import_telink_mesh.py "${IMPORT_ARGS[@]}"
      echo "Import completed without starting Bluetooth Mesh. Run preflight, then readiness-test or service."
      exit 0
    fi

    if [ "$PESETECH_OPERATION" = "readiness-test" ] || [ "$PESETECH_OPERATION" = "ha-service-test" ] || [ "$PESETECH_OPERATION" = "proof-test" ]; then
      if [ ! -s "$CONFIG_PATH" ]; then
        echo "No gateway config found at $CONFIG_PATH. Run provision or import before $PESETECH_OPERATION." >&2
        exit 2
      fi
      echo "Checking Home Assistant API/token before starting Bluetooth Mesh."
      python3 $APP_ROOT/scripts/pesetech_ha_service_smoke.py \\
        --url "$PESETECH_HA_URL" \\
        --check-api
    fi

    write_mesh_daemon_report() {
      local status="$1"
      local message="$2"
      local meshd_bin="${MESHD_BIN:-}"
      local report_path="$DATA_DIR/pesetech-mesh-daemon-check.json"
      python3 - "$report_path" "$status" "$message" "$meshd_bin" "$DATA_DIR/mesh-storage" "${MESHD_STARTUP_TIMEOUT:-}" "${BLUETOOTH_ADAPTERS[@]}" <<'PY'
import json
import sys
import time

report_path, status, message, meshd_bin, storage, startup_timeout, *adapters = sys.argv[1:]
report = {
    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "operation": "mesh-daemon-check",
    "status": status,
    "message": message,
    "bluetooth_adapters": [adapter.rsplit("/", 1)[-1] for adapter in adapters],
    "bluetooth_meshd": meshd_bin,
    "storage": storage,
    "startup_timeout_seconds": int(startup_timeout or "0"),
    "sent_light_commands": False,
    "published_mqtt": False,
    "provisioned": False,
    "imported": False,
}
with open(report_path, "w", encoding="utf-8") as output:
    json.dump(report, output, indent=2, sort_keys=True)
    output.write("\\n")
PY
    }

    shopt -s nullglob
    BLUETOOTH_ADAPTERS=(/sys/class/bluetooth/hci*)
    shopt -u nullglob
    if [ "${#BLUETOOTH_ADAPTERS[@]}" -eq 0 ]; then
      echo "Warning: no /sys/class/bluetooth/hci* adapter was found; bluetooth-meshd needs a Linux Bluetooth adapter exposed to the app." >&2
      if [ "$PESETECH_OPERATION" = "mesh-daemon-check" ]; then
        write_mesh_daemon_report "failed" "no hci* Bluetooth adapter was visible"
        copy_report_to_share "$DATA_DIR/pesetech-mesh-daemon-check.json" "pesetech-mesh-daemon-check.json"
        exit 1
      fi
    else
      echo "Bluetooth adapters visible to the app: ${BLUETOOTH_ADAPTERS[*]}"
    fi

    if [ -n "$PESETECH_MESH_IO" ]; then
      REQUESTED_MESH_HCI="$(printf '%s' "$PESETECH_MESH_IO" | sed -n 's/.*\\(hci[0-9][0-9]*\\).*/\\1/p' | head -n 1)"
      if [ -n "$REQUESTED_MESH_HCI" ] && [ ! -e "/sys/class/bluetooth/$REQUESTED_MESH_HCI" ] && [ "${#BLUETOOTH_ADAPTERS[@]}" -eq 1 ]; then
        AVAILABLE_MESH_HCI="$(basename "${BLUETOOTH_ADAPTERS[0]}")"
        PESETECH_MESH_IO="${PESETECH_MESH_IO/$REQUESTED_MESH_HCI/$AVAILABLE_MESH_HCI}"
        echo "Configured Bluetooth Mesh I/O referenced missing $REQUESTED_MESH_HCI; using visible adapter $AVAILABLE_MESH_HCI instead: $PESETECH_MESH_IO"
      fi
    fi

    if ! MESHD_BIN="$(find_bluetooth_meshd)"; then
      echo "bluetooth-meshd was not found; the BlueZ mesh build did not install the daemon." >&2
      if [ "$PESETECH_OPERATION" = "mesh-daemon-check" ]; then
        write_mesh_daemon_report "failed" "bluetooth-meshd was not found"
        copy_report_to_share "$DATA_DIR/pesetech-mesh-daemon-check.json" "pesetech-mesh-daemon-check.json"
      fi
      exit 1
    fi

    if [ "$PESETECH_MESH_ADAPTER_POWER_OFF" = "true" ]; then
      if BTMGMT_BIN="$(find_btmgmt)"; then
        echo "Powering off selected Bluetooth adapter(s) before bluetooth-meshd using $BTMGMT_BIN."
        for adapter_index in $(mesh_io_indexes "$PESETECH_MESH_IO"); do
          if [ -n "$adapter_index" ]; then
            echo "btmgmt --index $adapter_index power off"
            "$BTMGMT_BIN" --index "$adapter_index" power off || true
          fi
        done
        if [ "$PESETECH_MESH_ADAPTER_POWER_OFF_DELAY" -gt 0 ]; then
          sleep "$PESETECH_MESH_ADAPTER_POWER_OFF_DELAY"
        fi
      else
        echo "mesh_adapter_power_off is true, but btmgmt was not found." >&2
      fi
    fi

    if ! service dbus start; then
      if [ "$PESETECH_OPERATION" = "mesh-daemon-check" ]; then
        write_mesh_daemon_report "failed" "D-Bus service failed to start"
        copy_report_to_share "$DATA_DIR/pesetech-mesh-daemon-check.json" "pesetech-mesh-daemon-check.json"
      fi
      exit 1
    fi
    MESHD_ARGS=(--storage "$DATA_DIR/mesh-storage")
    if [ -n "$PESETECH_MESH_IO" ]; then
      MESHD_ARGS+=(--io "$PESETECH_MESH_IO")
      echo "bluetooth-meshd I/O: $PESETECH_MESH_IO"
    else
      echo "bluetooth-meshd I/O: auto"
    fi
    if [ "$PESETECH_MESH_DEBUG" = "true" ]; then
      MESHD_ARGS+=(--nodetach --debug)
      echo "bluetooth-meshd debug logging enabled."
    fi

    "$MESHD_BIN" "${MESHD_ARGS[@]}" &
    MESHD_PID="$!"

    MESHD_STARTUP_TIMEOUT="${PESETECH_MESH_STARTUP_TIMEOUT:-5}"
    for _ in $(seq 1 "$MESHD_STARTUP_TIMEOUT"); do
      sleep 1
      if ! kill -0 "$MESHD_PID" >/dev/null 2>&1; then
        echo "bluetooth-meshd exited during startup; make sure Home Assistant host Bluetooth is free and an hci* adapter is available to the app." >&2
        if [ "$PESETECH_OPERATION" = "mesh-daemon-check" ]; then
          write_mesh_daemon_report "failed" "bluetooth-meshd exited during startup"
          copy_report_to_share "$DATA_DIR/pesetech-mesh-daemon-check.json" "pesetech-mesh-daemon-check.json"
        fi
        exit 1
      fi
    done

    if ! python3 $APP_ROOT/scripts/pesetech_bluez_mesh_introspect.py; then
      if [ "$PESETECH_OPERATION" = "mesh-daemon-check" ]; then
        write_mesh_daemon_report "failed" "bluetooth-meshd D-Bus object did not expose required BlueZ Mesh interfaces"
        copy_report_to_share "$DATA_DIR/pesetech-mesh-daemon-check.json" "pesetech-mesh-daemon-check.json"
      fi
      kill "$MESHD_PID" >/dev/null 2>&1 || true
      wait "$MESHD_PID" >/dev/null 2>&1 || true
      exit 1
    fi

    if [ "$PESETECH_OPERATION" = "mesh-daemon-check" ]; then
      echo "bluetooth-meshd stayed running for ${MESHD_STARTUP_TIMEOUT}s with storage $DATA_DIR/mesh-storage."
      write_mesh_daemon_report "passed" "bluetooth-meshd stayed running for ${MESHD_STARTUP_TIMEOUT}s"
      copy_report_to_share "$DATA_DIR/pesetech-mesh-daemon-check.json" "pesetech-mesh-daemon-check.json"
      kill "$MESHD_PID" >/dev/null 2>&1 || true
      wait "$MESHD_PID" >/dev/null 2>&1 || true
      echo "Bluetooth Mesh daemon check passed. No provisioning, import, MQTT, or light-control commands were run."
      exit 0
    fi

    cd "$APP_ROOT/gateway"

    case "$PESETECH_OPERATION" in
      service)
        if [ ! -s "$CONFIG_PATH" ]; then
          echo "No gateway config found at $CONFIG_PATH. Set skylight_uuid for first-time service config, or run provision/import before service." >&2
          exit 2
        fi
        exec python3 gateway.py --basedir "$DATA_DIR" --reload
        ;;
      scan)
        export PESETECH_MESH_SCAN_REPORT="$DATA_DIR/pesetech-mesh-scan.json"
        export PESETECH_MESH_SCAN_TEXT="$DATA_DIR/pesetech-mesh-scan.txt"
        rm -f "$PESETECH_MESH_SCAN_REPORT" "$PESETECH_MESH_SCAN_TEXT"
        if python3 gateway.py --basedir "$DATA_DIR" --reload scan --seconds "$PESETECH_MESH_SCAN_SECONDS" --repeat "$PESETECH_MESH_SCAN_REPEAT" --stop-on-found; then
          SCAN_EXIT=0
        else
          SCAN_EXIT=$?
        fi
        if [ -f "$PESETECH_MESH_SCAN_TEXT" ]; then
          cat "$PESETECH_MESH_SCAN_TEXT"
        fi
        copy_report_to_share "$PESETECH_MESH_SCAN_REPORT" "pesetech-mesh-scan.json"
        copy_report_to_share "$PESETECH_MESH_SCAN_TEXT" "pesetech-mesh-scan.txt"
        exit "$SCAN_EXIT"
        ;;
      provision)
        python3 gateway.py --basedir "$DATA_DIR" --reload prov --uuid "$PESETECH_UUID" add
        python3 gateway.py --basedir "$DATA_DIR" --reload prov --uuid "$PESETECH_UUID" config
        exec python3 gateway.py --basedir "$DATA_DIR" --reload prov list
        ;;
      configure)
        python3 gateway.py --basedir "$DATA_DIR" --reload prov --uuid "$PESETECH_UUID" config
        exec python3 gateway.py --basedir "$DATA_DIR" --reload prov list
        ;;
      import)
        if [ "$PESETECH_IMPORT_FORCE" != "true" ] && [ -s "$CONFIG_PATH" ] && [ -s "$DATA_DIR/store.yaml" ]; then
          echo "Existing imported gateway config and store found; skipping mesh.json import. Set import_force to true to overwrite."
          exec python3 gateway.py --basedir "$DATA_DIR" --reload
        fi
        IMPORT_ARGS=(
          "$PESETECH_MESH_JSON"
          --config "$CONFIG_PATH"
          --store "$DATA_DIR/store.yaml"
          --device-id "$PESETECH_DEVICE_ID"
          --device-name "$PESETECH_DEVICE_NAME"
          --default-entity-id "$PESETECH_HA_ENTITY_ID"
        )
        if [ "$PESETECH_IMPORT_MESH_CANDIDATE" != "0" ]; then
          IMPORT_ARGS+=(--mesh-candidate "$PESETECH_IMPORT_MESH_CANDIDATE")
        fi
        if [ -n "$PESETECH_IMPORT_NODE_UUID" ]; then
          IMPORT_ARGS+=(--node-uuid "$PESETECH_IMPORT_NODE_UUID")
        fi
        if [ -n "$PESETECH_IMPORT_NODE_UNICAST" ]; then
          IMPORT_ARGS+=(--node-unicast "$PESETECH_IMPORT_NODE_UNICAST")
        fi
        if [ -n "$PESETECH_IMPORT_LOCAL_ADDRESS" ]; then
          IMPORT_ARGS+=(--local-address "$PESETECH_IMPORT_LOCAL_ADDRESS")
        fi
        if [ "$PESETECH_IMPORT_FORCE" = "true" ]; then
          IMPORT_ARGS+=(--force)
        fi
        python3 $APP_ROOT/scripts/pesetech_import_telink_mesh.py "${IMPORT_ARGS[@]}"
        exec python3 gateway.py --basedir "$DATA_DIR" --reload
        ;;
      readiness-test)
        if [ ! -s "$CONFIG_PATH" ]; then
          echo "No gateway config found at $CONFIG_PATH. Run provision or import before readiness-test." >&2
          exit 2
        fi
        READINESS_REPORT="$DATA_DIR/pesetech-readiness.json"
        rm -f "$READINESS_REPORT"
        echo "Starting gateway for readiness-test. No light-control commands will be published."
        echo "This checks retained MQTT discovery and waits for Home Assistant entity $PESETECH_HA_ENTITY_ID."
        python3 gateway.py --basedir "$DATA_DIR" --reload &
        GATEWAY_PID="$!"
        trap 'kill "$GATEWAY_PID" "$MESHD_PID" >/dev/null 2>&1 || true' EXIT
        sleep 2
        if ! kill -0 "$GATEWAY_PID" >/dev/null 2>&1; then
          echo "Gateway exited before readiness-test checks could run." >&2
          exit 1
        fi
        python3 $APP_ROOT/scripts/pesetech_mqtt_discovery.py --config "$CONFIG_PATH" --require-retained --discovery-timeout 30
        python3 $APP_ROOT/scripts/pesetech_ha_service_smoke.py \\
          --url "$PESETECH_HA_URL" \\
          --entity-id "$PESETECH_HA_ENTITY_ID" \\
          --check-entity \\
          --entity-timeout 30 \\
          --candidate-search "${PESETECH_HA_ENTITY_ID#light.}"
        python3 - "$READINESS_REPORT" "$CONFIG_PATH" "$DATA_DIR/store.yaml" "$PESETECH_HA_URL" "$PESETECH_HA_ENTITY_ID" "$PESETECH_MQTT_SOURCE" <<'PY'
import json
import sys
import time

report_path, config_path, store_path, ha_url, ha_entity_id, mqtt_source = sys.argv[1:]
report = {
    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "operation": "readiness-test",
    "status": "passed",
    "sent_light_commands": False,
    "checks": [
        "home_assistant_api",
        "retained_mqtt_discovery",
        "home_assistant_entity",
    ],
    "config": config_path,
    "store": store_path,
    "ha_url": ha_url,
    "ha_entity_id": ha_entity_id,
    "mqtt_source": mqtt_source,
}
with open(report_path, "w", encoding="utf-8") as output:
    json.dump(report, output, indent=2, sort_keys=True)
    output.write("\\n")
PY
        echo "Readiness-test passed without publishing light-control commands."
        echo "Wrote readiness report to $READINESS_REPORT."
        copy_report_to_share "$READINESS_REPORT" "pesetech-readiness.json"
        echo "Keeping the gateway service running after readiness-test; switch operation when you need another action."
        wait "$GATEWAY_PID"
        ;;
      read-state)
        if [ ! -s "$CONFIG_PATH" ]; then
          echo "No gateway config found at $CONFIG_PATH. Run provision or import before read-state." >&2
          exit 2
        fi
        STATE_READ_REPORT="$DATA_DIR/pesetech-state-read.json"
        rm -f "$STATE_READ_REPORT"
        export PESETECH_STATE_READ_REPORT="$STATE_READ_REPORT"
        echo "Starting gateway read-state. This sends read-only Bluetooth Mesh Get messages and does not publish light-control commands."
        set +e
        timeout 180s python3 gateway.py --basedir "$DATA_DIR" --reload read-state --timeout 20
        STATE_READ_EXIT="$?"
        set -e
        copy_report_to_share "$STATE_READ_REPORT" "pesetech-state-read.json"
        if [ -f "$STATE_READ_REPORT" ]; then
          if [ "$STATE_READ_EXIT" -eq 0 ]; then
            STATE_READ_STATUS="passed"
          else
            STATE_READ_STATUS="failed"
          fi
          python3 $APP_ROOT/scripts/pesetech_publish_ha_state.py \
            --report "$STATE_READ_REPORT" \
            --ha-url "$PESETECH_HA_URL" \
            --entity-id sensor.pesetech_ble_mesh_read_state \
            --status "$STATE_READ_STATUS" \
            --message "read-state exit code $STATE_READ_EXIT" || true
          echo "Pesetech state read report:"
          cat "$STATE_READ_REPORT"
        else
          echo "No Pesetech state read report was written." >&2
          python3 $APP_ROOT/scripts/pesetech_publish_ha_state.py \
            --report "$STATE_READ_REPORT" \
            --ha-url "$PESETECH_HA_URL" \
            --entity-id sensor.pesetech_ble_mesh_read_state \
            --status failed \
            --message "read-state exited without a report; exit code $STATE_READ_EXIT" || true
        fi
        if [ "$STATE_READ_EXIT" -eq 0 ]; then
          echo "State read completed."
        else
          echo "State read failed with exit code $STATE_READ_EXIT." >&2
        fi
        exit "$STATE_READ_EXIT"
        ;;
      model-scope)
        if [ ! -s "$CONFIG_PATH" ]; then
          echo "No gateway config found at $CONFIG_PATH. Run provision or import before model-scope." >&2
          exit 2
        fi
        MODEL_SCOPE_REPORT="$DATA_DIR/pesetech-model-scope.json"
        rm -f "$MODEL_SCOPE_REPORT"
        export PESETECH_MODEL_SCOPE_REPORT="$MODEL_SCOPE_REPORT"
        echo "Starting gateway model-scope. This sends read-only Bluetooth Mesh Get messages and does not publish light-control commands."
        set +e
        timeout 240s python3 gateway.py --basedir "$DATA_DIR" --reload model-scope --timeout 20
        MODEL_SCOPE_EXIT="$?"
        set -e
        copy_report_to_share "$MODEL_SCOPE_REPORT" "pesetech-model-scope.json"
        if [ -f "$MODEL_SCOPE_REPORT" ]; then
          if [ "$MODEL_SCOPE_EXIT" -eq 0 ]; then
            MODEL_SCOPE_STATUS="passed"
          else
            MODEL_SCOPE_STATUS="failed"
          fi
          python3 $APP_ROOT/scripts/pesetech_publish_ha_state.py \
            --report "$MODEL_SCOPE_REPORT" \
            --ha-url "$PESETECH_HA_URL" \
            --entity-id sensor.pesetech_ble_mesh_model_scope \
            --status "$MODEL_SCOPE_STATUS" \
            --message "model-scope exit code $MODEL_SCOPE_EXIT" || true
          echo "Pesetech model scope report:"
          cat "$MODEL_SCOPE_REPORT"
        else
          echo "No Pesetech model scope report was written." >&2
          python3 $APP_ROOT/scripts/pesetech_publish_ha_state.py \
            --report "$MODEL_SCOPE_REPORT" \
            --ha-url "$PESETECH_HA_URL" \
            --entity-id sensor.pesetech_ble_mesh_model_scope \
            --status failed \
            --message "model-scope exited without a report; exit code $MODEL_SCOPE_EXIT" || true
        fi
        if [ "$MODEL_SCOPE_EXIT" -eq 0 ]; then
          echo "Model scope probe completed."
        else
          echo "Model scope probe failed with exit code $MODEL_SCOPE_EXIT." >&2
        fi
        exit "$MODEL_SCOPE_EXIT"
        ;;
      raw-command)
        if [ ! -s "$CONFIG_PATH" ]; then
          echo "No gateway config found at $CONFIG_PATH. Run provision or import before raw-command." >&2
          exit 2
        fi
        RAW_COMMAND_REPORT="$DATA_DIR/pesetech-raw-command.json"
        rm -f "$RAW_COMMAND_REPORT"
        export PESETECH_RAW_COMMAND_REPORT="$RAW_COMMAND_REPORT"
        RAW_ARGS=(
          --command "$PESETECH_RAW_COMMAND"
          --address-model "$PESETECH_RAW_ADDRESS_MODEL"
          --retransmissions "$PESETECH_RAW_RETRANSMISSIONS"
          --send-interval-ms "$PESETECH_RAW_SEND_INTERVAL_MS"
          --timeout "$PESETECH_RAW_TIMEOUT"
        )
        if [ -n "$PESETECH_RAW_NODE" ]; then
          RAW_ARGS+=(--node "$PESETECH_RAW_NODE")
        fi
        if [ -n "$PESETECH_RAW_ADDRESS" ]; then
          RAW_ARGS+=(--address "$PESETECH_RAW_ADDRESS")
        fi
        if [ "$PESETECH_RAW_COMMAND" = "raw" ]; then
          RAW_ARGS+=(--opcode "$PESETECH_RAW_OPCODE" --payload "$PESETECH_RAW_PAYLOAD")
        fi
        if [ "$PESETECH_RAW_COMMAND" = "pesetech-brightness" ]; then
          RAW_ARGS+=(--brightness "$PESETECH_RAW_BRIGHTNESS")
        fi
        if [ "$PESETECH_RAW_READ_AFTER" != "true" ]; then
          RAW_ARGS+=(--no-read-after)
        fi
        echo "Starting gateway raw-command. This can send real light-control commands; target node selector: ${PESETECH_RAW_NODE:-all configured lights}."
        set +e
        timeout 180s python3 gateway.py --basedir "$DATA_DIR" --reload raw-command "${RAW_ARGS[@]}"
        RAW_COMMAND_EXIT="$?"
        set -e
        copy_report_to_share "$RAW_COMMAND_REPORT" "pesetech-raw-command.json"
        if [ -f "$RAW_COMMAND_REPORT" ]; then
          if [ "$RAW_COMMAND_EXIT" -eq 0 ]; then
            RAW_COMMAND_STATUS="passed"
          else
            RAW_COMMAND_STATUS="failed"
          fi
          python3 $APP_ROOT/scripts/pesetech_publish_ha_state.py \
            --report "$RAW_COMMAND_REPORT" \
            --ha-url "$PESETECH_HA_URL" \
            --entity-id sensor.pesetech_ble_mesh_raw_command \
            --status "$RAW_COMMAND_STATUS" \
            --message "raw-command exit code $RAW_COMMAND_EXIT" || true
          echo "Pesetech raw command report:"
          cat "$RAW_COMMAND_REPORT"
        else
          echo "No Pesetech raw command report was written." >&2
          python3 $APP_ROOT/scripts/pesetech_publish_ha_state.py \
            --report "$RAW_COMMAND_REPORT" \
            --ha-url "$PESETECH_HA_URL" \
            --entity-id sensor.pesetech_ble_mesh_raw_command \
            --status failed \
            --message "raw-command exited without a report; exit code $RAW_COMMAND_EXIT" || true
        fi
        if [ "$RAW_COMMAND_EXIT" -eq 0 ]; then
          echo "Raw command completed."
        else
          echo "Raw command failed with exit code $RAW_COMMAND_EXIT." >&2
        fi
        exit "$RAW_COMMAND_EXIT"
        ;;
      skylight-programs)
        if [ ! -s "$CONFIG_PATH" ]; then
          echo "No gateway config found at $CONFIG_PATH. Run provision or import before skylight-programs." >&2
          exit 2
        fi
        SKYLIGHT_PROGRAMS_REPORT="$DATA_DIR/pesetech-skylight-programs.json"
        rm -f "$SKYLIGHT_PROGRAMS_REPORT"
        export PESETECH_SKYLIGHT_PROGRAMS_REPORT="$SKYLIGHT_PROGRAMS_REPORT"
        export PESETECH_SKYLIGHT_PROGRAMS_PATH="$PESETECH_SKYLIGHT_PROGRAMS_PATH"
        if [ "$PESETECH_SKYLIGHT_PROGRAMS_DRY_RUN" = "true" ] && [ ! -s "$PESETECH_SKYLIGHT_PROGRAMS_PATH" ] && [ -s "$APP_ROOT/docker/config/pesetech-skylight-programs.json.sample" ]; then
          echo "No skylight programs config found at $PESETECH_SKYLIGHT_PROGRAMS_PATH; dry-run will use bundled sample config."
          PESETECH_SKYLIGHT_PROGRAMS_PATH="$APP_ROOT/docker/config/pesetech-skylight-programs.json.sample"
        fi
        PROGRAM_ARGS=(--config "$PESETECH_SKYLIGHT_PROGRAMS_PATH")
        if [ "$PESETECH_SKYLIGHT_PROGRAMS_DRY_RUN" = "true" ]; then
          PROGRAM_ARGS+=(--dry-run)
          echo "Starting gateway skylight-programs dry-run from $PESETECH_SKYLIGHT_PROGRAMS_PATH. No light-control commands will be sent."
        else
          echo "Starting gateway skylight-programs from $PESETECH_SKYLIGHT_PROGRAMS_PATH. This sends real app-compatible skylight program commands."
        fi
        set +e
        timeout 180s python3 gateway.py --basedir "$DATA_DIR" --reload skylight-programs "${PROGRAM_ARGS[@]}"
        SKYLIGHT_PROGRAMS_EXIT="$?"
        set -e
        copy_report_to_share "$SKYLIGHT_PROGRAMS_REPORT" "pesetech-skylight-programs.json"
        if [ -f "$SKYLIGHT_PROGRAMS_REPORT" ]; then
          if [ "$SKYLIGHT_PROGRAMS_EXIT" -eq 0 ]; then
            SKYLIGHT_PROGRAMS_STATUS="passed"
          else
            SKYLIGHT_PROGRAMS_STATUS="failed"
          fi
          python3 $APP_ROOT/scripts/pesetech_publish_ha_state.py \
            --report "$SKYLIGHT_PROGRAMS_REPORT" \
            --ha-url "$PESETECH_HA_URL" \
            --entity-id sensor.pesetech_ble_mesh_skylight_programs \
            --status "$SKYLIGHT_PROGRAMS_STATUS" \
            --message "skylight-programs exit code $SKYLIGHT_PROGRAMS_EXIT" || true
          echo "Pesetech skylight programs report:"
          cat "$SKYLIGHT_PROGRAMS_REPORT"
        else
          echo "No Pesetech skylight programs report was written." >&2
          python3 $APP_ROOT/scripts/pesetech_publish_ha_state.py \
            --report "$SKYLIGHT_PROGRAMS_REPORT" \
            --ha-url "$PESETECH_HA_URL" \
            --entity-id sensor.pesetech_ble_mesh_skylight_programs \
            --status failed \
            --message "skylight-programs exited without a report; exit code $SKYLIGHT_PROGRAMS_EXIT" || true
        fi
        if [ "$SKYLIGHT_PROGRAMS_EXIT" -eq 0 ]; then
          echo "Skylight programs completed."
        else
          echo "Skylight programs failed with exit code $SKYLIGHT_PROGRAMS_EXIT." >&2
        fi
        exit "$SKYLIGHT_PROGRAMS_EXIT"
        ;;
      move-test)
        if [ ! -s "$CONFIG_PATH" ]; then
          echo "No gateway config found at $CONFIG_PATH. Run provision or import before move-test." >&2
          exit 2
        fi
        PROOF_LOG="$DATA_DIR/pesetech-move-test.jsonl"
        rm -f "$PROOF_LOG"
        echo "Starting gateway for move-test. It first sets a dim cool baseline and turns off; then watch the real skylight for on, brightness, warm, cool, and off changes."
        echo "Writing a fresh move-test proof log to $PROOF_LOG."
        echo "This checks MQTT/gateway state and may move the real light, but it is not a visual proof because the add-on cannot ask you for yes/no observations."
        python3 gateway.py --basedir "$DATA_DIR" --reload &
        GATEWAY_PID="$!"
        trap 'kill "$GATEWAY_PID" "$MESHD_PID" >/dev/null 2>&1 || true' EXIT
        sleep 2
        if ! kill -0 "$GATEWAY_PID" >/dev/null 2>&1; then
          echo "Gateway exited before move-test commands could run." >&2
          exit 1
        fi
        python3 $APP_ROOT/scripts/pesetech_mqtt_discovery.py --config "$CONFIG_PATH" --require-retained --discovery-timeout 30
        python3 $APP_ROOT/scripts/pesetech_mqtt_smoke.py --config "$CONFIG_PATH" --proof-log "$PROOF_LOG" --wait-state --precondition-visible-start --delay 2.5
        python3 $APP_ROOT/scripts/pesetech_verify_proof.py "$PROOF_LOG" --config "$CONFIG_PATH" --allow-unobserved
        copy_report_to_share "$PROOF_LOG" "pesetech-move-test.jsonl"
        echo "Move-test completed. Proof log with observed=null entries: $PROOF_LOG"
        ;;
      ha-service-test)
        if [ ! -s "$CONFIG_PATH" ]; then
          echo "No gateway config found at $CONFIG_PATH. Run provision or import before ha-service-test." >&2
          exit 2
        fi
        HA_PROOF_LOG="$DATA_DIR/pesetech-ha-service-proof.jsonl"
        rm -f "$HA_PROOF_LOG"
        echo "Starting gateway for ha-service-test. It first sets a dim cool baseline and turns off; then watch the real skylight while Home Assistant light services run on $PESETECH_HA_ENTITY_ID."
        echo "Writing a fresh Home Assistant service proof log to $HA_PROOF_LOG."
        echo "This calls Home Assistant's own light.turn_on/light.turn_off services through $PESETECH_HA_URL and checks HA state plus MQTT bridge state."
        echo "The app cannot ask for per-step yes/no visual proof, so use your own observation for the physical-light part."
        python3 gateway.py --basedir "$DATA_DIR" --reload &
        GATEWAY_PID="$!"
        trap 'kill "$GATEWAY_PID" "$MESHD_PID" >/dev/null 2>&1 || true' EXIT
        sleep 2
        if ! kill -0 "$GATEWAY_PID" >/dev/null 2>&1; then
          echo "Gateway exited before Home Assistant service commands could run." >&2
          exit 1
        fi
        python3 $APP_ROOT/scripts/pesetech_mqtt_discovery.py --config "$CONFIG_PATH" --require-retained --discovery-timeout 30
        python3 $APP_ROOT/scripts/pesetech_ha_service_smoke.py \\
          --url "$PESETECH_HA_URL" \\
          --entity-id "$PESETECH_HA_ENTITY_ID" \\
          --check-entity \\
          --entity-timeout 30 \\
          --candidate-search "${PESETECH_HA_ENTITY_ID#light.}"
        python3 $APP_ROOT/scripts/pesetech_ha_service_smoke.py \\
          --url "$PESETECH_HA_URL" \\
          --entity-id "$PESETECH_HA_ENTITY_ID" \\
          --proof-log "$HA_PROOF_LOG" \\
          --precondition-visible-start \\
          --wait-state \\
          --wait-attributes \\
          --wait-mqtt-state \\
          --wait-mqtt-attributes \\
          --mqtt-config "$CONFIG_PATH"
        python3 $APP_ROOT/scripts/pesetech_verify_ha_service_proof.py "$HA_PROOF_LOG" \\
          --url "$PESETECH_HA_URL" \\
          --entity-id "$PESETECH_HA_ENTITY_ID" \\
          --require-attributes \\
          --require-mqtt-state \\
          --require-mqtt-attributes \\
          --allow-unobserved
        copy_report_to_share "$HA_PROOF_LOG" "pesetech-ha-service-proof.jsonl"
        echo "Home Assistant service test completed. Proof log with observed=null entries: $HA_PROOF_LOG"
        ;;
      proof-test)
        if [ ! -s "$CONFIG_PATH" ]; then
          echo "No gateway config found at $CONFIG_PATH. Run provision or import before proof-test." >&2
          exit 2
        fi
        PROOF_LOG="$DATA_DIR/pesetech-move-test.jsonl"
        HA_PROOF_LOG="$DATA_DIR/pesetech-ha-service-proof.jsonl"
        FINAL_AUDIT_REPORT="$DATA_DIR/pesetech-final-audit.json"
        PROOF_RUN_ID="$(python3 - <<'PY'
import time
from uuid import uuid4

print(f"pesetech-addon-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid4().hex[:8]}")
PY
)"
        rm -f "$PROOF_LOG" "$HA_PROOF_LOG" "$FINAL_AUDIT_REPORT"
        echo "Starting gateway for proof-test. Each proof half first sets a dim cool baseline and turns off; then watch the real skylight for the MQTT sequence and then the Home Assistant service sequence."
        echo "Writing fresh proof logs to $PROOF_LOG and $HA_PROOF_LOG with run id $PROOF_RUN_ID."
        echo "Writing final audit JSON report to $FINAL_AUDIT_REPORT."
        echo "This runs the final audit with --allow-unobserved because the app cannot ask for per-step yes/no visual proof."
        echo "The final audit JSON will pass only as technical state proof and will set strict_visual_proof=false."
        python3 gateway.py --basedir "$DATA_DIR" --reload &
        GATEWAY_PID="$!"
        trap 'kill "$GATEWAY_PID" "$MESHD_PID" >/dev/null 2>&1 || true' EXIT
        sleep 2
        if ! kill -0 "$GATEWAY_PID" >/dev/null 2>&1; then
          echo "Gateway exited before proof-test commands could run." >&2
          exit 1
        fi
        python3 $APP_ROOT/scripts/pesetech_mqtt_discovery.py --config "$CONFIG_PATH" --require-retained --discovery-timeout 30
        python3 $APP_ROOT/scripts/pesetech_ha_service_smoke.py \\
          --url "$PESETECH_HA_URL" \\
          --entity-id "$PESETECH_HA_ENTITY_ID" \\
          --check-entity \\
          --entity-timeout 30 \\
          --candidate-search "${PESETECH_HA_ENTITY_ID#light.}"
        python3 $APP_ROOT/scripts/pesetech_mqtt_smoke.py \\
          --config "$CONFIG_PATH" \\
          --proof-log "$PROOF_LOG" \\
          --run-id "$PROOF_RUN_ID" \\
          --wait-state \\
          --precondition-visible-start \\
          --delay 2.5
        python3 $APP_ROOT/scripts/pesetech_verify_proof.py "$PROOF_LOG" \\
          --config "$CONFIG_PATH" \\
          --run-id "$PROOF_RUN_ID" \\
          --allow-unobserved
        python3 $APP_ROOT/scripts/pesetech_ha_service_smoke.py \\
          --url "$PESETECH_HA_URL" \\
          --entity-id "$PESETECH_HA_ENTITY_ID" \\
          --proof-log "$HA_PROOF_LOG" \\
          --run-id "$PROOF_RUN_ID" \\
          --precondition-visible-start \\
          --wait-state \\
          --wait-attributes \\
          --wait-mqtt-state \\
          --wait-mqtt-attributes \\
          --mqtt-config "$CONFIG_PATH"
        python3 $APP_ROOT/scripts/pesetech_verify_ha_service_proof.py "$HA_PROOF_LOG" \\
          --url "$PESETECH_HA_URL" \\
          --entity-id "$PESETECH_HA_ENTITY_ID" \\
          --run-id "$PROOF_RUN_ID" \\
          --require-attributes \\
          --require-mqtt-state \\
          --require-mqtt-attributes \\
          --allow-unobserved
        python3 $APP_ROOT/scripts/pesetech_real_device_audit.py \\
          --config "$CONFIG_PATH" \\
          --proof-log "$PROOF_LOG" \\
          --ha-proof-log "$HA_PROOF_LOG" \\
          --ha-url "$PESETECH_HA_URL" \\
          --ha-entity-id "$PESETECH_HA_ENTITY_ID" \\
          --proof-run-id "$PROOF_RUN_ID" \\
          --output-json "$FINAL_AUDIT_REPORT" \\
          --allow-unobserved
        copy_report_to_share "$PROOF_LOG" "pesetech-move-test.jsonl"
        copy_report_to_share "$HA_PROOF_LOG" "pesetech-ha-service-proof.jsonl"
        copy_report_to_share "$FINAL_AUDIT_REPORT" "pesetech-final-audit.json"
        echo "Proof-test completed. Logs share run id $PROOF_RUN_ID but contain observed=null entries."
        echo "Keeping the gateway service running after proof-test; stop the app or switch operation if you need another action."
        wait "$GATEWAY_PID"
        ;;
      list)
        if [ ! -s "$CONFIG_PATH" ]; then
          echo "No gateway config found at $CONFIG_PATH. Run provision or import before list." >&2
          exit 2
        fi
        exec python3 gateway.py --basedir "$DATA_DIR" --reload prov list
        ;;
      *)
        echo "Unknown operation: $PESETECH_OPERATION" >&2
        exit 2
        ;;
    esac
    """


def docs():
    return """
    # Pesetech BLE Mesh Gateway

    Home Assistant add-on for controlling Pesetech/Lepu artificial skylights over Bluetooth Mesh.

    ## Supported Controls

    - on/off
    - brightness
    - color temperature
    - transitions for brightness and color temperature

    ## Setup

    Keep the default options and change only `operation`:

    1. `runtime-check`
    2. `mesh-daemon-check`
    3. `cloud-fetch` with a Pesetech cloud token or username/password, or copy mesh JSON to `/share/pesetech_mesh.json`
    4. `import-check`
    5. `import`
    6. `service`

    The light appears in Home Assistant through MQTT discovery after `service` starts.

    ## Notes

    - Home Assistant OS or Supervised, MQTT, and a visible Bluetooth adapter are required.
    - Bluetooth adapter quality matters.
    - Off-with-transition is unreliable on these lights; use brightness/color transitions, then turn off separately.
    """


def readme():
    return """
    # Pesetech BLE Mesh Gateway

    Home Assistant add-on for controlling Pesetech/Lepu artificial skylights over Bluetooth Mesh.

    Supported controls: on/off, brightness, color temperature, and brightness/color transitions.

    Setup path: `runtime-check` -> `mesh-daemon-check` -> `cloud-fetch` or `/share/pesetech_mesh.json` -> `import-check` -> `import` -> `service`.

    Keep defaults unless you are changing `operation` or providing Pesetech cloud credentials/token.
    """


def install_md(slug=DEFAULT_SLUG):
    return f"""
    # Install

    This add-on must run on the Home Assistant host with Bluetooth access to the skylight.

    ## Git Repository Install

    Add the repository URL in the Home Assistant add-on store, install `{slug}`, and start with the default options.

    ## Local `/addons` Install

    Copy only the `{slug}` directory into `/addons/{slug}` on the Home Assistant host, then refresh the add-on store.

    ## Setup

    Change only `operation`:

    1. `runtime-check`
    2. `mesh-daemon-check`
    3. `cloud-fetch` with Pesetech cloud credentials/token, or copy mesh JSON to `/share/pesetech_mesh.json`
    4. `import-check`
    5. `import`
    6. `service`
    """


def repository_readme(slug=DEFAULT_SLUG):
    return f"""
    # Pesetech BLE Mesh Local Home Assistant Repository

    Generated Home Assistant add-on repository for the Pesetech BLE Mesh Gateway.

    Add this repository in the Home Assistant add-on store, install `{slug}`, and start with the default options.

    Setup path: `runtime-check` -> `mesh-daemon-check` -> `cloud-fetch` or `/share/pesetech_mesh.json` -> `import-check` -> `import` -> `service`.
    """


def changelog(version=DEFAULT_VERSION):
    return f"""
    # Changelog

    ## {version}

    - Initial experimental local Home Assistant app/add-on wrapper for the Pesetech BLE Mesh gateway.
    - Add a runtime-check operation for verifying the bundled Python Bluetooth Mesh APIs, including Light CTL set arguments, before touching hardware state; it writes `/data/pesetech-runtime-check.json`.
    - Add a mesh-daemon-check operation that fails clearly when no `hci*` adapter is visible, then starts D-Bus and `bluetooth-meshd` without provisioning, MQTT, or light-control commands.
    - Add a ble-scan operation that drives BlueZ `btmgmt` through a pseudo-terminal to list nearby BLE advertisements without starting D-Bus, Bluetooth Mesh, MQTT, provisioning, import, or light-control commands.
    - Add a status operation that prints a read-only next-step summary and writes `/data/pesetech-status.json`.
    - Add a preflight operation for checking persisted gateway config, store files, Bluetooth host readiness, and MQTT broker reachability from Home Assistant app logs, plus a `/data/pesetech-preflight.json` preflight report for status and diagnostics.
    - Add a cloud-fetch operation that writes `/share/pesetech_mesh.json` from Pesetech cloud token or credentials before import, including direct add-on config credentials for no-SSH installs.
    - Add a Home Assistant `/api/hassio` install helper for token-based no-SSH repository install and first safe operation startup.
    - Add a Home Assistant `/api/hassio` operation/log helper for token-based no-SSH setup gates after install.
    - Add a guarded Home Assistant `/api/hassio` gate sequence helper that defaults to no-motion setup through readiness-test.
    - Verify token-driven sequence gates from the latest add-on log pass marker, so no-motion setup stops on a missing runtime/import/preflight/readiness success signal.
    - Add a one-command token install helper that serves the generated add-on repository only while Home Assistant installs it, verifies the first runtime-check log gate, and then stops the temporary server.
    - Add an import-check operation for validating Telink/Pesetech mesh.json before writing add-on data.
    - Add an experimental Telink/Pesetech mesh.json import operation for already-provisioned skylights.
    - Add a readiness-test operation that starts the gateway and verifies MQTT discovery plus the Home Assistant entity without sending light-control commands.
    - Add a move-test operation that starts the gateway and publishes the on/brightness/warm/cool/off sequence from Home Assistant app logs.
    - Add a ha-api-check operation that verifies Home Assistant Core API access before starting Bluetooth Mesh.
    - Add a ha-service-test operation that calls Home Assistant light services through the app Core API proxy and verifies Home Assistant plus MQTT bridge state.
    - Add a proof-test operation that runs MQTT and Home Assistant service proofs under one shared run id, then runs a non-interactive final audit.
    - Add a diagnostics operation that writes a redacted troubleshooting bundle to the Home Assistant share.
    """


def repository_yaml():
    return """
    name: Pesetech BLE Mesh Local Apps
    url: https://community.home-assistant.io/t/pesetech-artificial-skylight-my-first-attempt-at-creating-an-integration/579060
    maintainer: Local Pesetech test build
    """


def make_addon(root, output, slug=DEFAULT_SLUG, version=DEFAULT_VERSION, image=None):
    root = Path(root).resolve()
    output = Path(output).resolve()
    addon_dir = output / slug
    source_dir = addon_dir / "source"

    output.mkdir(parents=True, exist_ok=True)
    if addon_dir.exists():
        shutil.rmtree(addon_dir)
    addon_dir.mkdir(parents=True)

    write_text(output / "repository.yaml", repository_yaml())
    write_text(output / "README.md", repository_readme(slug))
    write_text(output / "INSTALL.md", install_md(slug))
    write_text(addon_dir / "config.yaml", addon_config(slug, version, image))
    write_text(addon_dir / "Dockerfile", dockerfile(version))
    write_text(addon_dir / "run.sh", run_script())
    write_text(addon_dir / "README.md", readme())
    write_text(addon_dir / "DOCS.md", docs())
    write_text(addon_dir / "CHANGELOG.md", changelog(version))
    copy_source(root, source_dir, output_root=output)

    return addon_dir


def make_archive(repository_dir, archive_path):
    repository_dir = Path(repository_dir).resolve()
    archive_path = Path(archive_path).resolve()
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive_path, "w:gz") as archive:
        for path in sorted(repository_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.resolve() == archive_path:
                continue
            archive.add(path, arcname=path.relative_to(repository_dir).as_posix())

    return archive_path


def make_local_archive(repository_dir, archive_path, slug=DEFAULT_SLUG):
    repository_dir = Path(repository_dir).resolve()
    addon_dir = repository_dir / slug
    if not addon_dir.is_dir():
        raise FileNotFoundError(f"{addon_dir} does not exist; generate the add-on repository first.")

    archive_path = Path(archive_path).resolve()
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive_path, "w:gz") as archive:
        for path in sorted(addon_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.resolve() == archive_path:
                continue
            archive.add(path, arcname=(Path(slug) / path.relative_to(addon_dir)).as_posix())

    return archive_path


def main():
    parser = argparse.ArgumentParser(description="Create a self-contained local Home Assistant app/add-on folder.")
    parser.add_argument("--root", default=".", help="Repository checkout path.")
    parser.add_argument("--output", default="pesetech-ha-addon", help="Output app repository folder.")
    parser.add_argument("--archive-output", default=None, help="Optional output .tar.gz for the generated app repository.")
    parser.add_argument("--local-archive-output", default=None, help="Optional output .tar.gz containing only the app folder for direct /addons install.")
    parser.add_argument("--slug", default=DEFAULT_SLUG, help="App/add-on slug.")
    parser.add_argument("--version", default=DEFAULT_VERSION, help="App/add-on version.")
    parser.add_argument("--image", default=None, help="Optional prebuilt image repository for Home Assistant config.yaml; a trailing tag is stripped because Supervisor uses version as the tag.")
    args = parser.parse_args()

    addon_dir = make_addon(args.root, args.output, args.slug, args.version, args.image)
    print(f"Wrote {addon_dir}")
    if args.archive_output:
        archive = make_archive(args.output, args.archive_output)
        print(f"Wrote {archive}")
    if args.local_archive_output:
        archive = make_local_archive(args.output, args.local_archive_output, args.slug)
        print(f"Wrote {archive}")


if __name__ == "__main__":
    main()
