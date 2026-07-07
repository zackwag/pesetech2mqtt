#!/usr/bin/env python3
import argparse
import glob
import os
import shutil
import socket
import subprocess
import sys
from uuid import UUID


PLACEHOLDER_PREFIX = "<"
PLACEHOLDER_SUFFIX = ">"
MQTT_FORBIDDEN_TOPIC_CHARS = {"+", "#"}
EXPECTED_PESETECH_VALUES = {
    "brightness_scale": 65280,
    "min_mireds": 100,
    "max_mireds": 556,
}


def is_placeholder(value):
    return isinstance(value, str) and value.startswith(PLACEHOLDER_PREFIX) and value.endswith(PLACEHOLDER_SUFFIX)


def load_yaml(path):
    try:
        import yaml
    except ImportError:
        with open(path, "r") as config_file:
            return load_simple_yaml(config_file.read())

    with open(path, "r") as config_file:
        return yaml.safe_load(config_file) or {}


def load_simple_yaml(text):
    """
    Small fallback parser for the simple mapping-only gateway config files.
    PyYAML is still preferred when available.
    """
    root = {}
    stack = [(-1, root)]

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip() or line.strip() == "---":
            continue

        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        key, separator, value = content.partition(":")
        if not separator:
            raise ValueError(f"Unsupported YAML line: {raw_line}")

        while stack and indent <= stack[-1][0]:
            stack.pop()

        parent = stack[-1][1]
        value = value.strip()
        if value:
            parent[key] = parse_scalar(value)
            continue

        child = {}
        parent[key] = child
        stack.append((indent, child))

    return root


def parse_scalar(value):
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.lower() in {"null", "none", "~"}:
        return None
    if value == "{}":
        return {}
    if value == "[]":
        return []
    if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
        return int(value)
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def discovery_topic(discovery_prefix, component, mesh_topic, device_id):
    return f"{discovery_prefix}/{component}/{mesh_topic}/{device_id}/config"


def command_topic(discovery_prefix, component, mesh_topic, device_id):
    return f"{discovery_prefix}/{component}/{mesh_topic}/{device_id}/set"


def validate_topic_segment(name, value, errors):
    if not value or is_placeholder(value):
        errors.append(f"{name} must be set to a non-placeholder MQTT topic segment.")
        return
    if any(char in str(value) for char in MQTT_FORBIDDEN_TOPIC_CHARS):
        errors.append(f"{name} must not contain MQTT wildcards '+' or '#'.")
    if "/" in str(value):
        errors.append(f"{name} must be a single MQTT topic segment and must not contain '/'.")


def validate_port(name, value, errors):
    if value is None:
        return
    if is_placeholder(value):
        errors.append(f"{name} is still a placeholder; remove it or set a real port.")
        return

    try:
        port = int(value)
    except (TypeError, ValueError):
        errors.append(f"{name} must be an integer between 1 and 65535.")
        return

    if not 1 <= port <= 65535:
        errors.append(f"{name} must be an integer between 1 and 65535.")


def normalized_uuid(value):
    return str(UUID(str(value)))


