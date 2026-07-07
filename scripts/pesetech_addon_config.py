#!/usr/bin/env python3
import argparse
import json
import os
import shlex
import sys
import urllib.error
import urllib.request
from pathlib import Path
from uuid import UUID


DEFAULTS = {
    "operation": "runtime-check",
    "mqtt_from_supervisor": True,
    "mqtt_broker": "",
    "mqtt_port": 1883,
    "mqtt_username": "",
    "mqtt_password": "",
    "discovery_prefix": "homeassistant",
    "node_id": "mqtt_mesh",
    "device_id": "skylight",
    "skylight_name": "Pesetech Skylight",
    "skylight_uuid": "",
    "mesh_json_path": "/share/pesetech_mesh.json",
    "import_mesh_candidate": 0,
    "import_node_uuid": "",
    "import_node_unicast": "",
    "import_local_address": "",
    "import_force": False,
    "cloud_region": "europe",
    "cloud_base_url": "",
    "cloud_token": "",
    "cloud_username": "",
    "cloud_password": "",
    "cloud_token_path": "/share/pesetech_cloud_token.txt",
    "cloud_username_path": "/share/pesetech_cloud_username.txt",
    "cloud_password_path": "/share/pesetech_cloud_password.txt",
    "cloud_output_path": "/share/pesetech_mesh.json",
    "cloud_raw_output_path": "",
    "cloud_report_path": "/share/pesetech_cloud_fetch_report.json",
    "cloud_candidate": 0,
    "cloud_home_id": "",
    "mesh_io": "",
    "mesh_debug": False,
    "mesh_startup_timeout": 5,
    "mesh_adapter_power_off": False,
    "mesh_adapter_power_off_delay": 1,
    "mesh_scan_seconds": 10,
    "mesh_scan_repeat": 1,
    "ble_scan_seconds": 20,
    "ha_url": "http://supervisor/core",
    "ha_entity_id": "light.skylight",
    "relay": False,
    "dev_source_path": "",
    "dev_source_archive_url": "",
    "raw_command": "raw",
    "raw_node": "",
    "raw_opcode": "",
    "raw_payload": "",
    "raw_brightness": 32768,
    "raw_address": "",
    "raw_address_model": "lightness",
    "raw_retransmissions": 10,
    "raw_send_interval_ms": 75,
    "raw_timeout": 20,
    "raw_read_after": True,
    "skylight_programs_enabled": False,
    "skylight_programs_path": "/share/pesetech_skylight_programs.json",
    "skylight_programs_dry_run": True,
    "diagnostic_monitor_enabled": False,
    "diagnostic_monitor_path": "/share/pesetech-command-monitor.jsonl",
    "diagnostic_monitor_summary_interval_seconds": 60,
    "diagnostic_export_enabled": False,
    "diagnostic_export_port": 8766,
    "diagnostic_export_tail_bytes": 1048576,
    "btmon_monitor_enabled": False,
    "btmon_monitor_adapter": "",
    "btmon_monitor_raw_path": "/share/pesetech-btmon.log",
    "btmon_monitor_events_path": "/share/pesetech-btmon-events.jsonl",
    "btmon_monitor_summary_path": "/share/pesetech-btmon-summary.jsonl",
    "btmon_monitor_summary_interval_seconds": 60,
    "btmon_monitor_max_bytes": 26214400,
    "btmon_monitor_max_files": 3,
    "btmon_monitor_events_max_bytes": 5242880,
    "btmon_monitor_events_max_files": 3,
    "btmon_monitor_summary_max_bytes": 5242880,
    "btmon_monitor_summary_max_files": 3,
}

DEFAULT_OPERATION_OVERRIDE_PATH = "/share/pesetech_next_operation.json"

SAFE_OPERATION_OVERRIDE_KEYS = {
    "operation",
    "discovery_prefix",
    "node_id",
    "device_id",
    "skylight_name",
    "skylight_uuid",
    "mesh_json_path",
    "import_mesh_candidate",
    "import_node_uuid",
    "import_node_unicast",
    "import_local_address",
    "import_force",
    "cloud_region",
    "cloud_base_url",
    "cloud_output_path",
    "cloud_raw_output_path",
    "cloud_report_path",
    "cloud_candidate",
    "cloud_home_id",
    "mesh_io",
    "mesh_debug",
    "mesh_startup_timeout",
    "mesh_adapter_power_off",
    "mesh_adapter_power_off_delay",
    "mesh_scan_seconds",
    "mesh_scan_repeat",
    "ble_scan_seconds",
    "ha_url",
    "ha_entity_id",
    "relay",
    "dev_source_path",
    "dev_source_archive_url",
    "raw_command",
    "raw_node",
    "raw_opcode",
    "raw_payload",
    "raw_brightness",
    "raw_address",
    "raw_address_model",
    "raw_retransmissions",
    "raw_send_interval_ms",
    "raw_timeout",
    "raw_read_after",
    "skylight_programs_enabled",
    "skylight_programs_path",
    "skylight_programs_dry_run",
    "diagnostic_monitor_enabled",
    "diagnostic_monitor_path",
    "diagnostic_monitor_summary_interval_seconds",
    "diagnostic_export_enabled",
    "diagnostic_export_port",
    "diagnostic_export_tail_bytes",
    "btmon_monitor_enabled",
    "btmon_monitor_adapter",
    "btmon_monitor_raw_path",
    "btmon_monitor_events_path",
    "btmon_monitor_summary_path",
    "btmon_monitor_summary_interval_seconds",
    "btmon_monitor_max_bytes",
    "btmon_monitor_max_files",
    "btmon_monitor_events_max_bytes",
    "btmon_monitor_events_max_files",
    "btmon_monitor_summary_max_bytes",
    "btmon_monitor_summary_max_files",
}

