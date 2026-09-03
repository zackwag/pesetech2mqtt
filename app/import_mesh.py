import argparse
import json
import os
import re
import secrets
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from uuid import UUID

import yaml

REQUIRED_MODELS = {"1000", "1300", "1303", "1306"}
MESH_STORAGE_FIELDS = {"provisioners", "nodes", "netKeys", "appKeys"}


class MeshImportError(ValueError):
    pass


def clean_hex(value, label, byte_count):
    if not isinstance(value, str):
        raise MeshImportError(f"{label} must be a hex string")
    value = re.sub(r"[:\s-]", "", value).lower()
    try:
        parsed = bytes.fromhex(value)
    except ValueError as exc:
        raise MeshImportError(f"{label} is not valid hex") from exc
    if len(parsed) != byte_count:
        raise MeshImportError(f"{label} must be {byte_count} bytes")
    return value


def parse_uuid(value, label):
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise MeshImportError(f"{label} must be a UUID") from exc


def parse_address(value, label):
    try:
        address = value if isinstance(value, int) else int(str(value).lower().removeprefix("0x"), 16)
    except (TypeError, ValueError) as exc:
        raise MeshImportError(f"{label} must be a hexadecimal address") from exc
    if not 1 <= address <= 0x7FFF:
        raise MeshImportError(f"{label} must be a unicast address")
    return address


def parse_index(value, label):
    try:
        index = int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError) as exc:
        raise MeshImportError(f"{label} must be a key index") from exc
    if not 0 <= index <= 0xFFF:
        raise MeshImportError(f"{label} must be between 0 and 4095")
    return index


def model_id(value):
    if isinstance(value, int):
        return f"{value:04X}" if value <= 0xFFFF else f"{value:08X}"
    value = str(value).strip().removeprefix("0x").upper()
    parsed = int(value, 16)
    return f"{parsed:04X}" if parsed <= 0xFFFF else f"{parsed:08X}"


def is_mesh_storage(value):
    return isinstance(value, dict) and MESH_STORAGE_FIELDS <= value.keys()


def mesh_storage_candidates(value):
    if is_mesh_storage(value):
        return [value]
    if isinstance(value, str):
        try:
            return mesh_storage_candidates(json.loads(value))
        except json.JSONDecodeError:
            return []
    if isinstance(value, list):
        return [candidate for item in value for candidate in mesh_storage_candidates(item)]
    if isinstance(value, dict):
        return [candidate for item in value.values() for candidate in mesh_storage_candidates(item)]
    return []


def load_mesh_storage(path):
    with Path(path).open("r", encoding="utf-8") as source:
        candidates = mesh_storage_candidates(json.load(source))
    if not candidates:
        raise MeshImportError("No Telink MeshStorage object was found")
    if len(candidates) != 1:
        raise MeshImportError(
            "Multiple Telink MeshStorage objects were found; save the intended object as its own JSON file"
        )
    storage = candidates[0]
    for key in MESH_STORAGE_FIELDS:
        if not isinstance(storage[key], list):
            raise MeshImportError(f"MeshStorage {key} must be a list")
    return storage


def provisioner_uuids(storage):
    return {parse_uuid(item.get("UUID"), "provisioner UUID") for item in storage["provisioners"]}


def node_models(node):
    result = {}
    unicast = parse_address(node.get("unicastAddress"), "node unicastAddress")
    for position, element in enumerate(node.get("elements") or []):
        element_index = int(element.get("index", position))
        for model in element.get("models") or []:
            try:
                result.setdefault(model_id(model.get("modelId")), unicast + element_index)
            except (TypeError, ValueError):
                continue
    return result


def node_count(node):
    indexes = [int(element.get("index", position)) for position, element in enumerate(node.get("elements") or [])]
    return max(indexes, default=0) + 1


def select_skylights(storage):
    provisioners = provisioner_uuids(storage)
    selected = []
    for node in storage["nodes"]:
        node_uuid = parse_uuid(node.get("UUID"), "node UUID")
        if node_uuid in provisioners:
            continue
        models = node_models(node)
        if "1306" not in models:
            continue
        missing = sorted(REQUIRED_MODELS - models.keys())
        if missing:
            raise MeshImportError(f"Node {node_uuid} is missing required models: {', '.join(missing)}")
        selected.append(node)
    if not selected:
        raise MeshImportError("No Pesetech skylights exposing model 1306 were found")
    return selected


def slugify(value):
    ascii_name = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", ascii_name.lower()).strip("_")


def node_names(nodes):
    names = []
    for node in nodes:
        unicast = parse_address(node.get("unicastAddress"), "node unicastAddress")
        name = str(node.get("name") or "").strip()
        if not name or name.casefold() == "common node":
            names.append((f"Pesetech Skylight {unicast:04X}", f"skylight_{unicast:04x}", unicast))
        else:
            entity_id = slugify(name)
            if not entity_id:
                entity_id = f"skylight_{unicast:04x}"
            names.append((name, entity_id, unicast))

    duplicates = Counter(entity_id for _, entity_id, _ in names)
    return [
        (name, f"{entity_id}_{unicast:04x}" if duplicates[entity_id] > 1 else entity_id)
        for name, entity_id, unicast in names
    ]