def validate_config(config):
    errors = []
    warnings = []
    mqtt = config.get("mqtt") or {}
    mesh = config.get("mesh") or {}

    broker = mqtt.get("broker")
    if not broker or is_placeholder(broker):
        errors.append("mqtt.broker must be set to the Home Assistant MQTT broker host.")

    for credential in ("username", "password"):
        if is_placeholder(mqtt.get(credential)):
            errors.append(f"mqtt.{credential} is still a placeholder; remove it or set a real value.")

    for mqtt_field in ("discovery_prefix", "node_id", "topic"):
        if is_placeholder(mqtt.get(mqtt_field)):
            errors.append(f"mqtt.{mqtt_field} is still a placeholder; remove it or set a real value.")

    validate_port("mqtt.port", mqtt.get("port"), errors)

    mesh_topic = mqtt.get("topic") or mqtt.get("node_id") or "mqtt_mesh"
    validate_topic_segment("mqtt.topic/node_id", mesh_topic, errors)

    if not mesh:
        errors.append("mesh must contain at least one device entry.")

    for device_id, info in mesh.items():
        if is_placeholder(device_id):
            errors.append("mesh contains the sample placeholder device id; replace it with skylight or another id.")
            continue
        validate_topic_segment(f"mesh device id {device_id!r}", device_id, errors)

        uuid = info.get("uuid") if isinstance(info, dict) else None
        if not uuid or is_placeholder(uuid):
            errors.append(f"mesh.{device_id}.uuid must be replaced with a UUID from gateway.py scan.")
        else:
            try:
                normalized_uuid(uuid)
            except ValueError:
                errors.append(f"mesh.{device_id}.uuid is not a valid UUID: {uuid}")

        device_type = info.get("type") if isinstance(info, dict) else None
        if device_type != "pesetech_skylight":
            warnings.append(f"mesh.{device_id}.type is {device_type!r}; expected 'pesetech_skylight' for this test.")
        else:
            default_entity_id = info.get("default_entity_id")
            if default_entity_id and is_placeholder(default_entity_id):
                errors.append(f"mesh.{device_id}.default_entity_id is still a placeholder; remove it or set a light entity id.")
            elif default_entity_id and not str(default_entity_id).startswith("light."):
                errors.append(f"mesh.{device_id}.default_entity_id must be a Home Assistant light entity id, for example light.skylight.")
            for key, expected in EXPECTED_PESETECH_VALUES.items():
                if key in info and info.get(key) != expected:
                    warnings.append(f"mesh.{device_id}.{key} is {info.get(key)!r}; first Pesetech test expects {expected}.")

    return errors, warnings, mesh_topic


def validate_store(config, store):
    warnings = []
    warnings.extend(validate_keychain(store))
    warnings.extend(validate_store_addresses(store))
    configured_uuids = {
        normalized_uuid(info["uuid"]): device_id
        for device_id, info in (config.get("mesh") or {}).items()
        if isinstance(info, dict)
        and info.get("uuid")
        and not is_placeholder(info.get("uuid"))
        and is_valid_uuid(info.get("uuid"))
    }
    stored_nodes = (store.get("nodes") or {}) if isinstance(store, dict) else {}
    if not isinstance(stored_nodes, dict):
        stored_nodes = {}
    remote_nodes = (store.get("remote_nodes") or {}) if isinstance(store, dict) else {}
    if not isinstance(remote_nodes, dict):
        remote_nodes = {}

    for uuid, device_id in configured_uuids.items():
        stored = stored_nodes.get(uuid)
        if stored is None:
            warnings.append(f"mesh.{device_id} is not provisioned yet; this is expected before the add step.")
        elif stored.get("type") != "pesetech_skylight":
            warnings.append(
                f"store node {uuid} is type {stored.get('type')!r}; config should switch it to pesetech_skylight on load."
            )
        elif not stored.get("configured"):
            warnings.append(f"store node {uuid} exists but configured is not true; run the config step.")

        remote = remote_nodes.get(uuid)
        if remote is not None:
            if not is_valid_hex_key(remote.get("device_key")):
                warnings.append(f"remote node {uuid} has an invalid device_key; imported-mesh config operations may fail.")
            if stored is not None:
                remote_unicast = optional_int(remote.get("unicast"))
                stored_unicast = optional_int(stored.get("unicast"))
                remote_count = optional_int(remote.get("count"))
                stored_count = optional_int(stored.get("count"))
                if remote_unicast is None:
                    warnings.append(f"remote node {uuid} has an invalid unicast value.")
                elif stored_unicast is not None and remote_unicast != stored_unicast:
                    warnings.append(f"remote node {uuid} unicast does not match store node unicast.")
                if remote_count is None:
                    warnings.append(f"remote node {uuid} has an invalid count value.")
                elif stored_count is not None and remote_count != stored_count:
                    warnings.append(f"remote node {uuid} count does not match store node count.")

    return warnings


def is_fatal_store_warning(warning):
    fatal_prefixes = (
        "remote_nodes are present but keychain.",
        "keychain.",
        "local.",
        "nodes.",
        "remote_nodes.",
        "remote node ",
    )
    return any(warning.startswith(prefix) for prefix in fatal_prefixes)


def split_store_findings(warnings):
    errors = []
    nonfatal_warnings = []
    for warning in warnings:
        if is_fatal_store_warning(warning):
            errors.append(warning)
        else:
            nonfatal_warnings.append(warning)
    return errors, nonfatal_warnings


