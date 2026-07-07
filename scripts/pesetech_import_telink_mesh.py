#!/usr/bin/env python3
import argparse
import json
import secrets
import sys
import time
from pathlib import Path
from uuid import UUID

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pesetech_preflight import load_simple_yaml


LIGHT_CTL_TEMPERATURE_SERVER = 0x1306
DEFAULT_DEVICE_ID = "skylight"
DEFAULT_DEVICE_NAME = "Pesetech Skylight"
STORAGE_REQUIRED_FIELDS = ("provisioners", "nodes", "netKeys", "appKeys")


class ImportErrorWithDetail(ValueError):
    pass


def clean_hex(value, name, byte_count):
    if not isinstance(value, str):
        raise ImportErrorWithDetail(f"{name} must be a hex string.")
    normalized = value.replace(":", "").replace(" ", "").replace("-", "").lower()
    try:
        bytes.fromhex(normalized)
    except ValueError as exc:
        raise ImportErrorWithDetail(f"{name} is not valid hex.") from exc
    if len(normalized) != byte_count * 2:
        raise ImportErrorWithDetail(f"{name} must be {byte_count} bytes ({byte_count * 2} hex characters).")
    return normalized


def parse_hex_address(value, name):
    if isinstance(value, int):
        address = value
    elif isinstance(value, str):
        text = value.strip()
        if text.lower().startswith("0x"):
            text = text[2:]
        address = int(text, 16)
    else:
        raise ImportErrorWithDetail(f"{name} must be a hex address.")

    if not 1 <= address <= 0x7FFF:
        raise ImportErrorWithDetail(f"{name} must be a unicast address from 0001 to 7FFF.")
    return address


def parse_uuid(value, name):
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise ImportErrorWithDetail(f"{name} must be a UUID.") from exc


def parse_key_index(value, name, default=0):
    if value is None:
        value = default
    try:
        if isinstance(value, str):
            text = value.strip()
            index = int(text[2:], 16) if text.lower().startswith("0x") else int(text, 10)
        else:
            index = int(value)
    except (TypeError, ValueError) as exc:
        raise ImportErrorWithDetail(f"{name} must be a key index from 0 to 4095.") from exc

    if not 0 <= index <= 0xFFF:
        raise ImportErrorWithDetail(f"{name} must be a key index from 0 to 4095.")
    return index


def load_json(path):
    with open(path, "r", encoding="utf-8") as input_file:
        return json.load(input_file)


def clean_string(value):
    if value is None:
        return ""
    return str(value).strip()


def looks_like_mesh_storage(value):
    return isinstance(value, dict) and all(field in value for field in STORAGE_REQUIRED_FIELDS)


def parse_json_string(value, path):
    text = value.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def find_mesh_storage_candidates(value, path="root"):
    if looks_like_mesh_storage(value):
        return [(path, value)]

    if isinstance(value, str):
        parsed = parse_json_string(value, path)
        if parsed is None:
            return []
        return find_mesh_storage_candidates(parsed, f"{path}<json>")

    candidates = []
    if isinstance(value, list):
        for index, item in enumerate(value):
            candidates.extend(find_mesh_storage_candidates(item, f"{path}[{index}]"))
    elif isinstance(value, dict):
        preferred_keys = (
            "meshJson",
            "jsonNode",
            "meshStorage",
            "mesh",
            "homeInfo",
            "home",
            "data",
            "result",
            "info",
            "list",
        )
        seen = set()
        for key in preferred_keys:
            if key in value:
                seen.add(key)
                candidates.extend(find_mesh_storage_candidates(value[key], f"{path}.{key}"))
        for key, item in value.items():
            if key not in seen:
                candidates.extend(find_mesh_storage_candidates(item, f"{path}.{key}"))
    return candidates