SENSITIVE_OPERATION_OVERRIDE_KEYS = {
    "mqtt_broker",
    "mqtt_port",
    "mqtt_username",
    "mqtt_password",
    "mqtt_from_supervisor",
    "cloud_token",
    "cloud_username",
    "cloud_password",
    "cloud_token_path",
    "cloud_username_path",
    "cloud_password_path",
}

OPERATIONS = {
    "service",
    "ble-scan",
    "runtime-check",
    "mesh-daemon-check",
    "preflight",
    "scan",
    "provision",
    "configure",
    "cloud-fetch",
    "import-check",
    "import",
    "readiness-test",
    "read-state",
    "model-scope",
    "raw-command",
    "skylight-programs",
    "move-test",
    "ha-api-check",
    "ha-service-test",
    "proof-test",
    "diagnostics",
    "status",
    "list",
}
CONFIG_OPERATIONS = {"service", "scan", "provision", "configure", "import"}
UUID_OPERATIONS = {"provision", "configure"}
IMPORT_OPERATIONS = {"import", "import-check"}
CLOUD_OPERATIONS = {"cloud-fetch"}
HA_TARGET_OPERATIONS = {"ha-api-check", "readiness-test", "ha-service-test", "proof-test"}
PERSISTED_CONFIG_OPERATIONS = {
    "service",
    "preflight",
    "readiness-test",
    "read-state",
    "model-scope",
    "raw-command",
    "skylight-programs",
    "move-test",
    "ha-service-test",
    "proof-test",
    "diagnostics",
    "list",
}
DEFAULT_SUPERVISOR_URL = "http://supervisor"
VALID_CLOUD_REGIONS = {"asia", "europe"}
RAW_COMMANDS = {"raw", "pesetech-brightness"}
RAW_ADDRESS_MODELS = {"unicast", "onoff", "lightness", "ctl", "ctl-temperature"}


def load_options(path):
    options_path = Path(path)
    if not options_path.exists():
        return DEFAULTS.copy()

    with options_path.open("r", encoding="utf-8") as options_file:
        loaded = json.load(options_file)

    if not isinstance(loaded, dict):
        raise ValueError("options.json must contain a JSON object")

    options = DEFAULTS.copy()
    options.update({key: value for key, value in loaded.items() if value is not None})
    return options


def apply_operation_override(options, path):
    if not path:
        return options

    override_path = Path(path)
    if not override_path.exists() or override_path.stat().st_size == 0:
        return options

    with override_path.open("r", encoding="utf-8") as override_file:
        override = json.load(override_file)

    if not isinstance(override, dict):
        raise ValueError("operation override must contain a JSON object")

    sensitive_keys = sorted(set(override) & SENSITIVE_OPERATION_OVERRIDE_KEYS)
    if sensitive_keys:
        raise ValueError(
            "operation override may not contain secret or connection keys: "
            + ", ".join(sensitive_keys)
            + ". Set those in the Home Assistant add-on configuration instead."
        )

    unknown_keys = sorted(set(override) - SAFE_OPERATION_OVERRIDE_KEYS)
    if unknown_keys:
        raise ValueError(
            "operation override contains unsupported keys: "
            + ", ".join(unknown_keys)
            + ". Supported keys are: "
            + ", ".join(sorted(SAFE_OPERATION_OVERRIDE_KEYS))
        )

    resolved = options.copy()
    resolved.update({key: value for key, value in override.items() if value is not None})
    return resolved


def clean_string(value):
    if value is None:
        return ""
    return str(value).strip()


def normalized_uuid(value):
    value = clean_string(value)
    if not value:
        return ""
    return str(UUID(value))


def validate_int_range(options, errors, key, label, minimum, maximum):
    try:
        value = int(options.get(key, DEFAULTS[key]))
    except (TypeError, ValueError):
        errors.append(f"{label} must be an integer between {minimum} and {maximum}.")
        return None
    if not minimum <= value <= maximum:
        errors.append(f"{label} must be an integer between {minimum} and {maximum}.")
    return value


def validate_hex_string(options, errors, key, label, required=False):
    value = clean_string(options.get(key))
    if not value:
        if required:
            errors.append(f"{label} is required.")
        return
    compact = "".join(char for char in value if char not in " _:-")
    if len(compact) % 2 or any(char not in "0123456789abcdefABCDEF" for char in compact):
        errors.append(f"{label} must be hex bytes.")


