import asyncio
import logging
import secrets
import argparse
import uuid
import os
import time

from contextlib import AsyncExitStack, suppress

from bluetooth_mesh.application import Application, Element
from bluetooth_mesh.crypto import ApplicationKey, DeviceKey, NetworkKey
from bluetooth_mesh.messages.config import GATTNamespaceDescriptor
from bluetooth_mesh import models

from tools import Config, Store, Tasks
from mesh import Node, NodeManager
from mqtt import HassMqttMessenger

from modules.provisioner import ProvisionerModule
from modules.raw_command import RawCommandModule
from modules.scanner import ScannerModule
from modules.manager import ManagerModule
from modules.model_scope import ModelScopeModule
from modules.state_reader import StateReaderModule
from modules.skylight_programs import SkylightProgramsModule

from mesh.nodes.light import Light
from diagnostics import BtmonMonitor, DiagnosticExportServer, DiagnosticMonitor


logging.basicConfig(level=logging.DEBUG)


MESH_MODULES = {
    "prov": ProvisionerModule(),
    "scan": ScannerModule(),
    "mgmt": ManagerModule(),
    "read-state": StateReaderModule(),
    "model-scope": ModelScopeModule(),
    "raw-command": RawCommandModule(),
    "skylight-programs": SkylightProgramsModule(),
}


NODE_TYPES = {
    "generic": Node,
    "light": Light,
    "pesetech_skylight": Light,
}


NETWORK_KEY_RELOAD_NAMES = {"network_key", "primary_net_key"}


class MainElement(Element):
    """
    Represents the main element of the application node
    """

    LOCATION = GATTNamespaceDescriptor.MAIN
    MODELS = [
        models.ConfigClient,
        models.HealthClient,
        models.GenericOnOffClient,
        models.LightLightnessClient,
        models.LightCTLClient,
    ]