def normalize_mesh_candidate(value):
    if value in (None, "", 0):
        return None
    try:
        candidate = int(value)
    except (TypeError, ValueError) as exc:
        raise ImportErrorWithDetail("--mesh-candidate must be 0 or a positive integer.") from exc
    if candidate < 1:
        raise ImportErrorWithDetail("--mesh-candidate must be 0 or a positive integer.")
    return candidate


def load_mesh_storage(path, mesh_candidate=None):
    raw = load_json(path)
    candidates = find_mesh_storage_candidates(raw)
    if not candidates:
        raise ImportErrorWithDetail(
            "Could not find a Telink MeshStorage object. Expected raw mesh.json, "
            'a {"meshJson": "..."} wrapper, or a {"jsonNode": {...}} wrapper.'
        )

    unique = {}
    for candidate_path, storage in candidates:
        unique.setdefault(id(storage), (candidate_path, storage))
    candidates = list(unique.values())
    selected = normalize_mesh_candidate(mesh_candidate)
    if selected is not None:
        if selected > len(candidates):
            raise ImportErrorWithDetail(f"--mesh-candidate must be between 1 and {len(candidates)}.")
        return candidates[selected - 1][1]
    if len(candidates) > 1:
        formatted = format_storage_candidate_list(candidates)
        raise ImportErrorWithDetail(
            "Multiple Telink MeshStorage objects were found. Re-run with --mesh-candidate N, "
            "or save the desired meshJson/jsonNode as its own file, then retry:\n" + formatted
        )

    return candidates[0][1]


def load_yaml(path):
    if not path.exists():
        return {}
    try:
        import yaml
        if not getattr(yaml, "__file__", None) or not hasattr(yaml, "safe_load"):
            raise ImportError
    except ImportError:
        return load_simple_yaml(path.read_text(encoding="utf-8"))

    with open(path, "r", encoding="utf-8") as input_file:
        return yaml.safe_load(input_file) or {}


def dump_yaml(data):
    try:
        import yaml
        if not getattr(yaml, "__file__", None) or not hasattr(yaml, "safe_dump"):
            raise ImportError
    except ImportError:
        return dump_simple_yaml(data)

    return yaml.safe_dump(data, sort_keys=False)


def dump_simple_yaml(data, indent=0):
    lines = []
    for key, value in data.items():
        prefix = " " * indent + f"{key}:"
        if isinstance(value, dict):
            lines.append(prefix)
            lines.append(dump_simple_yaml(value, indent + 2).rstrip())
        elif isinstance(value, bool):
            lines.append(f"{prefix} {'true' if value else 'false'}")
        elif value is None:
            lines.append(f"{prefix} null")
        else:
            lines.append(f"{prefix} {value}")
    return "\n".join(lines) + "\n"


def write_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_yaml(data), encoding="utf-8")


def provisioner_uuids(storage):
    return {parse_uuid(item.get("UUID"), "provisioner.UUID") for item in storage.get("provisioners") or []}


def is_provisioner_node(storage, node):
    try:
        return parse_uuid(node.get("UUID"), "node.UUID") in provisioner_uuids(storage)
    except ImportErrorWithDetail:
        return False


def model_id(value):
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    return int(text, 16)


def node_model_ids(node):
    ids = set()
    for element in node.get("elements") or []:
        for model in element.get("models") or []:
            try:
                ids.add(model_id(model.get("modelId")))
            except (TypeError, ValueError):
                continue
    return ids


def format_model_id(value):
    model = model_id(value)
    if model <= 0xFFFF:
        return f"{model:04X}"
    return f"{model:08X}"


def imported_model_bindings(node):
    base = parse_hex_address(node.get("unicastAddress"), "node.unicastAddress")
    bindings = {}
    for element_index, element in enumerate(node.get("elements") or []):
        for model in element.get("models") or []:
            try:
                bindings.setdefault(format_model_id(model.get("modelId")), base + element_index)
            except (TypeError, ValueError):
                continue
    return bindings


def node_count(node):
    elements = node.get("elements") or []
    return max(1, len(elements))


