import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path


GATEWAY_DIR = Path(__file__).resolve().parents[1] / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

MODULE_NAMES = [
    "gateway",
    "mesh",
    "mesh.composition",
    "mesh.manager",
    "mesh.node",
    "mesh.nodes",
    "mesh.nodes.generic",
    "mesh.nodes.light",
    "modules",
    "modules.manager",
    "modules.provisioner",
    "modules.scanner",
    "mqtt",
    "mqtt.bridge",
    "mqtt.bridges",
    "mqtt.bridges.light",
    "mqtt.messenger",
    "tools",
    "tools.config",
    "tools.store",
    "tools.tasks",
    "bluetooth_mesh",
    "bluetooth_mesh.application",
    "bluetooth_mesh.crypto",
    "bluetooth_mesh.messages",
    "bluetooth_mesh.messages.config",
    "bluetooth_mesh.messages.generic",
    "bluetooth_mesh.messages.generic.onoff",
    "bluetooth_mesh.messages.generic.light",
    "bluetooth_mesh.messages.generic.light.ctl",
    "bluetooth_mesh.messages.generic.light.lightness",
    "bluetooth_mesh.messages.time",
    "bluetooth_mesh.models",
    "bluetooth_mesh.models.base",
    "asyncio_mqtt",
    "asyncio_mqtt.client",
    "yaml",
]


def install_gateway_stubs():
    yaml = types.ModuleType("yaml")
    yaml.safe_load = lambda stream: {}
    yaml.dump = lambda data, stream: None
    sys.modules["yaml"] = yaml

    asyncio_mqtt = types.ModuleType("asyncio_mqtt")
    asyncio_mqtt_client = types.ModuleType("asyncio_mqtt.client")

    class Client:
        def __init__(self, *args, **kwargs):
            pass

    asyncio_mqtt_client.Client = Client
    asyncio_mqtt_client.MqttError = Exception
    sys.modules["asyncio_mqtt"] = asyncio_mqtt
    sys.modules["asyncio_mqtt.client"] = asyncio_mqtt_client

    bluetooth_mesh = types.ModuleType("bluetooth_mesh")
    application = types.ModuleType("bluetooth_mesh.application")
    crypto = types.ModuleType("bluetooth_mesh.crypto")
    messages = types.ModuleType("bluetooth_mesh.messages")
    config_messages = types.ModuleType("bluetooth_mesh.messages.config")
    generic = types.ModuleType("bluetooth_mesh.messages.generic")
    onoff = types.ModuleType("bluetooth_mesh.messages.generic.onoff")
    light = types.ModuleType("bluetooth_mesh.messages.generic.light")
    ctl = types.ModuleType("bluetooth_mesh.messages.generic.light.ctl")
    lightness = types.ModuleType("bluetooth_mesh.messages.generic.light.lightness")
    time_messages = types.ModuleType("bluetooth_mesh.messages.time")
    models = types.ModuleType("bluetooth_mesh.models")
    models_base = types.ModuleType("bluetooth_mesh.models.base")

    class Application:
        def __init__(self, loop):
            self.loop = loop

    class Element:
        pass

    class Key:
        def __init__(self, value):
            self.bytes = value

    class GATTNamespaceDescriptor:
        MAIN = object()

    class LightCTLOpcode:
        LIGHT_CTL_SET = 0x825D
        LIGHT_CTL_STATUS = 0x825E
        LIGHT_CTL_TEMPERATURE_SET_UNACKNOWLEDGED = 0x8265
        LIGHT_CTL_TEMPERATURE_SET = 0x8264
        LIGHT_CTL_TEMPERATURE_STATUS = 0x8266

    class LightLightnessOpcode:
        LIGHT_LIGHTNESS_SET = 0x824C
        LIGHT_LIGHTNESS_STATUS = 0x824E
        LIGHT_LIGHTNESS_SET_UNACKNOWLEDGED = 0x824D

    class GenericOnOffOpcode:
        GENERIC_ONOFF_SET = 0x8202
        GENERIC_ONOFF_STATUS = 0x8204

    class TimeOpcode:
        TIME_STATUS = 0x5D
        TIME_ROLE_STATUS = 0x8240

    class Model:
        pass

    for name in (
        "ConfigClient",
        "HealthClient",
        "GenericOnOffClient",
        "GenericOnOffServer",
        "LightLightnessClient",
        "LightLightnessServer",
        "LightCTLClient",
        "LightCTLServer",
        "LightCTLTemperatureClient",
    ):
        setattr(models, name, type(name, (), {"MODEL_ID": (None, 0), "OPCODES": set()}))

    application.Application = Application
    application.Element = Element
    crypto.ApplicationKey = Key
    crypto.DeviceKey = Key
    crypto.NetworkKey = Key
    config_messages.ConfigOpcode = type("ConfigOpcode", (), {})
    config_messages.GATTNamespaceDescriptor = GATTNamespaceDescriptor
    config_messages.NodeIdentity = type("NodeIdentity", (), {})
    config_messages.StatusCode = type("StatusCode", (), {})
    onoff.GenericOnOffOpcode = GenericOnOffOpcode
    ctl.LightCTLOpcode = LightCTLOpcode
    lightness.LightLightnessOpcode = LightLightnessOpcode
    time_messages.TimeOpcode = TimeOpcode
    models_base.Model = Model
    bluetooth_mesh.models = models

    sys.modules["bluetooth_mesh"] = bluetooth_mesh
    sys.modules["bluetooth_mesh.application"] = application
    sys.modules["bluetooth_mesh.crypto"] = crypto
    sys.modules["bluetooth_mesh.messages"] = messages
    sys.modules["bluetooth_mesh.messages.config"] = config_messages
    sys.modules["bluetooth_mesh.messages.generic"] = generic
    sys.modules["bluetooth_mesh.messages.generic.onoff"] = onoff
    sys.modules["bluetooth_mesh.messages.generic.light"] = light
    sys.modules["bluetooth_mesh.messages.generic.light.ctl"] = ctl
    sys.modules["bluetooth_mesh.messages.generic.light.lightness"] = lightness
    sys.modules["bluetooth_mesh.messages.time"] = time_messages
    sys.modules["bluetooth_mesh.models"] = models
    sys.modules["bluetooth_mesh.models.base"] = models_base