class MqttGateway(Application):

    COMPANY_ID = 0x05F1  # The Linux Foundation
    PRODUCT_ID = 1
    VERSION_ID = 1
    ELEMENTS = {
        0: MainElement,
    }
    CRPL = 32768
    PATH = "/org/hass/mesh"

    def __init__(self, loop, basedir):
        super().__init__(loop)

        self._store = Store(location=os.path.join(basedir, "store.yaml"))
        self._config = Config(os.path.join(basedir, "config.yaml"))
        self._nodes = {}

        self._messenger = None

        self._app_keys = None
        self._dev_key = None
        self._primary_net_key = None
        self._primary_net_key_index = 0
        self._remote_nodes = {}
        self._new_keys = set()
        self.diagnostic_monitor = None
        self.btmon_monitor = None
        self.diagnostic_export = None

        # load mesh modules
        for name, module in MESH_MODULES.items():
            module.initialize(self, self._store.section(name), self._config)

        self._initialize()

    @property
    def dev_key(self):
        if not self._dev_key:
            raise Exception("Device key not ready")
        return self._dev_key

    @property
    def primary_net_key(self):
        if not self._primary_net_key:
            raise Exception("Primary network key not ready")
        return self._primary_net_key_index, self._primary_net_key

    @property
    def app_keys(self):
        if not self._app_keys:
            raise Exception("Application keys not ready")
        return self._app_keys

    def primary_app_key_for_bluez(self):
        app_key_index, net_key_index, app_key = self.app_keys[0]
        return net_key_index, app_key_index, app_key

    @property
    def nodes(self):
        return self._nodes

    def _load_key(self, keychain, name):
        if name not in keychain:
            logging.info(f"Generating {name}...")
            keychain[name] = secrets.token_hex(16)
            self._new_keys.add(name)
        try:
            return bytes.fromhex(keychain[name])
        except:
            raise Exception("Invalid device key")

    def _load_key_index(self, keychain, name, fallback=0):
        if name not in keychain:
            keychain[name] = fallback

        try:
            value = keychain[name]
            if isinstance(value, str):
                value = int(value[2:], 16) if value.lower().startswith("0x") else int(value, 10)
            else:
                value = int(value)
        except (TypeError, ValueError):
            raise Exception(f"Invalid {name}")

        if not 0 <= value <= 0xFFF:
            raise Exception(f"Invalid {name}")

        keychain[name] = value
        return value

    def _initialize(self):
        keychain = self._store.get("keychain") or {}
        local = self._store.section("local")
        nodes = self._store.section("nodes")

        # load or set application parameters
        self.address = local.get("address", 1)
        self.iv_index = local.get("iv_index", 5)

        # load or generate keys
        self._dev_key = DeviceKey(self._load_key(keychain, "device_key"))
        self._primary_net_key_index = self._load_key_index(keychain, "network_key_index")
        app_key_index = self._load_key_index(keychain, "app_key_index")
        app_key_bound_net_key_index = self._load_key_index(
            keychain, "app_key_bound_net_key_index", self._primary_net_key_index
        )
        self._primary_net_key = NetworkKey(self._load_key(keychain, "network_key"))
        self._app_keys = [
            # currently just a single application key supported
            (app_key_index, app_key_bound_net_key_index, ApplicationKey(self._load_key(keychain, "app_key"))),
        ]

        # initialize node manager
        self._nodes = NodeManager(nodes, self._config, NODE_TYPES)
        self._remote_nodes = self._store.get("remote_nodes", {}) or {}
        self.diagnostic_monitor = DiagnosticMonitor.from_config(self._config)
        self.btmon_monitor = BtmonMonitor.from_config(self._config)
        self.diagnostic_export = DiagnosticExportServer.from_config(self._config)

        # initialize MQTT messenger
        self._messenger = HassMqttMessenger(self._config, self._nodes, self.diagnostic_monitor)

        # persist changes
        self._store.set("keychain", keychain)
        self._store.persist()

    def _remote_node_imports(self):
        for uuid, info in self._remote_nodes.items():
            if not isinstance(info, dict):
                logging.warning(f"Skipping invalid remote node import for {uuid}: expected mapping")
                continue

            try:
                unicast = int(info["unicast"])
                count = int(info.get("count", 1))
                device_key = DeviceKey(bytes.fromhex(info["device_key"]))
            except (KeyError, TypeError, ValueError):
                logging.warning(f"Skipping invalid remote node import for {uuid}: bad unicast/count/device_key")
                continue

            if count <= 0:
                logging.warning(f"Skipping invalid remote node import for {uuid}: count must be positive")
                continue

            yield uuid, unicast, count, device_key

    async def _import_keys(self):
        logging.info("Importing keys...")

        if self._new_keys & NETWORK_KEY_RELOAD_NAMES:
            # register primary network key as subnet key
            await self.management_interface.import_subnet(self.primary_net_key[0], self.primary_net_key[1])
            logging.info(f"Imported primary net key as subnet key {self.primary_net_key[0]}")

        if "app_key" in self._new_keys:
            # import application key into daemon
            net_key_index, app_key_index, app_key = self.primary_app_key_for_bluez()
            await self.management_interface.import_app_key(net_key_index, app_key_index, app_key)
            logging.info(f"Imported app key {app_key_index} bound to net key {net_key_index}")

        # update application key for client models
        client = self.elements[0][models.GenericOnOffClient]
        await client.bind(self.app_keys[0][0])
        client = self.elements[0][models.LightLightnessClient]
        await client.bind(self.app_keys[0][0])
        client = self.elements[0][models.LightCTLClient]
        await client.bind(self.app_keys[0][0])

        for uuid, unicast, count, device_key in self._remote_node_imports():
            try:
                await self.management_interface.import_remote_node(unicast, count, device_key)
                logging.info(f"Imported remote node {uuid} at {unicast:04} ({count})")
            except Exception:
                logging.exception(f"Failed to import remote node {uuid} at {unicast:04} ({count})")

    async def _try_bind_node(self, node):
        try:
            await node.bind(self)
            logging.info(f"Bound node {node}")
            node.ready.set()
        except:
            logging.exception(f"Failed to bind node {node}")

    def scan_result(self, rssi, data, options):
        MESH_MODULES["scan"]._scan_result(rssi, data, options)

    def request_prov_data(self, count):
        return MESH_MODULES["prov"]._request_prov_data(count)

    def add_node_complete(self, uuid, unicast, count):
        MESH_MODULES["prov"]._add_node_complete(uuid, unicast, count)

    def add_node_failed(self, uuid, reason):
        MESH_MODULES["prov"]._add_node_failed(uuid, reason)

    def shutdown(self, tasks):
        self._messenger.shutdown()

    async def run(self, args):
        async with AsyncExitStack() as stack:
            tasks = await stack.enter_async_context(Tasks())
            tasks.spawn(self.btmon_monitor.run(), "run btmon monitor")
            tasks.spawn(self.diagnostic_export.run(), "run diagnostic export")

            # connect to daemon
            await stack.enter_async_context(self)
            await self.connect()

            # leave network
            if args.leave:
                await self.leave()
                self._nodes.reset()
                self._nodes.persist()
                return

            try:
                # set overall application key
                net_key_index, app_key_index, app_key = self.primary_app_key_for_bluez()
                await self.add_app_key(net_key_index, app_key_index, app_key)
            except:
                logging.exception(f"Failed to set app key {self._app_keys[0][2].bytes.hex()}")

                # try to re-add application key
                net_key_index, app_key_index, app_key = self.primary_app_key_for_bluez()
                await self.delete_app_key(net_key_index, app_key_index)
                await self.add_app_key(net_key_index, app_key_index, app_key)

            # force reloading keys
            if args.reload:
                self._new_keys.add("primary_net_key")
                self._new_keys.add("app_key")

            # configure all keys
            await self._import_keys()
            self.diagnostic_monitor.attach_app(self)

            # run user task if specified
            if "handler" in args:
                await args.handler(args)
                return

            # initialize all nodes
            for node in self._nodes.all():
                tasks.spawn(self._try_bind_node(node), f"bind {node}")

            # start MQTT task
            tasks.spawn(self._messenger.run(self), "run messenger")
            tasks.spawn(self.diagnostic_monitor.run(), "run diagnostic monitor")

            # wait for all tasks
            await tasks.gather()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--leave", action="store_true")
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--basedir", default="..")

    # module specific CLI interfaces
    subparsers = parser.add_subparsers()
    for name, module in MESH_MODULES.items():
        subparser = subparsers.add_parser(name)
        subparser.set_defaults(handler=module.handle_cli)
        module.setup_cli(subparser)

    args = parser.parse_args()

    loop = asyncio.get_event_loop()
    app = MqttGateway(loop, args.basedir)

    with suppress(KeyboardInterrupt):
        loop.run_until_complete(app.run(args))


if __name__ == "__main__":
    main()