def non_provisioner_nodes(storage):
    return [node for node in storage.get("nodes") or [] if not is_provisioner_node(storage, node)]


def format_node(node):
    uuid = node.get("UUID", "<missing uuid>")
    address = node.get("unicastAddress", "<missing address>")
    name = node.get("name") or "<unnamed>"
    models = ",".join(f"{item:04X}" for item in sorted(node_model_ids(node)))
    return f"{uuid} at {address} ({name}; models: {models or 'none'})"


def safe_storage_count(storage, key):
    value = storage.get(key)
    return len(value) if isinstance(value, list) else 0


def safe_candidate_nodes(storage):
    try:
        return non_provisioner_nodes(storage)
    except (TypeError, ValueError, ImportErrorWithDetail):
        nodes = storage.get("nodes") if isinstance(storage, dict) else []
        return nodes if isinstance(nodes, list) else []


def format_candidate_node(node):
    try:
        return format_node(node)
    except (TypeError, ValueError, ImportErrorWithDetail, AttributeError):
        if not isinstance(node, dict):
            return "<invalid node>"
        return f"{node.get('UUID', '<missing uuid>')} at {node.get('unicastAddress', '<missing address>')}"


def format_storage_candidate(candidate_path, storage, index):
    nodes = safe_candidate_nodes(storage)
    ctl_nodes = [node for node in nodes if LIGHT_CTL_TEMPERATURE_SERVER in node_model_ids(node)]
    visible_nodes = ctl_nodes or nodes
    lines = [
        f"  {index}. {candidate_path}",
        (
            "     counts: "
            f"netKeys={safe_storage_count(storage, 'netKeys')}, "
            f"appKeys={safe_storage_count(storage, 'appKeys')}, "
            f"nodes={safe_storage_count(storage, 'nodes')}"
        ),
    ]
    if visible_nodes:
        label = "likely CTL light nodes" if ctl_nodes else "candidate nodes"
        lines.append(f"     {label}:")
        for node in visible_nodes[:5]:
            lines.append(f"       - {format_candidate_node(node)}")
        if len(visible_nodes) > 5:
            lines.append(f"       - ... {len(visible_nodes) - 5} more")
    return "\n".join(lines)


def format_storage_candidate_list(candidates):
    return "\n".join(
        format_storage_candidate(candidate_path, storage, index)
        for index, (candidate_path, storage) in enumerate(candidates, start=1)
    )


def select_node(storage, node_uuid=None, node_unicast=None):
    nodes = non_provisioner_nodes(storage)
    if node_uuid:
        wanted_uuid = parse_uuid(node_uuid, "--node-uuid")
        matches = [node for node in nodes if parse_uuid(node.get("UUID"), "node.UUID") == wanted_uuid]
    elif node_unicast:
        wanted_unicast = parse_hex_address(node_unicast, "--node-unicast")
        matches = [node for node in nodes if parse_hex_address(node.get("unicastAddress"), "node.unicastAddress") == wanted_unicast]
    else:
        ctl_matches = [node for node in nodes if LIGHT_CTL_TEMPERATURE_SERVER in node_model_ids(node)]
        matches = ctl_matches or nodes

    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ImportErrorWithDetail("No matching non-provisioner node found in Telink mesh JSON.")

    formatted = "\n".join(f"  - {format_node(node)}" for node in matches)
    raise ImportErrorWithDetail(
        "Multiple candidate nodes were found. Re-run with --node-uuid or --node-unicast:\n" + formatted
    )


def select_nodes(storage, node_uuid=None, node_unicast=None):
    if node_uuid or node_unicast:
        return [select_node(storage, node_uuid, node_unicast)]

    nodes = non_provisioner_nodes(storage)
    ctl_matches = [node for node in nodes if LIGHT_CTL_TEMPERATURE_SERVER in node_model_ids(node)]
    matches = ctl_matches or nodes
    if not matches:
        raise ImportErrorWithDetail("No matching non-provisioner node found in Telink mesh JSON.")
    return matches