class FakeManagementInterface:
    def __init__(self):
        self.imported_subnets = []
        self.imported_app_keys = []
        self.imported_remote_nodes = []

    async def import_subnet(self, net_index, net_key):
        self.imported_subnets.append((net_index, net_key))

    async def import_app_key(self, *args):
        self.imported_app_keys.append(args)

    async def import_remote_node(self, unicast, count, device_key):
        self.imported_remote_nodes.append((unicast, count, device_key))


class FakeClient:
    def __init__(self):
        self.bound = []

    async def bind(self, app_key_index):
        self.bound.append(app_key_index)


class FakeGateway:
    def __init__(self, gateway_module, new_keys, remote_nodes=None, net_key_index=0, app_key_index=0):
        self.gateway_module = gateway_module
        self._new_keys = set(new_keys)
        self.management_interface = FakeManagementInterface()
        self._net_key_index = net_key_index
        self._primary_net_key = "net-key"
        self._app_keys = [(app_key_index, net_key_index, "app-key")]
        self._remote_nodes = remote_nodes or {}
        self.clients = {}
        self.elements = {0: {}}

        for client_type in (
            gateway_module.models.GenericOnOffClient,
            gateway_module.models.LightLightnessClient,
            gateway_module.models.LightCTLClient,
        ):
            self.clients[client_type] = FakeClient()
            self.elements[0][client_type] = self.clients[client_type]

    @property
    def primary_net_key(self):
        return self._net_key_index, self._primary_net_key

    @property
    def app_keys(self):
        return self._app_keys

    def _remote_node_imports(self):
        return self.gateway_module.MqttGateway._remote_node_imports(self)

    def primary_app_key_for_bluez(self):
        return self.gateway_module.MqttGateway.primary_app_key_for_bluez(self)


