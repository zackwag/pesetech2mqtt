import argparse
import asyncio
import logging
import os
from pathlib import Path
from uuid import UUID

import yaml
from bluetooth_mesh import models
from bluetooth_mesh.application import Application, Element
from bluetooth_mesh.crypto import ApplicationKey, DeviceKey, NetworkKey
from bluetooth_mesh.messages.config import GATTNamespaceDescriptor
from dbus_next.errors import DBusError

from .mqtt import run_mqtt
from .skylight import PesetechSkylight

LOGGER = logging.getLogger(__name__)
ALREADY_EXISTS = "org.bluez.mesh.Error.AlreadyExists"


class MainElement(Element):
    LOCATION = GATTNamespaceDescriptor.MAIN
    MODELS = [
        models.ConfigClient,
        models.HealthClient,
        models.GenericOnOffClient,
        models.LightLightnessClient,
        models.LightCTLClient,
    ]


def load_yaml(path):
    with Path(path).open("r", encoding="utf-8") as source:
        value = yaml.safe_load(source)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return value


def required_mapping(value, key, source):
    result = value.get(key)
    if not isinstance(result, dict):
        raise ValueError(f"{source} requires a {key} mapping")
    return result


def required_int(value, key, source, minimum, maximum):
    try:
        result = int(value[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{source} requires integer {key}") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{source}.{key} must be between {minimum} and {maximum}")
    return result


def required_key(value, key, source):
    try:
        result = bytes.fromhex(value[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{source} requires a 16-byte hexadecimal {key}") from exc
    if len(result) != 16:
        raise ValueError(f"{source}.{key} must be 16 bytes")
    return result


def normalized_uuid(value, label):
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a UUID") from exc


def load_nodes(config, store):
    configured = required_mapping(config, "mesh", "config.yaml")
    stored = required_mapping(store, "nodes", "store.yaml")
    by_uuid = {}
    for entity_id, node_config in configured.items():
        if not isinstance(node_config, dict):
            raise ValueError(f"config.yaml mesh entry {entity_id} must be a mapping")
        node_uuid = normalized_uuid(node_config.get("uuid"), f"config.yaml mesh entry {entity_id} UUID")
        if node_uuid in by_uuid:
            raise ValueError(f"config.yaml contains duplicate node UUID {node_uuid}")
        if node_config.get("type") != "pesetech_skylight":
            raise ValueError(f"config.yaml mesh entry {entity_id} is not a Pesetech skylight")
        name = str(node_config.get("name") or "").strip()
        if not name:
            raise ValueError(f"config.yaml mesh entry {entity_id} requires a name")
        by_uuid[node_uuid] = {
            "id": str(entity_id),
            "name": name,
            "default_entity_id": node_config.get("default_entity_id", f"light.{entity_id}"),
        }

    nodes = []
    for node_uuid, node_data in stored.items():
        node_uuid = normalized_uuid(node_uuid, "store.yaml node UUID")
        if node_uuid not in by_uuid:
            raise ValueError(f"store.yaml node {node_uuid} has no config.yaml mesh entry")
        if not isinstance(node_data, dict) or node_data.get("type") != "pesetech_skylight":
            raise ValueError(f"store.yaml node {node_uuid} is not a Pesetech skylight")
        nodes.append(PesetechSkylight(node_uuid, node_data, by_uuid.pop(node_uuid)))

    if by_uuid:
        raise ValueError(f"config.yaml contains nodes missing from store.yaml: {', '.join(sorted(by_uuid))}")
    if not nodes:
        raise ValueError("No Pesetech skylights are configured")
    return nodes


class PesetechGateway(Application):
    COMPANY_ID = 0x05F1
    PRODUCT_ID = 1
    VERSION_ID = 1
    ELEMENTS = {0: MainElement}
    CRPL = 32768
    PATH = "/org/hass/mesh"

    def __init__(self, loop, data_dir):
        super().__init__(loop)
        data_dir = Path(data_dir)
        config = load_yaml(data_dir / "config.yaml")
        store = load_yaml(data_dir / "store.yaml")

        keychain = required_mapping(store, "keychain", "store.yaml")
        local = required_mapping(store, "local", "store.yaml")
        self._dev_key = DeviceKey(required_key(keychain, "device_key", "store.yaml keychain"))
        self._network_index = required_int(keychain, "network_key_index", "store.yaml keychain", 0, 0xFFF)
        self._network_key = NetworkKey(required_key(keychain, "network_key", "store.yaml keychain"))
        self._app_index = required_int(keychain, "app_key_index", "store.yaml keychain", 0, 0xFFF)
        self._app_network_index = required_int(
            keychain,
            "app_key_bound_net_key_index",
            "store.yaml keychain",
            0,
            0xFFF,
        )
        if self._app_network_index != self._network_index:
            raise ValueError("The application key must be bound to the primary network key")
        self._app_key = ApplicationKey(required_key(keychain, "app_key", "store.yaml keychain"))
        self.address = required_int(local, "address", "store.yaml local", 1, 0x7FFF)
        self.iv_index = required_int(local, "iv_index", "store.yaml local", 0, 0xFFFFFFFF)
        self.nodes = load_nodes(config, store)
        self.remote_nodes = self._load_remote_nodes(store)

    @property
    def dev_key(self):
        return self._dev_key

    @property
    def primary_net_key(self):
        return self._network_index, self._network_key

    @property
    def app_keys(self):
        return [(self._app_index, self._app_network_index, self._app_key)]

    def _load_remote_nodes(self, store):
        remote = required_mapping(store, "remote_nodes", "store.yaml")
        expected = {str(node.uuid) for node in self.nodes}
        normalized = {}
        for node_uuid, value in remote.items():
            key = normalized_uuid(node_uuid, "remote node UUID")
            if key in normalized:
                raise ValueError(f"store.yaml contains duplicate remote node UUID {key}")
            normalized[key] = value
        if set(normalized) != expected:
            raise ValueError("store.yaml remote_nodes must exactly match the configured skylights")
        result = []
        for node_uuid, value in normalized.items():
            if not isinstance(value, dict):
                raise ValueError(f"remote node {node_uuid} must be a mapping")
            result.append(
                (
                    node_uuid,
                    required_int(value, "unicast", f"remote node {node_uuid}", 1, 0x7FFF),
                    required_int(value, "count", f"remote node {node_uuid}", 1, 255),
                    DeviceKey(required_key(value, "device_key", f"remote node {node_uuid}")),
                )
            )
        return result

    async def _import(self, label, operation):
        try:
            await operation
        except DBusError as exc:
            if exc.type != ALREADY_EXISTS:
                raise
            LOGGER.info("%s already exists in BlueZ", label)
        else:
            LOGGER.info("Imported %s into BlueZ", label)

    async def import_mesh_data(self):
        await self._import(
            f"network key {self._network_index}",
            self.management_interface.import_subnet(self._network_index, self._network_key),
        )
        await self._import(
            f"application key {self._app_index}",
            self.management_interface.import_app_key(self._network_index, self._app_index, self._app_key),
        )
        for node_uuid, unicast, count, device_key in self.remote_nodes:
            await self._import(
                f"remote node {node_uuid} at {unicast:04X}",
                self.management_interface.import_remote_node(unicast, count, device_key),
            )

        for model in (models.GenericOnOffClient, models.LightLightnessClient, models.LightCTLClient):
            await self.elements[0][model].bind(self._app_index)
            LOGGER.info("Bound local %s to application key %d", model.__name__, self._app_index)

    async def _heartbeat(self):
        path = Path("/tmp/gateway.healthy")
        while True:
            path.touch()
            await asyncio.sleep(30)

    async def run(self):
        async with self:
            await self.connect()
            await self.import_mesh_data()
            for node in self.nodes:
                await node.bind(self)
            heartbeat = asyncio.create_task(self._heartbeat())
            try:
                await run_mqtt(self.nodes)
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="/data")
    args = parser.parse_args(argv)
    log_level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        gateway = PesetechGateway(loop, args.data_dir)
        loop.run_until_complete(gateway.run())
    except KeyboardInterrupt:
        return 0
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