def storage_net_keys(storage):
    keys = storage.get("netKeys") or []
    if not keys:
        raise ImportErrorWithDetail("Telink mesh JSON does not contain netKeys.")
    result = {}
    for key in keys:
        index = parse_key_index(key.get("index"), "netKeys[].index")
        result[index] = clean_hex(key.get("key"), f"netKeys[{index}].key", 16)
    return result


def storage_app_keys(storage):
    keys = storage.get("appKeys") or []
    if not keys:
        raise ImportErrorWithDetail("Telink mesh JSON does not contain appKeys.")
    result = {}
    for key in keys:
        index = parse_key_index(key.get("index"), "appKeys[].index")
        bound_net_key = parse_key_index(key.get("boundNetKey"), f"appKeys[{index}].boundNetKey")
        result[index] = {
            "index": index,
            "bound_net_key": bound_net_key,
            "key": clean_hex(key.get("key"), f"appKeys[{index}].key", 16),
        }
    return result


def node_app_key_indexes(node):
    indexes = []
    for key in node.get("appKeys") or []:
        indexes.append(parse_key_index(key.get("index"), "node.appKeys[].index"))
    return sorted(indexes)


def normalized_default_entity_id(value, device_id):
    value = str(value).strip() if value else ""
    if not value:
        return f"light.{device_id}"
    if not value.startswith("light."):
        raise ImportErrorWithDetail("default_entity_id must be a Home Assistant light entity id, for example light.skylight.")
    return value


def selected_keys(storage, node):
    net_keys = storage_net_keys(storage)
    app_keys = storage_app_keys(storage)
    node_indexes = node_app_key_indexes(node)

    if node_indexes:
        matching_indexes = [index for index in node_indexes if index in app_keys]
        if not matching_indexes:
            known = ", ".join(str(index) for index in sorted(app_keys))
            wanted = ", ".join(str(index) for index in node_indexes)
            raise ImportErrorWithDetail(
                f"Selected node is bound to app key index(es) {wanted}, "
                f"but mesh appKeys only contain index(es) {known}."
            )
        app_key = app_keys[matching_indexes[0]]
    else:
        app_key = app_keys[sorted(app_keys)[0]]

    net_key_index = app_key["bound_net_key"]
    if net_key_index not in net_keys:
        known = ", ".join(str(index) for index in sorted(net_keys))
        raise ImportErrorWithDetail(
            f"Selected app key index {app_key['index']} is bound to net key index {net_key_index}, "
            f"but mesh netKeys only contain index(es) {known}."
        )

    return {
        "network_key_index": net_key_index,
        "network_key": net_keys[net_key_index],
        "app_key_index": app_key["index"],
        "app_key_bound_net_key_index": net_key_index,
        "app_key": app_key["key"],
    }


def address_ranges(storage):
    ranges = []
    for node in storage.get("nodes") or []:
        try:
            start = parse_hex_address(node.get("unicastAddress"), "node.unicastAddress")
        except ImportErrorWithDetail:
            continue
        end = start + node_count(node) - 1
        ranges.append((start, end))
    return ranges


def non_provisioner_address_ranges(storage):
    ranges = []
    for node in non_provisioner_nodes(storage):
        try:
            start = parse_hex_address(node.get("unicastAddress"), "node.unicastAddress")
        except ImportErrorWithDetail:
            continue
        end = start + node_count(node) - 1
        ranges.append((start, end))
    return ranges


def provisioner_address_ranges(storage):
    ranges = []
    for node in storage.get("nodes") or []:
        if not is_provisioner_node(storage, node):
            continue
        try:
            start = parse_hex_address(node.get("unicastAddress"), "node.unicastAddress")
        except ImportErrorWithDetail:
            continue
        end = start + node_count(node) - 1
        ranges.append((start, end))
    return ranges


def overlaps(address, ranges):
    return any(start <= address <= end for start, end in ranges)