class GatewayKeyImportTest(unittest.TestCase):
    def setUp(self):
        self.saved_modules = {name: sys.modules.get(name) for name in MODULE_NAMES}
        for name in MODULE_NAMES:
            sys.modules.pop(name, None)
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        install_gateway_stubs()
        self.gateway = importlib.import_module("gateway")

    def tearDown(self):
        for name in MODULE_NAMES:
            sys.modules.pop(name, None)
        for name, module in self.saved_modules.items():
            if module is not None:
                sys.modules[name] = module
        self.loop.close()
        asyncio.set_event_loop(None)

    def test_first_run_network_key_marker_imports_subnet(self):
        app = FakeGateway(self.gateway, {"network_key"})

        asyncio.run(self.gateway.MqttGateway._import_keys(app))

        self.assertEqual(app.management_interface.imported_subnets, [(0, "net-key")])

    def test_reload_primary_net_key_marker_imports_subnet(self):
        app = FakeGateway(self.gateway, {"primary_net_key"})

        asyncio.run(self.gateway.MqttGateway._import_keys(app))

        self.assertEqual(app.management_interface.imported_subnets, [(0, "net-key")])

    def test_app_key_marker_imports_app_key_and_binds_clients(self):
        app = FakeGateway(self.gateway, {"app_key"})

        asyncio.run(self.gateway.MqttGateway._import_keys(app))

        self.assertEqual(app.management_interface.imported_app_keys, [(0, 0, "app-key")])
        for client in app.clients.values():
            self.assertEqual(client.bound, [0])

    def test_nonzero_key_indexes_are_used_in_bluez_order(self):
        app = FakeGateway(self.gateway, {"primary_net_key", "app_key"}, net_key_index=7, app_key_index=5)

        asyncio.run(self.gateway.MqttGateway._import_keys(app))

        self.assertEqual(app.management_interface.imported_subnets, [(7, "net-key")])
        self.assertEqual(app.management_interface.imported_app_keys, [(7, 5, "app-key")])
        for client in app.clients.values():
            self.assertEqual(client.bound, [5])

    def test_primary_app_key_for_bluez_swaps_internal_tuple_order(self):
        app = FakeGateway(self.gateway, set(), net_key_index=7, app_key_index=5)

        self.assertEqual(self.gateway.MqttGateway.primary_app_key_for_bluez(app), (7, 5, "app-key"))

    def test_load_key_index_accepts_yaml_int_and_string_forms(self):
        keychain = {"network_key_index": "0x007", "app_key_index": "5"}

        self.assertEqual(self.gateway.MqttGateway._load_key_index(None, keychain, "network_key_index"), 7)
        self.assertEqual(self.gateway.MqttGateway._load_key_index(None, keychain, "app_key_index"), 5)
        self.assertEqual(self.gateway.MqttGateway._load_key_index(None, keychain, "missing_index", 3), 3)
        self.assertEqual(keychain["network_key_index"], 7)
        self.assertEqual(keychain["app_key_index"], 5)
        self.assertEqual(keychain["missing_index"], 3)

    def test_load_key_index_rejects_invalid_values(self):
        with self.assertRaises(Exception):
            self.gateway.MqttGateway._load_key_index(None, {"network_key_index": 4096}, "network_key_index")

    def test_imports_remote_node_device_keys_from_store(self):
        app = FakeGateway(
            self.gateway,
            {"app_key"},
            remote_nodes={
                "00112233-4455-6677-8899-aabbccddeeff": {
                    "unicast": 2,
                    "count": 3,
                    "device_key": "00112233445566778899aabbccddeeff",
                }
            },
        )

        asyncio.run(self.gateway.MqttGateway._import_keys(app))

        self.assertEqual(len(app.management_interface.imported_remote_nodes), 1)
        unicast, count, device_key = app.management_interface.imported_remote_nodes[0]
        self.assertEqual((unicast, count), (2, 3))
        self.assertEqual(device_key.bytes, bytes.fromhex("00112233445566778899aabbccddeeff"))


if __name__ == "__main__":
    unittest.main()