def key_maps(storage):
    network_keys = {}
    for item in storage["netKeys"]:
        index = parse_index(item.get("index"), "network key index")
        network_keys[index] = clean_hex(item.get("key"), f"network key {index}", 16)

    application_keys = {}
    for item in storage["appKeys"]:
        index = parse_index(item.get("index"), "application key index")
        application_keys[index] = {
            "key": clean_hex(item.get("key"), f"application key {index}", 16),
            "network_index": parse_index(item.get("boundNetKey"), f"application key {index} network index"),
        }

    if not network_keys or not application_keys:
        raise MeshImportError("MeshStorage must contain network and application keys")
    return network_keys, application_keys


def selected_key(storage, nodes):
    network_keys, application_keys = key_maps(storage)
    selected = []
    for node in nodes:
        indexes = [parse_index(item.get("index"), "node application key index") for item in node.get("appKeys") or []]
        matches = [index for index in indexes if index in application_keys]
        if not matches:
            raise MeshImportError(f"Node {node.get('UUID')} has no application key present in MeshStorage")
        index = matches[0]
        app_key = application_keys[index]
        if app_key["network_index"] not in network_keys:
            raise MeshImportError(f"Application key {index} refers to a missing network key")
        selected.append((app_key["network_index"], index, network_keys[app_key["network_index"]], app_key["key"]))
    if len(set(selected)) != 1:
        raise MeshImportError("Selected skylights do not use the same mesh keys")
    return selected[0]


def address_ranges(storage):
    ranges = []
    for node in storage["nodes"]:
        start = parse_address(node.get("unicastAddress"), "node unicastAddress")
        ranges.append((start, start + node_count(node) - 1))
    return ranges


def choose_local_address(storage):
    used = address_ranges(storage)
    for address in range(1, 0x8000):
        if not any(start <= address <= end for start, end in used):
            return address
    raise MeshImportError("No unused local unicast address is available")


def iv_index(storage):
    value = storage.get("ivIndex") or 0
    return value if isinstance(value, int) else int(str(value), 16)


def build_files(storage):
    nodes = select_skylights(storage)
    names = node_names(nodes)
    network_index, app_index, network_key, app_key = selected_key(storage, nodes)
    local_address = choose_local_address(storage)

    config = {"mesh": {}}
    store = {
        "keychain": {
            "device_key": secrets.token_hex(16),
            "network_key": network_key,
            "network_key_index": network_index,
            "app_key": app_key,
            "app_key_index": app_index,
            "app_key_bound_net_key_index": network_index,
        },
        "local": {"address": local_address, "iv_index": iv_index(storage)},
        "nodes": {},
        "remote_nodes": {},
    }

    for node, (name, entity_id) in zip(nodes, names):
        node_uuid = parse_uuid(node.get("UUID"), "node UUID")
        unicast = parse_address(node.get("unicastAddress"), "node unicastAddress")
        count = node_count(node)
        config["mesh"][entity_id] = {
            "uuid": node_uuid,
            "name": name,
            "default_entity_id": f"light.{entity_id}",
            "type": "pesetech_skylight",
        }
        store["nodes"][node_uuid] = {
            "type": "pesetech_skylight",
            "unicast": unicast,
            "count": count,
            "configured": True,
            "imported_models": {key: value for key, value in node_models(node).items() if key in REQUIRED_MODELS},
        }
        store["remote_nodes"][node_uuid] = {
            "unicast": unicast,
            "count": count,
            "device_key": clean_hex(node.get("deviceKey"), "node deviceKey", 16),
        }
    return config, store


def write_yaml(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    os.replace(temporary, path)


def import_mesh(source, config_path, store_path):
    config_path = Path(config_path)
    store_path = Path(store_path)
    if config_path.exists() or store_path.exists():
        raise MeshImportError("Import requires both output files to be absent")
    config, store = build_files(load_mesh_storage(source))
    config_path.parent.mkdir(parents=True, exist_ok=True)
    write_yaml(store_path, store)
    write_yaml(config_path, config)
    print(f"Imported {len(config['mesh'])} Pesetech skylight(s).")


def ensure_data(source, config_path, store_path):
    source = Path(source)
    config_path = Path(config_path)
    store_path = Path(store_path)
    if config_path.exists() and store_path.exists():
        print("Using existing mesh configuration in /data.")
        return False
    if config_path.exists() or store_path.exists():
        raise MeshImportError("config.yaml and store.yaml must either both exist or both be absent")
    if not source.exists():
        raise MeshImportError(f"First start requires {source}")
    import_mesh(source, config_path, store_path)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Import a Pesetech/Telink MeshStorage JSON file")
    parser.add_argument("--ensure", action="store_true")
    parser.add_argument("source")
    parser.add_argument("config")
    parser.add_argument("store")
    args = parser.parse_args(argv)
    try:
        if args.ensure:
            ensure_data(args.source, args.config, args.store)
        else:
            import_mesh(args.source, args.config, args.store)
    except (OSError, json.JSONDecodeError, MeshImportError, yaml.YAMLError) as exc:
        print(f"Mesh import failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