def choose_local_address(storage, explicit=None):
    if explicit:
        address = parse_hex_address(explicit, "--local-address")
        if overlaps(address, non_provisioner_address_ranges(storage)):
            raise ImportErrorWithDetail(f"--local-address {address:04X} overlaps an existing non-provisioner mesh node.")
        if overlaps(address, provisioner_address_ranges(storage)):
            return address
        if overlaps(address, address_ranges(storage)):
            raise ImportErrorWithDetail(f"--local-address {address:04X} overlaps an existing mesh node.")
        return address

    used = address_ranges(storage)
    for address in range(1, 0x8000):
        if not overlaps(address, used):
            return address
    raise ImportErrorWithDetail("Could not find an unused local unicast address.")


def next_base_address(storage, local_address):
    highest = local_address
    for start, end in address_ranges(storage):
        highest = max(highest, end)
    return min(highest + 1, 0x7FFF)


def storage_iv_index(storage):
    value = storage.get("ivIndex") or "00000000"
    if isinstance(value, int):
        return value
    return int(str(value), 16)


def ensure_no_live_store(path, force):
    if force or not path.exists():
        return
    existing = load_yaml(path)
    if existing.get("keychain") or existing.get("nodes") or existing.get("remote_nodes"):
        raise ImportErrorWithDetail(f"{path} already contains mesh data; pass --force to overwrite it.")


def node_device_suffix(node, index):
    unicast = parse_hex_address(node.get("unicastAddress"), "node.unicastAddress")
    if unicast == 0x0802:
        return "a"
    if unicast == 0x0005:
        return "b"
    return f"{unicast:04x}" if index else ""


def node_device_id(base_id, node, index, total):
    base_id = clean_string(base_id) or DEFAULT_DEVICE_ID
    if total <= 1:
        return base_id
    suffix = node_device_suffix(node, index)
    return f"{base_id}_{suffix}" if suffix else base_id


def node_device_name(base_name, node, index, total):
    base_name = clean_string(base_name) or DEFAULT_DEVICE_NAME
    if total <= 1:
        return base_name

    unicast = parse_hex_address(node.get("unicastAddress"), "node.unicastAddress")
    if unicast == 0x0802:
        return "Skylight A"
    if unicast == 0x0005:
        return "Skylight B"

    name = clean_string(node.get("name"))
    if name and name.lower() != "common node":
        return name
    return f"{base_name} {unicast:04X}"


def update_config(config, nodes, device_id, device_name, default_entity_id=None):
    if not isinstance(config, dict):
        config = {}
    if isinstance(nodes, dict):
        nodes = [nodes]

    mqtt = config.get("mqtt")
    if not isinstance(mqtt, dict):
        mqtt = {}
    config["mqtt"] = mqtt
    mqtt.setdefault("broker", "<home_assistant_mqtt_host>")
    mqtt.setdefault("discovery_prefix", "homeassistant")
    mqtt.setdefault("node_id", "mqtt_mesh")

    mesh = config.get("mesh")
    if not isinstance(mesh, dict):
        mesh = {}
    config["mesh"] = mesh
    total = len(nodes)
    for index, node in enumerate(nodes):
        current_device_id = node_device_id(device_id, node, index, total)
        current_name = node_device_name(device_name, node, index, total)
        current_entity_id = (
            normalized_default_entity_id(default_entity_id, current_device_id)
            if total == 1
            else normalized_default_entity_id(None, current_device_id)
        )
        mesh[current_device_id] = {
            "uuid": parse_uuid(node.get("UUID"), "node.UUID"),
            "name": current_name,
            "default_entity_id": current_entity_id,
            "type": "pesetech_skylight",
            "relay": False,
        }
    return config