def validate_keychain(store):
    warnings = []
    if not isinstance(store, dict):
        return warnings

    keychain = store.get("keychain") or {}
    if not isinstance(keychain, dict):
        keychain = {}

    remote_nodes = store.get("remote_nodes") or {}
    has_remote_nodes = isinstance(remote_nodes, dict) and bool(remote_nodes)
    if has_remote_nodes:
        for key_name in ("network_key", "app_key"):
            if key_name not in keychain:
                warnings.append(
                    f"remote_nodes are present but keychain.{key_name} is missing; "
                    "an imported skylight will not respond to generated replacement keys."
                )

    if not keychain:
        return warnings

    for key_name in ("network_key", "app_key", "device_key"):
        if key_name in keychain and not is_valid_hex_key(keychain.get(key_name)):
            warnings.append(f"keychain.{key_name} must be a 16-byte hex key.")

    indexes = {}
    for key_name in ("network_key_index", "app_key_index", "app_key_bound_net_key_index"):
        if key_name not in keychain:
            continue
        index = optional_key_index(keychain.get(key_name))
        if index is None:
            warnings.append(f"keychain.{key_name} must be an integer key index from 0 to 4095.")
        else:
            indexes[key_name] = index

    network_index = indexes.get("network_key_index")
    bound_index = indexes.get("app_key_bound_net_key_index")
    if network_index is not None and bound_index is not None and network_index != bound_index:
        warnings.append(
            "keychain.app_key_bound_net_key_index must match keychain.network_key_index "
            "for this single-network gateway profile."
        )

    return warnings


def validate_store_addresses(store):
    warnings = []
    if not isinstance(store, dict):
        return warnings

    local = store.get("local") or {}
    if isinstance(local, dict):
        if "address" in local and not is_valid_unicast(local.get("address")):
            warnings.append("local.address must be a unicast address from 0001 to 7FFF.")
        if "iv_index" in local and not is_valid_iv_index(local.get("iv_index")):
            warnings.append("local.iv_index must be an integer from 0 to FFFFFFFF.")

    for section_name in ("nodes", "remote_nodes"):
        nodes = store.get(section_name) or {}
        if not isinstance(nodes, dict):
            warnings.append(f"{section_name} must be a mapping.")
            continue

        for uuid, info in nodes.items():
            if not is_valid_uuid(uuid):
                warnings.append(f"{section_name}.{uuid} is not a valid UUID key.")
            if not isinstance(info, dict):
                warnings.append(f"{section_name}.{uuid} must be a mapping.")
                continue

            unicast = optional_int(info.get("unicast"))
            count = optional_int(info.get("count", 1))
            if not is_valid_unicast(unicast):
                warnings.append(f"{section_name}.{uuid}.unicast must be a unicast address from 0001 to 7FFF.")
            if count is None or count <= 0:
                warnings.append(f"{section_name}.{uuid}.count must be a positive integer.")
            elif unicast is not None and unicast + count - 1 > 0x7FFF:
                warnings.append(f"{section_name}.{uuid} address range must stay within 0001..7FFF.")

    return warnings


def is_valid_uuid(value):
    try:
        normalized_uuid(value)
    except ValueError:
        return False
    return True


def is_valid_hex_key(value, byte_count=16):
    if not isinstance(value, str):
        return False
    normalized = value.replace(":", "").replace(" ", "").replace("-", "")
    if len(normalized) != byte_count * 2:
        return False
    try:
        bytes.fromhex(normalized)
    except ValueError:
        return False
    return True


def optional_int(value):
    try:
        if isinstance(value, str):
            text = value.strip()
            return int(text[2:], 16) if text.lower().startswith("0x") else int(text, 10)
        return int(value)
    except (TypeError, ValueError):
        return None


def is_valid_unicast(value):
    address = optional_int(value)
    return address is not None and 1 <= address <= 0x7FFF


def is_valid_iv_index(value):
    iv_index = optional_int(value)
    return iv_index is not None and 0 <= iv_index <= 0xFFFFFFFF


def optional_key_index(value):
    try:
        if isinstance(value, str):
            text = value.strip()
            index = int(text[2:], 16) if text.lower().startswith("0x") else int(text, 10)
        else:
            index = int(value)
    except (TypeError, ValueError):
        return None

    if not 0 <= index <= 0xFFF:
        return None
    return index


def command_exists(name):
    return shutil.which(name) is not None