def validate_options(options):
    errors = []
    operation = clean_string(options.get("operation")) or DEFAULTS["operation"]
    service_uuid = operation == "service" and clean_string(options.get("skylight_uuid"))
    render_gateway_config = operation in CONFIG_OPERATIONS and (operation != "service" or service_uuid)
    if operation not in OPERATIONS:
        errors.append(f"operation must be one of {', '.join(sorted(OPERATIONS))}.")

    if render_gateway_config and not clean_string(options.get("mqtt_broker")) and not bool(options.get("mqtt_from_supervisor", True)):
        errors.append("mqtt_broker must be set to your Home Assistant MQTT broker host.")
    if render_gateway_config:
        try:
            mqtt_port = int(options.get("mqtt_port", DEFAULTS["mqtt_port"]))
        except (TypeError, ValueError):
            errors.append("mqtt_port must be an integer between 1 and 65535.")
        else:
            if not 1 <= mqtt_port <= 65535:
                errors.append("mqtt_port must be an integer between 1 and 65535.")

    if operation in UUID_OPERATIONS or service_uuid:
        try:
            normalized_uuid(options.get("skylight_uuid"))
        except ValueError:
            errors.append("skylight_uuid must be a valid UUID from the scan result.")
        if operation in UUID_OPERATIONS and not clean_string(options.get("skylight_uuid")):
            errors.append(f"skylight_uuid is required for {operation}.")

    if operation in IMPORT_OPERATIONS:
        if not clean_string(options.get("mesh_json_path")):
            errors.append(f"mesh_json_path is required for {operation}.")
        try:
            import_mesh_candidate = int(options.get("import_mesh_candidate", DEFAULTS["import_mesh_candidate"]))
        except (TypeError, ValueError):
            errors.append("import_mesh_candidate must be 0 or a positive integer.")
        else:
            if import_mesh_candidate < 0:
                errors.append("import_mesh_candidate must be 0 or a positive integer.")
        try:
            normalized_uuid(options.get("import_node_uuid"))
        except ValueError:
            errors.append("import_node_uuid must be blank or a valid UUID from mesh.json.")

    if operation in CLOUD_OPERATIONS:
        cloud_region = (clean_string(options.get("cloud_region")) or DEFAULTS["cloud_region"]).lower()
        if cloud_region not in VALID_CLOUD_REGIONS:
            errors.append("cloud_region must be one of asia, europe.")
        if not clean_string(options.get("cloud_output_path")):
            errors.append(f"cloud_output_path is required for {operation}.")
        cloud_username = clean_string(options.get("cloud_username"))
        cloud_password = clean_string(options.get("cloud_password"))
        if bool(cloud_username) != bool(cloud_password):
            errors.append("cloud_username and cloud_password must be set together.")
        try:
            cloud_candidate = int(options.get("cloud_candidate", DEFAULTS["cloud_candidate"]))
        except (TypeError, ValueError):
            errors.append("cloud_candidate must be 0 or a positive integer.")
        else:
            if cloud_candidate < 0:
                errors.append("cloud_candidate must be 0 or a positive integer.")
        if "\n" in clean_string(options.get("cloud_home_id")):
            errors.append("cloud_home_id must be blank or a single Pesetech homeId value.")

    mesh_io = clean_string(options.get("mesh_io"))
    if "\n" in mesh_io:
        errors.append("mesh_io must be blank or a single bluetooth-meshd --io value, for example generic:hci0.")
    dev_source_path = clean_string(options.get("dev_source_path"))
    if "\n" in dev_source_path:
        errors.append("dev_source_path must be blank or a single path.")
    dev_source_archive_url = clean_string(options.get("dev_source_archive_url"))
    if "\n" in dev_source_archive_url:
        errors.append("dev_source_archive_url must be blank or a single URL.")
    try:
        mesh_startup_timeout = int(options.get("mesh_startup_timeout", DEFAULTS["mesh_startup_timeout"]))
    except (TypeError, ValueError):
        errors.append("mesh_startup_timeout must be an integer between 1 and 60.")
    else:
        if not 1 <= mesh_startup_timeout <= 60:
            errors.append("mesh_startup_timeout must be an integer between 1 and 60.")
    try:
        power_off_delay = int(options.get("mesh_adapter_power_off_delay", DEFAULTS["mesh_adapter_power_off_delay"]))
    except (TypeError, ValueError):
        errors.append("mesh_adapter_power_off_delay must be an integer between 0 and 10.")
    else:
        if not 0 <= power_off_delay <= 10:
            errors.append("mesh_adapter_power_off_delay must be an integer between 0 and 10.")
    try:
        mesh_scan_seconds = int(options.get("mesh_scan_seconds", DEFAULTS["mesh_scan_seconds"]))
    except (TypeError, ValueError):
        errors.append("mesh_scan_seconds must be an integer between 5 and 300.")
    else:
        if not 5 <= mesh_scan_seconds <= 300:
            errors.append("mesh_scan_seconds must be an integer between 5 and 300.")
    try:
        mesh_scan_repeat = int(options.get("mesh_scan_repeat", DEFAULTS["mesh_scan_repeat"]))
    except (TypeError, ValueError):
        errors.append("mesh_scan_repeat must be an integer between 1 and 60.")
    else:
        if not 1 <= mesh_scan_repeat <= 60:
            errors.append("mesh_scan_repeat must be an integer between 1 and 60.")
    try:
        ble_scan_seconds = int(options.get("ble_scan_seconds", DEFAULTS["ble_scan_seconds"]))
    except (TypeError, ValueError):
        errors.append("ble_scan_seconds must be an integer between 5 and 120.")
    else:
        if not 5 <= ble_scan_seconds <= 120:
            errors.append("ble_scan_seconds must be an integer between 5 and 120.")

    if operation in HA_TARGET_OPERATIONS:
        if not clean_string(options.get("ha_url")):
            errors.append(f"ha_url must be set for {operation}.")
        if not clean_string(options.get("ha_entity_id")):
            errors.append(f"ha_entity_id must be set for {operation}.")

    if operation == "raw-command":
        raw_command = clean_string(options.get("raw_command")) or DEFAULTS["raw_command"]
        if raw_command not in RAW_COMMANDS:
            errors.append(f"raw_command must be one of {', '.join(sorted(RAW_COMMANDS))}.")
        raw_address_model = clean_string(options.get("raw_address_model")) or DEFAULTS["raw_address_model"]
        if raw_address_model not in RAW_ADDRESS_MODELS:
            errors.append(f"raw_address_model must be one of {', '.join(sorted(RAW_ADDRESS_MODELS))}.")
        validate_int_range(options, errors, "raw_brightness", "raw_brightness", 0, 65535)
        validate_int_range(options, errors, "raw_retransmissions", "raw_retransmissions", 1, 20)
        validate_int_range(options, errors, "raw_send_interval_ms", "raw_send_interval_ms", 0, 5000)
        validate_int_range(options, errors, "raw_timeout", "raw_timeout", 1, 120)
        if "\n" in clean_string(options.get("raw_node")):
            errors.append("raw_node must be blank or a single node selector.")
        if "\n" in clean_string(options.get("raw_address")):
            errors.append("raw_address must be blank or a single address.")
        validate_hex_string(options, errors, "raw_payload", "raw_payload")
        if raw_command == "raw":
            raw_opcode = clean_string(options.get("raw_opcode"))
            if not raw_opcode:
                errors.append("raw_opcode is required when raw_command is raw.")
            else:
                compact_opcode = raw_opcode[2:] if raw_opcode.lower().startswith("0x") else raw_opcode
                if any(char not in "0123456789abcdefABCDEF" for char in compact_opcode):
                    errors.append("raw_opcode must be hex or decimal digits.")

    if operation == "skylight-programs":
        if "\n" in clean_string(options.get("skylight_programs_path")):
            errors.append("skylight_programs_path must be blank or a single path.")

    if "\n" in clean_string(options.get("diagnostic_monitor_path")):
        errors.append("diagnostic_monitor_path must be blank or a single path.")
    validate_int_range(options, errors, "diagnostic_export_port", "diagnostic_export_port", 1, 65535)
    validate_int_range(
        options,
        errors,
        "diagnostic_export_tail_bytes",
        "diagnostic_export_tail_bytes",
        1024,
        104857600,
    )
    if "\n" in clean_string(options.get("btmon_monitor_adapter")):
        errors.append("btmon_monitor_adapter must be blank or a single adapter, for example hci0.")
    if "\n" in clean_string(options.get("btmon_monitor_raw_path")):
        errors.append("btmon_monitor_raw_path must be blank or a single path.")
    if "\n" in clean_string(options.get("btmon_monitor_events_path")):
        errors.append("btmon_monitor_events_path must be blank or a single path.")
    if "\n" in clean_string(options.get("btmon_monitor_summary_path")):
        errors.append("btmon_monitor_summary_path must be blank or a single path.")
    validate_int_range(
        options,
        errors,
        "diagnostic_monitor_summary_interval_seconds",
        "diagnostic_monitor_summary_interval_seconds",
        5,
        3600,
    )
    validate_int_range(
        options,
        errors,
        "btmon_monitor_summary_interval_seconds",
        "btmon_monitor_summary_interval_seconds",
        5,
        3600,
    )
    validate_int_range(
        options,
        errors,
        "btmon_monitor_max_bytes",
        "btmon_monitor_max_bytes",
        1048576,
        1073741824,
    )
    validate_int_range(options, errors, "btmon_monitor_max_files", "btmon_monitor_max_files", 1, 20)
    validate_int_range(
        options,
        errors,
        "btmon_monitor_events_max_bytes",
        "btmon_monitor_events_max_bytes",
        1048576,
        1073741824,
    )
    validate_int_range(options, errors, "btmon_monitor_events_max_files", "btmon_monitor_events_max_files", 1, 20)
    validate_int_range(
        options,
        errors,
        "btmon_monitor_summary_max_bytes",
        "btmon_monitor_summary_max_bytes",
        1048576,
        1073741824,
    )
    validate_int_range(
        options,
        errors,
        "btmon_monitor_summary_max_files",
        "btmon_monitor_summary_max_files",
        1,
        20,
    )

    ha_entity_id = clean_string(options.get("ha_entity_id"))
    entity_id_operation = render_gateway_config or operation in HA_TARGET_OPERATIONS or operation in IMPORT_OPERATIONS
    if entity_id_operation and ha_entity_id and not ha_entity_id.startswith("light."):
        errors.append("ha_entity_id must be a Home Assistant light entity id, for example light.skylight.")

    if render_gateway_config:
        if not clean_string(options.get("device_id")):
            errors.append("device_id must be set.")
        if not clean_string(options.get("node_id")):
            errors.append("node_id must be set.")
        if not clean_string(options.get("discovery_prefix")):
            errors.append("discovery_prefix must be set.")

    return errors