def make_gateway_store(storage, nodes, local_address, existing_store=None):
    existing_store = existing_store or {}
    keys = selected_keys(storage, nodes[0])

    keychain = dict(existing_store.get("keychain") or {})
    keychain["network_key"] = keys["network_key"]
    keychain["network_key_index"] = keys["network_key_index"]
    keychain["app_key"] = keys["app_key"]
    keychain["app_key_index"] = keys["app_key_index"]
    keychain["app_key_bound_net_key_index"] = keys["app_key_bound_net_key_index"]
    keychain.setdefault("device_key", secrets.token_hex(16))

    node_store = dict(existing_store.get("nodes") or {})
    remote_nodes = dict(existing_store.get("remote_nodes") or {})
    for node in nodes:
        uuid = parse_uuid(node.get("UUID"), "node.UUID")
        unicast = parse_hex_address(node.get("unicastAddress"), "node.unicastAddress")
        count = node_count(node)
        device_key = clean_hex(node.get("deviceKey"), "node.deviceKey", 16)
        node_store[uuid] = {
            "type": "pesetech_skylight",
            "unicast": unicast,
            "count": count,
            "configured": True,
            "imported_models": imported_model_bindings(node),
        }
        remote_nodes[uuid] = {
            "unicast": unicast,
            "count": count,
            "device_key": device_key,
        }

    return {
        "keychain": keychain,
        "local": {
            "address": local_address,
            "iv_index": storage_iv_index(storage),
        },
        "prov": {
            "base_address": next_base_address(storage, local_address),
        },
        "nodes": node_store,
        "remote_nodes": remote_nodes,
    }


def requested_context(args, mesh_candidate):
    return {
        "mesh_candidate": mesh_candidate or 0,
        "node_uuid": clean_string(getattr(args, "node_uuid", "")),
        "node_unicast": clean_string(getattr(args, "node_unicast", "")),
        "local_address": clean_string(getattr(args, "local_address", "")),
        "device_id": clean_string(getattr(args, "device_id", "")),
        "device_name": clean_string(getattr(args, "device_name", "")),
        "default_entity_id": clean_string(getattr(args, "default_entity_id", "")),
    }


def selected_node_summary(node):
    return {
        "uuid": parse_uuid(node.get("UUID"), "node.UUID"),
        "name": clean_string(node.get("name")),
        "unicast": f"{parse_hex_address(node.get('unicastAddress'), 'node.unicastAddress'):04X}",
        "element_count": node_count(node),
        "models": [f"{item:04X}" for item in sorted(node_model_ids(node))],
        "imported_models": {
            model: f"{address:04X}" for model, address in sorted(imported_model_bindings(node).items())
        },
    }


def import_report(args, *, status, mesh_candidate=None, node=None, nodes=None, local_address=None, store=None, error=None):
    report = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "operation": "import-check" if getattr(args, "dry_run", False) else "import",
        "status": status,
        "dry_run": bool(getattr(args, "dry_run", False)),
        "sent_light_commands": False,
        "published_mqtt": False,
        "wrote_files": status == "passed" and not bool(getattr(args, "dry_run", False)),
        "source": str(Path(args.mesh_json)),
        "config": str(Path(args.config)),
        "store": str(Path(args.store)),
        "requested": requested_context(args, mesh_candidate),
    }
    if nodes is None and node is not None:
        nodes = [node]
    if node is not None:
        report["selected_node"] = selected_node_summary(node)
    if nodes is not None:
        report["selected_nodes"] = [selected_node_summary(item) for item in nodes]
    if local_address is not None:
        report["local_address"] = f"{local_address:04X}"
    if store:
        report["iv_index"] = f"{store['local']['iv_index']:08X}"
        report["net_key_index"] = store["keychain"]["network_key_index"]
        report["app_key_index"] = store["keychain"]["app_key_index"]
    if error:
        report["error"] = str(error)
    return report


def write_report(path, report):
    if not path:
        return
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def safe_mesh_candidate(value):
    try:
        return normalize_mesh_candidate(value)
    except (ImportErrorWithDetail, ValueError):
        return None