def run_status(command):
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    except OSError as exc:
        return False, str(exc)

    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def bluetooth_adapters(sys_class="/sys/class/bluetooth"):
    return sorted(
        os.path.basename(path)
        for path in glob.glob(os.path.join(sys_class, "hci*"))
    )


def mqtt_connect_warnings(config, timeout=3.0):
    mqtt = config.get("mqtt") or {}
    broker = mqtt.get("broker")
    if not broker or is_placeholder(broker):
        return []

    port = optional_int(mqtt.get("port") or 1883)
    if port is None or not 1 <= port <= 65535:
        return []

    endpoint = f"{broker}:{port}"
    try:
        with socket.create_connection((str(broker), port), timeout=timeout):
            return []
    except OSError as exc:
        return [f"MQTT broker {endpoint} was not reachable from this host: {exc}"]


def host_checks(skip_docker=False, bluetooth_sys_class="/sys/class/bluetooth"):
    warnings = []

    if sys.platform != "linux":
        warnings.append(f"host platform is {sys.platform}; real BLE Mesh testing needs Linux with BlueZ.")

    if not skip_docker:
        if not command_exists("docker"):
            warnings.append("docker command not found.")
        else:
            docker_compose_ok, docker_compose_output = run_status(["docker", "compose", "version"])
            if not docker_compose_ok:
                warnings.append(f"docker compose is not ready: {docker_compose_output or 'command failed'}")

    adapters = bluetooth_adapters(bluetooth_sys_class)
    if not adapters:
        warnings.append(f"No Linux Bluetooth hci* adapter was found under {bluetooth_sys_class}.")

    if command_exists("systemctl"):
        bluetooth_ok, bluetooth_output = run_status(["systemctl", "is-active", "bluetooth"])
        if bluetooth_ok and bluetooth_output.strip() == "active":
            warnings.append("system bluetooth service is active; stop/disable it before running bluetooth-meshd.")

    return warnings


def print_report(config, store, args):
    errors, warnings, mesh_topic = validate_config(config)
    store_errors, store_warnings = split_store_findings(validate_store(config, store))
    errors.extend(store_errors)
    warnings.extend(store_warnings)
    mqtt = config.get("mqtt") or {}
    discovery_prefix = args.discovery_prefix or mqtt.get("discovery_prefix") or "homeassistant"

    if args.host:
        warnings.extend(host_checks(skip_docker=args.skip_docker))
    if getattr(args, "check_mqtt", False):
        warnings.extend(mqtt_connect_warnings(config, timeout=getattr(args, "mqtt_connect_timeout", 3.0)))

    print("Pesetech/Home Assistant preflight")
    print()

    for device_id in (config.get("mesh") or {}).keys():
        if is_placeholder(device_id):
            continue
        print(f"{device_id}:")
        print(f"  discovery: {discovery_topic(discovery_prefix, 'light', mesh_topic, device_id)}")
        print(f"  command:   {command_topic(discovery_prefix, 'light', mesh_topic, device_id)}")

    if warnings:
        print("\nWarnings:")
        for warning in warnings:
            print(f"  - {warning}")

    if errors:
        print("\nErrors:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("\nConfig preflight passed.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Validate Pesetech skylight gateway config before hardware testing.")
    parser.add_argument("--config", default="docker/config/config.yaml", help="Gateway config.yaml path.")
    parser.add_argument("--store", default="docker/config/store.yaml", help="Gateway store.yaml path.")
    parser.add_argument("--discovery-prefix", default=None, help="MQTT discovery prefix override.")
    parser.add_argument("--host", action="store_true", help="Also check host Docker/Linux/Bluetooth readiness.")
    parser.add_argument("--skip-docker", action="store_true", help="Skip Docker host checks, useful inside a Home Assistant app/add-on.")
    parser.add_argument("--check-mqtt", action="store_true", help="Warn if the configured MQTT broker host/port is not reachable over TCP.")
    parser.add_argument("--mqtt-connect-timeout", type=float, default=3.0, help="Seconds to wait for the optional MQTT TCP reachability check.")
    args = parser.parse_args()

    if not os.path.exists(args.config):
        print(f"Config file not found: {args.config}", file=sys.stderr)
        raise SystemExit(1)

    config = load_yaml(args.config)
    store = load_yaml(args.store) if os.path.exists(args.store) else {}

    raise SystemExit(print_report(config, store, args))


if __name__ == "__main__":
    main()