def gateway_config(options):
    mqtt = {
        "broker": clean_string(options.get("mqtt_broker")),
        "port": int(options.get("mqtt_port", DEFAULTS["mqtt_port"])),
        "discovery_prefix": clean_string(options.get("discovery_prefix")) or DEFAULTS["discovery_prefix"],
        "node_id": clean_string(options.get("node_id")) or DEFAULTS["node_id"],
    }
    username = clean_string(options.get("mqtt_username"))
    password = clean_string(options.get("mqtt_password"))
    if username:
        mqtt["username"] = username
    if password:
        mqtt["password"] = password

    mesh = {}
    uuid = normalized_uuid(options.get("skylight_uuid"))
    if uuid:
        mesh[clean_string(options.get("device_id")) or DEFAULTS["device_id"]] = {
            "uuid": uuid,
            "name": clean_string(options.get("skylight_name")) or DEFAULTS["skylight_name"],
            "default_entity_id": clean_string(options.get("ha_entity_id")) or DEFAULTS["ha_entity_id"],
            "type": "pesetech_skylight",
            "relay": bool(options.get("relay", False)),
        }

    return {
        "mqtt": mqtt,
        "mesh": mesh,
        "skylight_programs_enabled": bool(options.get("skylight_programs_enabled", False)),
        "diagnostic_monitor": diagnostic_monitor_config(options),
        "diagnostic_export": diagnostic_export_config(options),
        "btmon_monitor": btmon_monitor_config(options),
    }


def unwrap_supervisor_response(payload):
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload


def fetch_supervisor_mqtt_service(base_url=DEFAULT_SUPERVISOR_URL, token=None, timeout=10.0):
    token = token or os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise ValueError(
            "mqtt_from_supervisor is enabled but SUPERVISOR_TOKEN is missing; "
            "run inside Home Assistant or set mqtt_broker manually."
        )

    url = clean_string(base_url or DEFAULT_SUPERVISOR_URL).rstrip("/") + "/services/mqtt"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Could not read Home Assistant MQTT service from Supervisor ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Could not reach Home Assistant Supervisor MQTT service API: {exc.reason}") from exc

    service = unwrap_supervisor_response(payload)
    if not isinstance(service, dict):
        raise ValueError("Supervisor MQTT service response was not a JSON object.")
    return service


def apply_supervisor_mqtt_service(options, service):
    service = unwrap_supervisor_response(service)
    if not isinstance(service, dict):
        raise ValueError("Supervisor MQTT service response was not a JSON object.")
    if bool(service.get("ssl")):
        raise ValueError(
            "Supervisor MQTT service reports ssl=true; the Pesetech gateway currently supports plain MQTT only. "
            "Set mqtt_from_supervisor to false and fill in a non-TLS MQTT listener manually."
        )

    host = clean_string(service.get("host"))
    if not host:
        raise ValueError("Supervisor MQTT service did not include a host.")
    try:
        port = int(service.get("port") or DEFAULTS["mqtt_port"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Supervisor MQTT service port was not an integer.") from exc
    if not 1 <= port <= 65535:
        raise ValueError("Supervisor MQTT service port must be between 1 and 65535.")

    resolved = options.copy()
    resolved["mqtt_broker"] = host
    resolved["mqtt_port"] = port
    resolved["mqtt_username"] = clean_string(service.get("username"))
    resolved["mqtt_password"] = clean_string(service.get("password"))
    resolved["_mqtt_source"] = "supervisor"
    return resolved


def write_gateway_config(config, path):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        output_file.write(dump_simple_yaml(config))


def _line_indent(line):
    return len(line) - len(line.lstrip(" "))


def is_placeholder(value):
    value = clean_string(value).lower()
    return value in {
        "<home_assistant_mqtt_host>",
        "home_assistant_mqtt_host",
        "your_mqtt_host",
        "your_home_assistant_mqtt_host",
    }


def import_config_needs_seed(path):
    config_path = Path(path)
    if not config_path.exists() or config_path.stat().st_size == 0:
        return True

    mqtt_indent = None
    for raw_line in config_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        indent = _line_indent(line)
        if stripped == "mqtt:":
            mqtt_indent = indent
            continue

        if mqtt_indent is not None and indent <= mqtt_indent:
            return False

        if mqtt_indent is not None and stripped.startswith("broker:"):
            value = clean_string(stripped.split(":", 1)[1]).strip('"').strip("'")
            return not value or is_placeholder(value)

    return False


def should_write_gateway_config(options, output_path=None):
    operation = clean_string(options.get("operation")) or DEFAULTS["operation"]
    if operation == "service" and not clean_string(options.get("skylight_uuid")):
        return False
    if operation == "import":
        if bool(options.get("import_force", False)):
            return True
        if output_path is None:
            return False
        return import_config_needs_seed(output_path)
    return operation not in {
        "import",
        "import-check",
        "cloud-fetch",
        "runtime-check",
        "mesh-daemon-check",
        "ble-scan",
        "status",
        "preflight",
        "readiness-test",
        "read-state",
        "model-scope",
        "raw-command",
        "skylight-programs",
        "move-test",
        "ha-api-check",
        "ha-service-test",
        "proof-test",
        "diagnostics",
        "list",
    }


def should_resolve_supervisor_mqtt(options, output_path=None):
    if not bool(options.get("mqtt_from_supervisor", True)):
        return False
    if clean_string(options.get("mqtt_broker")):
        return False
    return should_write_gateway_config(options, output_path)


def persisted_config_exists(path):
    if path is None:
        return False
    config_path = Path(path)
    return config_path.exists() and config_path.is_file() and config_path.stat().st_size > 0


def mqtt_config_source(options, output_path=None):
    explicit_source = clean_string(options.get("_mqtt_source"))
    if explicit_source:
        return explicit_source
    operation = clean_string(options.get("operation")) or DEFAULTS["operation"]
    if clean_string(options.get("mqtt_broker")):
        return "manual"
    if should_write_gateway_config(options, output_path):
        if bool(options.get("mqtt_from_supervisor", True)):
            return "supervisor_pending"
        return "unset"
    if output_path is not None and operation in PERSISTED_CONFIG_OPERATIONS and persisted_config_exists(output_path):
        return "persisted"
    if operation == "service":
        return "persisted"
    return "none"


def dump_scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if value is None:
        return "null"
    return json.dumps(str(value))


def load_simple_yaml(text):
    root = {}
    stack = [(-1, root)]

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        indent = _line_indent(line)
        stripped = line.strip()
        key, separator, raw_value = stripped.partition(":")
        if not separator or not key:
            raise ValueError(f"unsupported gateway config line: {raw_line!r}")

        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ValueError(f"unsupported gateway config indentation: {raw_line!r}")

        parent = stack[-1][1]
        value = raw_value.strip()
        if not value:
            child = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = load_simple_yaml_scalar(value)

    return root


def load_simple_yaml_scalar(value):
    if value == "{}":
        return {}
    if value == "true":
        return True
    if value == "false":
        return False
    if value == "null":
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def dump_simple_yaml(value, indent=0):
    lines = []
    prefix = " " * indent

    for key, item in value.items():
        if isinstance(item, dict):
            if item:
                lines.append(f"{prefix}{key}:")
                lines.append(dump_simple_yaml(item, indent + 2).rstrip())
            else:
                lines.append(f"{prefix}{key}: {{}}")
        else:
            lines.append(f"{prefix}{key}: {dump_scalar(item)}")

    return "\n".join(lines) + "\n"


def diagnostic_monitor_enabled(options):
    if bool(options.get("diagnostic_monitor_enabled", False)):
        return True
    return raw_payload_has_marker(options, "diagnostic-monitor-enabled")


def raw_payload_has_marker(options, marker):
    raw = clean_string(options.get("raw_payload"))
    return marker in raw.replace(",", " ").replace(";", " ").split()


def diagnostic_monitor_config(options):
    return {
        "enabled": diagnostic_monitor_enabled(options),
        "path": clean_string(options.get("diagnostic_monitor_path")) or DEFAULTS["diagnostic_monitor_path"],
        "summary_interval_seconds": int(
            options.get(
                "diagnostic_monitor_summary_interval_seconds",
                DEFAULTS["diagnostic_monitor_summary_interval_seconds"],
            )
            or DEFAULTS["diagnostic_monitor_summary_interval_seconds"]
        ),
    }


def diagnostic_export_enabled(options):
    if bool(options.get("diagnostic_export_enabled", False)):
        return True
    return raw_payload_has_marker(options, "diagnostic-export-enabled")


def diagnostic_export_config(options):
    return {
        "enabled": diagnostic_export_enabled(options),
        "port": int(options.get("diagnostic_export_port", DEFAULTS["diagnostic_export_port"])),
        "tail_bytes": int(options.get("diagnostic_export_tail_bytes", DEFAULTS["diagnostic_export_tail_bytes"])),
    }


def btmon_monitor_enabled(options):
    if bool(options.get("btmon_monitor_enabled", False)):
        return True
    return raw_payload_has_marker(options, "btmon-monitor-enabled")


def btmon_monitor_config(options):
    return {
        "enabled": btmon_monitor_enabled(options),
        "adapter": clean_string(options.get("btmon_monitor_adapter")),
        "raw_path": clean_string(options.get("btmon_monitor_raw_path")) or DEFAULTS["btmon_monitor_raw_path"],
        "events_path": clean_string(options.get("btmon_monitor_events_path")) or DEFAULTS["btmon_monitor_events_path"],
        "summary_path": clean_string(options.get("btmon_monitor_summary_path"))
        or DEFAULTS["btmon_monitor_summary_path"],
        "summary_interval_seconds": int(
            options.get(
                "btmon_monitor_summary_interval_seconds",
                DEFAULTS["btmon_monitor_summary_interval_seconds"],
            )
            or DEFAULTS["btmon_monitor_summary_interval_seconds"]
        ),
        "max_bytes": int(options.get("btmon_monitor_max_bytes", DEFAULTS["btmon_monitor_max_bytes"])),
        "max_files": int(options.get("btmon_monitor_max_files", DEFAULTS["btmon_monitor_max_files"])),
        "events_max_bytes": int(
            options.get("btmon_monitor_events_max_bytes", DEFAULTS["btmon_monitor_events_max_bytes"])
        ),
        "events_max_files": int(
            options.get("btmon_monitor_events_max_files", DEFAULTS["btmon_monitor_events_max_files"])
        ),
        "summary_max_bytes": int(
            options.get("btmon_monitor_summary_max_bytes", DEFAULTS["btmon_monitor_summary_max_bytes"])
        ),
        "summary_max_files": int(
            options.get("btmon_monitor_summary_max_files", DEFAULTS["btmon_monitor_summary_max_files"])
        ),
    }


def update_service_runtime_config(options, path):
    operation = clean_string(options.get("operation")) or DEFAULTS["operation"]
    if operation != "service":
        return False

    config_path = Path(path)
    if not config_path.exists() or not config_path.is_file():
        return False

    with config_path.open("r", encoding="utf-8") as config_file:
        config = load_simple_yaml(config_file.read())

    if not isinstance(config, dict):
        raise ValueError(f"{path} must contain a gateway config object")

    if (
        "diagnostic_monitor" not in config
        and not diagnostic_monitor_enabled(options)
        and "diagnostic_export" not in config
        and not diagnostic_export_enabled(options)
        and "btmon_monitor" not in config
        and not btmon_monitor_enabled(options)
    ):
        return False

    config["diagnostic_monitor"] = diagnostic_monitor_config(options)
    config["diagnostic_export"] = diagnostic_export_config(options)
    config["btmon_monitor"] = btmon_monitor_config(options)
    write_gateway_config(config, config_path)
    return True


def shell_exports(options, output_path=None):
    operation = clean_string(options.get("operation")) or DEFAULTS["operation"]
    uuid = normalized_uuid(options.get("skylight_uuid")) if operation in UUID_OPERATIONS or operation == "service" else ""
    import_node_uuid = normalized_uuid(options.get("import_node_uuid")) if operation in IMPORT_OPERATIONS else ""
    cloud_region = (clean_string(options.get("cloud_region")) or DEFAULTS["cloud_region"]).lower()
    mqtt_port = int(options.get("mqtt_port", DEFAULTS["mqtt_port"]) or DEFAULTS["mqtt_port"])
    mesh_startup_timeout = int(
        options.get("mesh_startup_timeout", DEFAULTS["mesh_startup_timeout"])
        or DEFAULTS["mesh_startup_timeout"]
    )
    mesh_adapter_power_off_delay = int(
        options.get("mesh_adapter_power_off_delay", DEFAULTS["mesh_adapter_power_off_delay"])
        or DEFAULTS["mesh_adapter_power_off_delay"]
    )
    mesh_scan_seconds = int(
        options.get("mesh_scan_seconds", DEFAULTS["mesh_scan_seconds"])
        or DEFAULTS["mesh_scan_seconds"]
    )
    mesh_scan_repeat = int(
        options.get("mesh_scan_repeat", DEFAULTS["mesh_scan_repeat"])
        or DEFAULTS["mesh_scan_repeat"]
    )
    ble_scan_seconds = int(options.get("ble_scan_seconds", DEFAULTS["ble_scan_seconds"]) or DEFAULTS["ble_scan_seconds"])
    raw_brightness = int(options.get("raw_brightness", DEFAULTS["raw_brightness"]) or DEFAULTS["raw_brightness"])
    raw_retransmissions = int(
        options.get("raw_retransmissions", DEFAULTS["raw_retransmissions"])
        or DEFAULTS["raw_retransmissions"]
    )
    raw_send_interval_ms = int(
        options.get("raw_send_interval_ms", DEFAULTS["raw_send_interval_ms"])
        or DEFAULTS["raw_send_interval_ms"]
    )
    raw_timeout = int(options.get("raw_timeout", DEFAULTS["raw_timeout"]) or DEFAULTS["raw_timeout"])
    monitor_config = diagnostic_monitor_config(options)
    export_config = diagnostic_export_config(options)
    btmon_config = btmon_monitor_config(options)
    return "\n".join(
        [
            f"PESETECH_OPERATION={shlex.quote(operation)}",
            f"PESETECH_UUID={shlex.quote(uuid)}",
            f"PESETECH_MQTT_BROKER={shlex.quote(clean_string(options.get('mqtt_broker')))}",
            f"PESETECH_MQTT_PORT={shlex.quote(str(mqtt_port))}",
            f"PESETECH_DISCOVERY_PREFIX={shlex.quote(clean_string(options.get('discovery_prefix')) or DEFAULTS['discovery_prefix'])}",
            f"PESETECH_NODE_ID={shlex.quote(clean_string(options.get('node_id')) or DEFAULTS['node_id'])}",
            f"PESETECH_DEVICE_ID={shlex.quote(clean_string(options.get('device_id')) or DEFAULTS['device_id'])}",
            f"PESETECH_DEVICE_NAME={shlex.quote(clean_string(options.get('skylight_name')) or DEFAULTS['skylight_name'])}",
            f"PESETECH_MESH_JSON={shlex.quote(clean_string(options.get('mesh_json_path')) or DEFAULTS['mesh_json_path'])}",
            f"PESETECH_IMPORT_MESH_CANDIDATE={shlex.quote(str(int(options.get('import_mesh_candidate', DEFAULTS['import_mesh_candidate']))))}",
            f"PESETECH_IMPORT_NODE_UUID={shlex.quote(import_node_uuid)}",
            f"PESETECH_IMPORT_NODE_UNICAST={shlex.quote(clean_string(options.get('import_node_unicast')))}",
            f"PESETECH_IMPORT_LOCAL_ADDRESS={shlex.quote(clean_string(options.get('import_local_address')))}",
            f"PESETECH_IMPORT_FORCE={shlex.quote('true' if options.get('import_force', False) else 'false')}",
            f"PESETECH_CLOUD_REGION={shlex.quote(cloud_region)}",
            f"PESETECH_CLOUD_BASE_URL={shlex.quote(clean_string(options.get('cloud_base_url')))}",
            f"PESETECH_CLOUD_TOKEN={shlex.quote(clean_string(options.get('cloud_token')))}",
            f"PESETECH_CLOUD_USERNAME={shlex.quote(clean_string(options.get('cloud_username')))}",
            f"PESETECH_CLOUD_PASSWORD={shlex.quote(clean_string(options.get('cloud_password')))}",
            f"PESETECH_CLOUD_TOKEN_FILE={shlex.quote(clean_string(options.get('cloud_token_path')) or DEFAULTS['cloud_token_path'])}",
            f"PESETECH_CLOUD_USERNAME_FILE={shlex.quote(clean_string(options.get('cloud_username_path')) or DEFAULTS['cloud_username_path'])}",
            f"PESETECH_CLOUD_PASSWORD_FILE={shlex.quote(clean_string(options.get('cloud_password_path')) or DEFAULTS['cloud_password_path'])}",
            f"PESETECH_CLOUD_OUTPUT={shlex.quote(clean_string(options.get('cloud_output_path')) or DEFAULTS['cloud_output_path'])}",
            f"PESETECH_CLOUD_RAW_OUTPUT={shlex.quote(clean_string(options.get('cloud_raw_output_path')))}",
            f"PESETECH_CLOUD_REPORT={shlex.quote(clean_string(options.get('cloud_report_path')) or DEFAULTS['cloud_report_path'])}",
            f"PESETECH_CLOUD_CANDIDATE={shlex.quote(str(int(options.get('cloud_candidate', DEFAULTS['cloud_candidate']))))}",
            f"PESETECH_CLOUD_HOME_ID={shlex.quote(clean_string(options.get('cloud_home_id')))}",
            f"PESETECH_MESH_IO={shlex.quote(clean_string(options.get('mesh_io')))}",
            f"PESETECH_MESH_DEBUG={shlex.quote('true' if options.get('mesh_debug', False) else 'false')}",
            f"PESETECH_MESH_STARTUP_TIMEOUT={shlex.quote(str(mesh_startup_timeout))}",
            f"PESETECH_MESH_ADAPTER_POWER_OFF={shlex.quote('true' if options.get('mesh_adapter_power_off', False) else 'false')}",
            f"PESETECH_MESH_ADAPTER_POWER_OFF_DELAY={shlex.quote(str(mesh_adapter_power_off_delay))}",
            f"PESETECH_MESH_SCAN_SECONDS={shlex.quote(str(mesh_scan_seconds))}",
            f"PESETECH_MESH_SCAN_REPEAT={shlex.quote(str(mesh_scan_repeat))}",
            f"PESETECH_BLE_SCAN_SECONDS={shlex.quote(str(ble_scan_seconds))}",
            f"PESETECH_HA_URL={shlex.quote(clean_string(options.get('ha_url')) or DEFAULTS['ha_url'])}",
            f"PESETECH_HA_ENTITY_ID={shlex.quote(clean_string(options.get('ha_entity_id')) or DEFAULTS['ha_entity_id'])}",
            f"PESETECH_MQTT_SOURCE={shlex.quote(mqtt_config_source(options, output_path))}",
            f"PESETECH_DEV_SOURCE_PATH={shlex.quote(clean_string(options.get('dev_source_path')))}",
            f"PESETECH_DEV_SOURCE_ARCHIVE_URL={shlex.quote(clean_string(options.get('dev_source_archive_url')))}",
            f"PESETECH_RAW_COMMAND={shlex.quote(clean_string(options.get('raw_command')) or DEFAULTS['raw_command'])}",
            f"PESETECH_RAW_NODE={shlex.quote(clean_string(options.get('raw_node')))}",
            f"PESETECH_RAW_OPCODE={shlex.quote(clean_string(options.get('raw_opcode')))}",
            f"PESETECH_RAW_PAYLOAD={shlex.quote(clean_string(options.get('raw_payload')))}",
            f"PESETECH_RAW_BRIGHTNESS={shlex.quote(str(raw_brightness))}",
            f"PESETECH_RAW_ADDRESS={shlex.quote(clean_string(options.get('raw_address')))}",
            f"PESETECH_RAW_ADDRESS_MODEL={shlex.quote(clean_string(options.get('raw_address_model')) or DEFAULTS['raw_address_model'])}",
            f"PESETECH_RAW_RETRANSMISSIONS={shlex.quote(str(raw_retransmissions))}",
            f"PESETECH_RAW_SEND_INTERVAL_MS={shlex.quote(str(raw_send_interval_ms))}",
            f"PESETECH_RAW_TIMEOUT={shlex.quote(str(raw_timeout))}",
            f"PESETECH_RAW_READ_AFTER={shlex.quote('true' if options.get('raw_read_after', True) else 'false')}",
            f"PESETECH_SKYLIGHT_PROGRAMS_PATH={shlex.quote(clean_string(options.get('skylight_programs_path')) or DEFAULTS['skylight_programs_path'])}",
            f"PESETECH_SKYLIGHT_PROGRAMS_DRY_RUN={shlex.quote('true' if options.get('skylight_programs_dry_run', True) else 'false')}",
            f"PESETECH_DIAGNOSTIC_MONITOR_ENABLED={shlex.quote('true' if monitor_config['enabled'] else 'false')}",
            f"PESETECH_DIAGNOSTIC_MONITOR_PATH={shlex.quote(monitor_config['path'])}",
            f"PESETECH_DIAGNOSTIC_MONITOR_SUMMARY_INTERVAL_SECONDS={shlex.quote(str(monitor_config['summary_interval_seconds']))}",
            f"PESETECH_DIAGNOSTIC_EXPORT_ENABLED={shlex.quote('true' if export_config['enabled'] else 'false')}",
            f"PESETECH_DIAGNOSTIC_EXPORT_PORT={shlex.quote(str(export_config['port']))}",
            f"PESETECH_DIAGNOSTIC_EXPORT_TAIL_BYTES={shlex.quote(str(export_config['tail_bytes']))}",
            f"PESETECH_BTMON_MONITOR_ENABLED={shlex.quote('true' if btmon_config['enabled'] else 'false')}",
            f"PESETECH_BTMON_MONITOR_ADAPTER={shlex.quote(btmon_config['adapter'])}",
            f"PESETECH_BTMON_MONITOR_RAW_PATH={shlex.quote(btmon_config['raw_path'])}",
            f"PESETECH_BTMON_MONITOR_EVENTS_PATH={shlex.quote(btmon_config['events_path'])}",
            f"PESETECH_BTMON_MONITOR_SUMMARY_PATH={shlex.quote(btmon_config['summary_path'])}",
            f"PESETECH_BTMON_MONITOR_SUMMARY_INTERVAL_SECONDS={shlex.quote(str(btmon_config['summary_interval_seconds']))}",
        ]
    )


def main():
    parser = argparse.ArgumentParser(description="Render Home Assistant add-on options into gateway config.yaml.")
    parser.add_argument("--options", default="/data/options.json", help="Home Assistant add-on options.json path.")
    parser.add_argument(
        "--override",
        default=os.environ.get("PESETECH_OPERATION_OVERRIDE", DEFAULT_OPERATION_OVERRIDE_PATH),
        help="Optional non-secret operation override JSON path.",
    )
    parser.add_argument("--output", default="/data/config.yaml", help="Gateway config.yaml output path.")
    parser.add_argument("--supervisor-url", default=os.environ.get("SUPERVISOR_URL", DEFAULT_SUPERVISOR_URL))
    parser.add_argument("--shell", action="store_true", help="Print shell exports for run.sh.")
    args = parser.parse_args()

    try:
        options = load_options(args.options)
        overridden = apply_operation_override(options, args.override)
        if overridden != options:
            print(f"Using non-secret add-on operation override from {args.override}.", file=sys.stderr)
            options = overridden
        if should_resolve_supervisor_mqtt(options, args.output):
            options = apply_supervisor_mqtt_service(options, fetch_supervisor_mqtt_service(args.supervisor_url))
            print("Using Home Assistant Supervisor MQTT service credentials.", file=sys.stderr)
        errors = validate_options(options)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 2

        if should_write_gateway_config(options, args.output):
            write_gateway_config(gateway_config(options), args.output)
        elif update_service_runtime_config(options, args.output):
            print(f"Updated persisted gateway diagnostic monitor config in {args.output}.", file=sys.stderr)
        if args.shell:
            print(shell_exports(options, args.output))
    except Exception as exc:
        print(f"Failed to render add-on config: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