def import_telink_mesh(args):
    source = Path(args.mesh_json)
    config_path = Path(args.config)
    store_path = Path(args.store)
    mesh_candidate = normalize_mesh_candidate(getattr(args, "mesh_candidate", None))
    storage = load_mesh_storage(source, mesh_candidate)

    if not storage.get("provisioners"):
        raise ImportErrorWithDetail("Telink mesh JSON does not contain provisioners.")

    nodes = select_nodes(storage, args.node_uuid, args.node_unicast)
    node = nodes[0] if len(nodes) == 1 else None
    local_address = choose_local_address(storage, args.local_address)
    ensure_no_live_store(store_path, args.force)

    config = update_config(
        load_yaml(config_path),
        nodes,
        args.device_id,
        args.device_name,
        getattr(args, "default_entity_id", None),
    )
    store = make_gateway_store(storage, nodes, local_address, load_yaml(store_path) if store_path.exists() else {})

    print("Telink mesh import summary")
    print(f"  source:        {source}")
    if mesh_candidate is not None:
        print(f"  mesh candidate: {mesh_candidate}")
    for selected in nodes:
        print(f"  selected node: {format_node(selected)}")
    print(f"  local address: {local_address:04X}")
    print(f"  iv index:      {store['local']['iv_index']:08X}")
    print(f"  net key index: {store['keychain']['network_key_index']}")
    print(f"  app key index: {store['keychain']['app_key_index']}")
    print(f"  config path:   {config_path}")
    print(f"  store path:    {store_path}")

    if args.dry_run:
        write_report(
            getattr(args, "report_output", None),
            import_report(
                args,
                status="passed",
                mesh_candidate=mesh_candidate,
                node=node,
                nodes=nodes,
                local_address=local_address,
                store=store,
            ),
        )
        print("Dry run only; no files written.")
        return 0

    write_yaml(config_path, config)
    write_yaml(store_path, store)
    write_report(
        getattr(args, "report_output", None),
        import_report(
            args,
            status="passed",
            mesh_candidate=mesh_candidate,
            node=node,
            nodes=nodes,
            local_address=local_address,
            store=store,
        ),
    )
    print("Import files written. Run preflight, then start the gateway in service mode with --reload.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Import a Telink/Pesetech mesh.json into gateway config.yaml/store.yaml.")
    parser.add_argument("mesh_json", help="Telink MeshStorage JSON from the official app/cloud export.")
    parser.add_argument("--config", default="docker/config/config.yaml", help="Gateway config.yaml to create/update.")
    parser.add_argument("--store", default="docker/config/store.yaml", help="Gateway store.yaml to create.")
    parser.add_argument("--device-id", default=DEFAULT_DEVICE_ID, help="Home Assistant/MQTT device id.")
    parser.add_argument("--device-name", default=DEFAULT_DEVICE_NAME, help="Home Assistant display name.")
    parser.add_argument("--default-entity-id", help="Home Assistant entity id hint for MQTT discovery.")
    parser.add_argument("--mesh-candidate", type=int, default=0, help="Select a 1-based MeshStorage candidate when the input contains multiple meshes.")
    parser.add_argument("--node-uuid", help="Select a specific mesh node UUID when JSON contains multiple devices.")
    parser.add_argument("--node-unicast", help="Select a specific mesh node unicast address, such as 0002.")
    parser.add_argument("--local-address", help="Use a specific unused local gateway unicast address.")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing gateway store with mesh data.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the import plan without writing files.")
    parser.add_argument("--report-output", help="Write a key-free JSON import/import-check report.")
    try:
        args = parser.parse_args(argv)
        return import_telink_mesh(args)
    except (OSError, json.JSONDecodeError, ImportErrorWithDetail, ValueError) as exc:
        if "args" in locals() and getattr(args, "report_output", None):
            write_report(
                args.report_output,
                import_report(
                    args,
                    status="failed",
                    mesh_candidate=safe_mesh_candidate(getattr(args, "mesh_candidate", None)),
                    error=exc,
                ),
            )
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
