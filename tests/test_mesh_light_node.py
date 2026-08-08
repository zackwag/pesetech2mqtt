import asyncio
import enum
import sys
import types
import unittest
import uuid
from pathlib import Path


GATEWAY_DIR = Path(__file__).resolve().parents[1] / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

yaml = types.ModuleType("yaml")
yaml.safe_load = lambda stream: {}
sys.modules["yaml"] = yaml

asyncio_mqtt = types.ModuleType("asyncio_mqtt")
asyncio_mqtt_client = types.ModuleType("asyncio_mqtt.client")


class StubMqttClient:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.filtered_topics = []
        self.subscriptions = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def filtered_messages(self, topic):
        self.filtered_topics.append(topic)
        return topic

    async def subscribe(self, topic):
        self.subscriptions.append(topic)

    async def publish(self, topic, payload, **kwargs):
        pass


class StubMqttError(Exception):
    pass


asyncio_mqtt_client.Client = StubMqttClient
asyncio_mqtt_client.MqttError = StubMqttError
sys.modules["asyncio_mqtt"] = asyncio_mqtt
sys.modules["asyncio_mqtt.client"] = asyncio_mqtt_client


STUB_MODULES = [
    "yaml",
    "asyncio_mqtt",
    "asyncio_mqtt.client",
    "bluetooth_mesh",
    "bluetooth_mesh.models",
    "bluetooth_mesh.messages",
    "bluetooth_mesh.messages.generic",
    "bluetooth_mesh.messages.generic.onoff",
    "bluetooth_mesh.messages.generic.light",
    "bluetooth_mesh.messages.generic.light.ctl",
    "bluetooth_mesh.messages.generic.light.lightness",
]


def tearDownModule():
    for name in STUB_MODULES:
        sys.modules.pop(name, None)


def install_bluetooth_mesh_stubs():
    bluetooth_mesh = types.ModuleType("bluetooth_mesh")
    models = types.ModuleType("bluetooth_mesh.models")

    class ConfigClient:
        pass

    class GenericOnOffClient:
        pass

    class GenericOnOffServer:
        MODEL_ID = (None, 0x1000)

    class LightLightnessClient:
        pass

    class LightLightnessServer:
        MODEL_ID = (None, 0x1300)

    class LightCTLClient:
        pass

    class LightCTLServer:
        MODEL_ID = (None, 0x1303)

    models.ConfigClient = ConfigClient
    models.GenericOnOffClient = GenericOnOffClient
    models.GenericOnOffServer = GenericOnOffServer
    models.LightLightnessClient = LightLightnessClient
    models.LightLightnessServer = LightLightnessServer
    models.LightCTLClient = LightCTLClient
    models.LightCTLServer = LightCTLServer
    bluetooth_mesh.models = models

    class GenericOnOffOpcode(enum.IntEnum):
        GENERIC_ONOFF_GET = 0x8201
        GENERIC_ONOFF_SET = 0x8202
        GENERIC_ONOFF_SET_UNACKNOWLEDGED = 0x8203
        GENERIC_ONOFF_STATUS = 0x8204

    class LightCTLOpcode(enum.IntEnum):
        LIGHT_CTL_GET = 0x825D
        LIGHT_CTL_SET = 0x825E
        LIGHT_CTL_STATUS = 0x8260
        LIGHT_CTL_TEMPERATURE_GET = 0x8261
        LIGHT_CTL_TEMPERATURE_SET = 0x8264
        LIGHT_CTL_TEMPERATURE_STATUS = 0x8266
        LIGHT_CTL_TEMPERATURE_SET_UNACKNOWLEDGED = 0x8265

    class LightLightnessOpcode(enum.IntEnum):
        LIGHT_LIGHTNESS_GET = 0x824B
        LIGHT_LIGHTNESS_SET = 0x824C
        LIGHT_LIGHTNESS_STATUS = 0x824E
        LIGHT_LIGHTNESS_SET_UNACKNOWLEDGED = 0x824D

    messages = types.ModuleType("bluetooth_mesh.messages")
    generic = types.ModuleType("bluetooth_mesh.messages.generic")
    generic.__path__ = []
    onoff = types.ModuleType("bluetooth_mesh.messages.generic.onoff")
    light = types.ModuleType("bluetooth_mesh.messages.generic.light")
    light.__path__ = []
    ctl = types.ModuleType("bluetooth_mesh.messages.generic.light.ctl")
    lightness = types.ModuleType("bluetooth_mesh.messages.generic.light.lightness")
    onoff.GenericOnOffOpcode = GenericOnOffOpcode
    ctl.LightCTLOpcode = LightCTLOpcode
    lightness.LightLightnessOpcode = LightLightnessOpcode

    sys.modules["bluetooth_mesh"] = bluetooth_mesh
    sys.modules["bluetooth_mesh.models"] = models
    sys.modules["bluetooth_mesh.messages"] = messages
    sys.modules["bluetooth_mesh.messages.generic"] = generic
    sys.modules["bluetooth_mesh.messages.generic.onoff"] = onoff
    sys.modules["bluetooth_mesh.messages.generic.light"] = light
    sys.modules["bluetooth_mesh.messages.generic.light.ctl"] = ctl
    sys.modules["bluetooth_mesh.messages.generic.light.lightness"] = lightness

    return models, GenericOnOffOpcode, LightCTLOpcode, LightLightnessOpcode


MODELS, GENERIC_ONOFF_OPCODE, LIGHT_CTL_OPCODE, LIGHT_LIGHTNESS_OPCODE = install_bluetooth_mesh_stubs()

from mesh.nodes.light import PESETECH_VENDOR_OPCODE, Light, LightCTLTemperatureServer
from mqtt.bridge import HassMqttBridge
from mqtt.bridges.light import PesetechSkylightBridge
from mqtt.messenger import HassMqttMessenger
from tools import Config


class FakeConfigClient:
    def __init__(self, composition, fail_model_ids=None):
        self.composition = composition
        self.fail_model_ids = set(fail_model_ids or [])
        self.binds = []

    async def get_composition_data(self, nodes, net_index, timeout):
        return {nodes[0]: {"zero": self.composition}}

    async def bind_app_key(self, destination, net_index, element_address, app_key_index, model):
        if model.MODEL_ID[1] in self.fail_model_ids:
            raise RuntimeError(f"bind failed for {model.MODEL_ID[1]:04x}")

        self.binds.append(
            {
                "destination": destination,
                "net_index": net_index,
                "element_address": element_address,
                "app_key_index": app_key_index,
                "model": model,
            }
        )


class FakeOnOffClient:
    def __init__(self, fail_get=False):
        self.fail_get = fail_get
        self.sent = []
        self._status = None

    async def get_light_status(self, addresses, app_index, **kwargs):
        if self.fail_get:
            raise RuntimeError("on/off status read failed")
        return {addresses[0]: {"present_onoff": True}}

    async def set_onoff_unack(self, address, app_index, onoff, **kwargs):
        self.last_set = (address, app_index, onoff, kwargs)

    def tid(self):
        return 42

    def expect_app(self, address, *, app_index, destination, opcode, params):
        self._status = asyncio.get_running_loop().create_future()
        self._status_opcode = opcode
        return self._status

    async def send_app(self, address, app_index, opcode, params):
        self.sent.append({"address": address, "app_index": app_index, "opcode": opcode, "params": params})
        if opcode == GENERIC_ONOFF_OPCODE.GENERIC_ONOFF_GET:
            if self.fail_get:
                raise RuntimeError("on/off status read failed")
            if self._status is not None and not self._status.done():
                self._status.set_result({self._status_opcode.name.lower(): {"present_onoff": True}})
            return

        kwargs = {}
        if "transition_time" in params:
            kwargs["transition_time"] = params["transition_time"]
        self.last_set = (address, app_index, bool(params["onoff"]), kwargs)
        if self._status is not None and not self._status.done():
            self._status.set_result(
                {self._status_opcode.name.lower(): {"present_onoff": params["onoff"], "target_onoff": params["onoff"]}}
            )

    async def query(self, request, status, *, send_interval, timeout):
        await request()
        return await status


class FakeLightnessClient:
    def __init__(self, fail_get=False, status=None):
        self.fail_get = fail_get
        self.status = status or {"present_lightness": 32000}
        self.sets = []
        self.sent = []
        self._status = None

    async def get_lightness(self, addresses, app_index, **kwargs):
        if self.fail_get:
            raise RuntimeError("lightness status read failed")
        return {addresses[0]: dict(self.status)}

    async def set_lightness_unack(self, address, app_index, lightness, **kwargs):
        self.last_set = (address, app_index, lightness, kwargs)
        self.sets.append(self.last_set)

    def tid(self):
        return 42

    async def send_app(self, address, app_index, opcode, params):
        self.sent.append(
            {
                "address": address,
                "app_index": app_index,
                "opcode": opcode,
                "params": params,
            }
        )
        if opcode == LIGHT_LIGHTNESS_OPCODE.LIGHT_LIGHTNESS_GET:
            if self.fail_get:
                raise RuntimeError("lightness status read failed")
            if self._status is not None and not self._status.done():
                self._status.set_result({self._status_opcode.name.lower(): dict(self.status)})
            return

        if isinstance(params, dict) and "lightness" in params:
            kwargs = {}
            if "transition_time" in params:
                kwargs["transition_time"] = params["transition_time"]
            self.last_set = (address, app_index, params["lightness"], kwargs)
            self.sets.append(self.last_set)
        if isinstance(params, dict) and self._status is not None and not self._status.done():
            self._status.set_result(
                {
                    self._status_opcode.name.lower(): {
                        "present_lightness": params["lightness"],
                        "target_lightness": params["lightness"],
                    }
                }
            )

    async def repeat(self, request, retransmissions, send_interval):
        await request()

    def expect_app(self, address, *, app_index, destination, opcode, params):
        self._status = asyncio.get_running_loop().create_future()
        self._status_opcode = opcode
        return self._status

    async def query(self, request, status, *, send_interval, timeout):
        await request()
        return await status


class FakeCtlClient:
    def __init__(self, fail_get=False, status=None):
        self.fail_get = fail_get
        self.status = status or {"present_ctl_temperature": 20000}
        self.sent = []
        self.gets = []
        self.ctl_sets = []
        self._status = None

    async def get_ctl(self, addresses, app_index, **kwargs):
        if self.fail_get:
            raise RuntimeError("CTL status read failed")
        self.gets.append((list(addresses), app_index))
        return {addresses[0]: dict(self.status)}

    def tid(self):
        return 42

    async def send_app(self, address, app_index, opcode, params):
        self.sent.append(
            {
                "address": address,
                "app_index": app_index,
                "opcode": opcode,
                "params": params,
            }
        )
        if opcode == LIGHT_CTL_OPCODE.LIGHT_CTL_TEMPERATURE_GET:
            if self.fail_get:
                raise RuntimeError("CTL status read failed")
            self.gets.append(([address], app_index))
            if self._status is not None and not self._status.done():
                self._status.set_result({self._status_opcode.name.lower(): dict(self.status)})
            return

        if "ctl_temperature" in params and "ctl_lightness" in params:
            kwargs = {}
            if "transition_time" in params:
                kwargs["transition_time"] = params["transition_time"]
            self.ctl_sets.append((address, app_index, params["ctl_temperature"], params["ctl_lightness"], kwargs))
            self.last_ctl_kwargs = {
                "ctl_temperature": params["ctl_temperature"],
                "ctl_lightness": params["ctl_lightness"],
            }
        if self._status is not None and not self._status.done():
            payload = {}
            if "ctl_temperature" in params:
                payload["present_ctl_temperature"] = params["ctl_temperature"]
                payload["target_ctl_temperature"] = params["ctl_temperature"]
            if "ctl_lightness" in params:
                payload["present_ctl_lightness"] = params["ctl_lightness"]
                payload["target_ctl_lightness"] = params["ctl_lightness"]
            self._status.set_result({self._status_opcode.name.lower(): payload})

    async def repeat(self, request, retransmissions, send_interval):
        await request()

    def expect_app(self, address, *, app_index, destination, opcode, params):
        self._status = asyncio.get_running_loop().create_future()
        self._status_opcode = opcode
        return self._status

    async def query(self, request, status, *, send_interval, timeout):
        await request()
        return await status

    async def set_ctl_unack(self, address, app_index, *, ctl_temperature, ctl_lightness, **kwargs):
        self.ctl_sets.append((address, app_index, ctl_temperature, ctl_lightness, kwargs))
        self.last_ctl_kwargs = {
            "ctl_temperature": ctl_temperature,
            "ctl_lightness": ctl_lightness,
        }


class FakeApp:
    def __init__(
        self,
        composition,
        fail_model_ids=None,
        fail_state_reads=None,
        net_key_index=0,
        app_key_index=0,
        lightness_status=None,
        ctl_status=None,
    ):
        fail_state_reads = set(fail_state_reads or [])
        self.config_client = FakeConfigClient(composition, fail_model_ids=fail_model_ids)
        self.onoff_client = FakeOnOffClient(fail_get="onoff" in fail_state_reads)
        self.lightness_client = FakeLightnessClient(
            fail_get="lightness" in fail_state_reads,
            status=lightness_status,
        )
        self.ctl_client = FakeCtlClient(fail_get="ctl" in fail_state_reads, status=ctl_status)
        self._primary_net_key = (net_key_index, b"net-key")
        self.app_keys = [(app_key_index, net_key_index, b"app-key")]
        self.elements = {
            0: {
                MODELS.ConfigClient: self.config_client,
                MODELS.GenericOnOffClient: self.onoff_client,
                MODELS.LightLightnessClient: self.lightness_client,
                MODELS.LightCTLClient: self.ctl_client,
            }
        }

    @property
    def primary_net_key(self):
        return self._primary_net_key


class FakeMessenger:
    def __init__(self):
        self.published = []
        self.topic = "mqtt_mesh"

    def node_topic(self, component, node):
        return f"homeassistant/{component}/mqtt_mesh/{node.config.require('id')}"

    async def publish(self, component, node, topic, message, **kwargs):
        self.published.append((component, topic, message, kwargs))


class FakeBridgeNode:
    config = Config(config={"id": "skylight", "name": "Skylight"})
    uuid = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")

    def supports(self, property_name):
        return property_name in {Light.BrightnessProperty, Light.TemperatureProperty}

    def retained(self, property_name, fallback):
        return fallback


class FakeRetainedBridgeNode(FakeBridgeNode):
    def __init__(self, retained):
        self._retained = retained

    def retained(self, property_name, fallback):
        return self._retained.get(property_name, fallback)


class FakeEntityIdBridgeNode(FakeBridgeNode):
    config = Config(config={"id": "skylight", "name": "Skylight", "default_entity_id": "light.sunroom_sky"})


class FakeCommandNode:
    config = Config(config={"id": "skylight", "name": "Skylight"})

    def __init__(self):
        self.calls = []

    async def turn_on(self, **kwargs):
        self.calls.append(("turn_on", kwargs))

    async def turn_off(self, **kwargs):
        self.calls.append(("turn_off", kwargs))

    async def set_brightness(self, brightness, **kwargs):
        self.calls.append(("set_brightness", brightness, kwargs))

    async def set_mireds(self, mireds, **kwargs):
        self.calls.append(("set_mireds", mireds, kwargs))

    async def set_brightness_mireds(self, brightness, mireds, **kwargs):
        self.calls.append(("set_brightness_mireds", brightness, mireds, kwargs))


class EmptyAsyncMessages:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class ListAsyncMessages:
    def __init__(self, messages):
        self.messages = list(messages)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)


class FakeMessageContext:
    def __init__(self, messages=None):
        self.messages = messages

    async def __aenter__(self):
        if self.messages is None:
            return EmptyAsyncMessages()
        return ListAsyncMessages(self.messages)

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeListenMessenger:
    def __init__(self, messages=None):
        self.filtered_calls = []
        self.subscriptions = []
        self.messages = messages

    def filtered_messages(self, component, node, topic="#"):
        self.filtered_calls.append((component, node, topic))
        return FakeMessageContext(self.messages)

    async def subscribe(self, component, node, topic):
        self.subscriptions.append((component, node, topic))


class FakeReadyNode:
    def __init__(self):
        self.ready = asyncio.Event()
        self.ready.set()
        self.subscribers = []

    def subscribe(self, subscriber, resend=True):
        self.subscribers.append((subscriber, resend))


class TestBridge(HassMqttBridge):
    @property
    def component(self):
        return "light"

    async def config(self, node):
        pass


class RecordingBridge(TestBridge):
    def __init__(self, messenger):
        super().__init__(messenger)
        self.payloads = []

    async def _mqtt_set(self, node, payload):
        if payload.get("raise"):
            raise RuntimeError("simulated command failure")
        self.payloads.append(payload)


class MeshLightNodeTest(unittest.TestCase):
    def setUp(self):
        self._event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._event_loop)

    def tearDown(self):
        asyncio.set_event_loop(None)
        self._event_loop.close()

    def test_pesetech_temperature_commands_target_temperature_element(self):
        composition = {
            "elements": [
                {
                    "sig_models": [
                        {"model_id": MODELS.GenericOnOffServer.MODEL_ID[1]},
                        {"model_id": MODELS.LightLightnessServer.MODEL_ID[1]},
                        {"model_id": MODELS.LightCTLServer.MODEL_ID[1]},
                    ],
                    "vendor_models": [],
                },
                {"sig_models": [], "vendor_models": []},
                {
                    "sig_models": [{"model_id": LightCTLTemperatureServer.MODEL_ID[1]}],
                    "vendor_models": [],
                },
            ]
        }
        app = FakeApp(composition)

        async def run_scenario():
            node = Light(uuid.uuid4(), type="pesetech_skylight", unicast=0x120, count=3)
            await node.bind(app)
            await node.set_mireds(1250)
            return node

        node = asyncio.run(run_scenario())

        bound_addresses = {
            bind["model"].MODEL_ID[1]: bind["element_address"] for bind in app.config_client.binds
        }
        self.assertEqual(bound_addresses[MODELS.GenericOnOffServer.MODEL_ID[1]], 0x120)
        self.assertEqual(bound_addresses[MODELS.LightLightnessServer.MODEL_ID[1]], 0x120)
        self.assertEqual(bound_addresses[MODELS.LightCTLServer.MODEL_ID[1]], 0x120)
        self.assertEqual(bound_addresses[LightCTLTemperatureServer.MODEL_ID[1]], 0x122)

        self.assertEqual(app.ctl_client.gets[-1], ([0x120], 0))
        self.assertEqual(node.retained(Light.TemperatureProperty, None), 556)
        self.assertEqual(app.ctl_client.sent[-1]["address"], 0x122)
        self.assertEqual(app.ctl_client.sent[-1]["opcode"], LIGHT_CTL_OPCODE.LIGHT_CTL_TEMPERATURE_SET)
        self.assertEqual(app.ctl_client.sent[-1]["params"], {"ctl_temperature": 800, "ctl_delta_uv": 0, "tid": 42})

    def test_initial_ctl_status_retains_temperature_and_lightness(self):
        composition = {
            "elements": [
                {
                    "sig_models": [
                        {"model_id": MODELS.LightCTLServer.MODEL_ID[1]},
                    ],
                    "vendor_models": [],
                },
            ]
        }
        app = FakeApp(
            composition,
            ctl_status={"present_ctl_temperature": 4000, "present_ctl_lightness": 12345},
        )

        async def run_scenario():
            node = Light(uuid.uuid4(), type="pesetech_skylight", unicast=0x120, count=1)
            await node.bind(app)
            return node

        node = asyncio.run(run_scenario())

        self.assertEqual(node.retained(Light.TemperatureProperty, None), 316)
        self.assertEqual(node.retained(Light.BrightnessProperty, None), 12345)
        self.assertTrue(node.retained(Light.OnOffProperty, False))

    def test_initial_ctl_status_ignores_invalid_zero_temperature(self):
        composition = {
            "elements": [
                {
                    "sig_models": [
                        {"model_id": MODELS.LightCTLServer.MODEL_ID[1]},
                    ],
                    "vendor_models": [],
                },
            ]
        }
        app = FakeApp(
            composition,
            ctl_status={"present_ctl_temperature": 0, "present_ctl_lightness": 0},
        )

        async def run_scenario():
            node = Light(uuid.uuid4(), type="pesetech_skylight", unicast=0x120, count=1)
            await node.bind(app)
            return node

        with self.assertLogs(level="WARNING") as logs:
            node = asyncio.run(run_scenario())

        self.assertIn("ignored invalid CTL temperature 0", "\n".join(logs.output))
        self.assertIsNone(node.retained(Light.TemperatureProperty, None))
        self.assertEqual(node.retained(Light.BrightnessProperty, None), 0)
        self.assertFalse(node.retained(Light.OnOffProperty, True))

    def test_initial_lightness_status_ignores_invalid_negative_value(self):
        composition = {
            "elements": [
                {
                    "sig_models": [
                        {"model_id": MODELS.LightLightnessServer.MODEL_ID[1]},
                    ],
                    "vendor_models": [],
                },
            ]
        }
        app = FakeApp(composition, lightness_status={"present_lightness": -1})

        async def run_scenario():
            node = Light(uuid.uuid4(), type="pesetech_skylight", unicast=0x120, count=1)
            await node.bind(app)
            return node

        with self.assertLogs(level="WARNING") as logs:
            node = asyncio.run(run_scenario())

        self.assertIn("ignored invalid brightness -1", "\n".join(logs.output))
        self.assertIsNone(node.retained(Light.BrightnessProperty, None))
        self.assertIsNone(node.retained(Light.OnOffProperty, None))

    def test_invalid_lightness_command_is_not_sent(self):
        composition = {
            "elements": [
                {
                    "sig_models": [
                        {"model_id": MODELS.LightLightnessServer.MODEL_ID[1]},
                    ],
                    "vendor_models": [],
                },
            ]
        }
        app = FakeApp(composition)

        async def run_scenario():
            node = Light(uuid.uuid4(), type="pesetech_skylight", unicast=0x120, count=1)
            await node.bind(app)
            app.lightness_client.sets.clear()
            app.lightness_client.sent.clear()
            await node.set_brightness("not-a-number")
            return node

        with self.assertLogs(level="WARNING") as logs:
            asyncio.run(run_scenario())

        self.assertIn("ignored invalid brightness 'not-a-number'", "\n".join(logs.output))
        self.assertEqual(app.lightness_client.sets, [])
        self.assertEqual(app.lightness_client.sent, [])

    def test_pesetech_brightness_uses_simple_lightness_set(self):
        composition = {
            "elements": [
                {
                    "sig_models": [
                        {"model_id": MODELS.LightLightnessServer.MODEL_ID[1]},
                    ],
                    "vendor_models": [],
                },
            ]
        }
        app = FakeApp(composition)

        async def run_scenario():
            node = Light(uuid.uuid4(), type="pesetech_skylight", unicast=0x120, count=1)
            await node.bind(app)
            app.lightness_client.sets.clear()
            app.lightness_client.sent.clear()
            await node.set_brightness(32640)
            return node

        node = asyncio.run(run_scenario())

        self.assertEqual(app.lightness_client.sets, [(0x120, 0, 32640, {})])
        self.assertEqual(
            app.lightness_client.sent,
            [
                {
                    "address": 0x120,
                    "app_index": 0,
                    "opcode": LIGHT_LIGHTNESS_OPCODE.LIGHT_LIGHTNESS_SET,
                    "params": {"lightness": 32640, "tid": 42},
                },
                {
                    "address": 0x120,
                    "app_index": 0,
                    "opcode": PESETECH_VENDOR_OPCODE,
                    "params": bytes.fromhex("a0ff0000000000807f0000000000000000000000000000"),
                },
            ],
        )
        self.assertEqual(node.retained(Light.BrightnessProperty, None), 32640)

    def test_invalid_zero_mired_command_is_ignored(self):
        composition = {
            "elements": [
                {
                    "sig_models": [
                        {"model_id": MODELS.LightCTLServer.MODEL_ID[1]},
                    ],
                    "vendor_models": [],
                },
            ]
        }
        app = FakeApp(composition)

        async def run_scenario():
            node = Light(uuid.uuid4(), type="pesetech_skylight", unicast=0x120, count=1)
            await node.bind(app)
            app.ctl_client.sent.clear()
            await node.set_mireds(0)
            return node

        with self.assertLogs(level="WARNING") as logs:
            asyncio.run(run_scenario())

        self.assertIn("ignored invalid mired color temperature 0", "\n".join(logs.output))
        self.assertEqual(app.ctl_client.sent, [])

    def test_nonzero_mesh_key_indexes_are_used_for_config_and_access_messages(self):
        composition = {
            "elements": [
                {
                    "sig_models": [
                        {"model_id": MODELS.LightCTLServer.MODEL_ID[1]},
                    ],
                    "vendor_models": [],
                },
                {
                    "sig_models": [{"model_id": LightCTLTemperatureServer.MODEL_ID[1]}],
                    "vendor_models": [],
                },
            ]
        }
        app = FakeApp(composition, net_key_index=7, app_key_index=5)

        async def run_scenario():
            node = Light(uuid.uuid4(), type="pesetech_skylight", unicast=0x120, count=2)
            await node.bind(app)
            await node.set_mireds(1250)

        asyncio.run(run_scenario())

        self.assertEqual({bind["net_index"] for bind in app.config_client.binds}, {7})
        self.assertEqual({bind["app_key_index"] for bind in app.config_client.binds}, {5})
        self.assertEqual(app.ctl_client.gets[-1], ([0x120], 5))
        self.assertEqual(app.ctl_client.sent[-1]["app_index"], 5)

    def test_failed_ctl_bind_does_not_skip_temperature_element(self):
        composition = {
            "elements": [
                {
                    "sig_models": [
                        {"model_id": MODELS.GenericOnOffServer.MODEL_ID[1]},
                        {"model_id": MODELS.LightLightnessServer.MODEL_ID[1]},
                        {"model_id": MODELS.LightCTLServer.MODEL_ID[1]},
                    ],
                    "vendor_models": [],
                },
                {"sig_models": [], "vendor_models": []},
                {
                    "sig_models": [{"model_id": LightCTLTemperatureServer.MODEL_ID[1]}],
                    "vendor_models": [],
                },
            ]
        }
        app = FakeApp(composition, fail_model_ids={MODELS.LightCTLServer.MODEL_ID[1]})

        async def run_scenario():
            node = Light(uuid.uuid4(), type="pesetech_skylight", unicast=0x120, count=3)
            await node.bind(app)
            await node.set_mireds(1250)
            return node

        with self.assertLogs(level="ERROR") as logs:
            node = asyncio.run(run_scenario())

        bound_addresses = {
            bind["model"].MODEL_ID[1]: bind["element_address"] for bind in app.config_client.binds
        }
        self.assertIn("failed to bind", "\n".join(logs.output))
        self.assertEqual(bound_addresses[MODELS.GenericOnOffServer.MODEL_ID[1]], 0x120)
        self.assertEqual(bound_addresses[MODELS.LightLightnessServer.MODEL_ID[1]], 0x120)
        self.assertNotIn(MODELS.LightCTLServer.MODEL_ID[1], bound_addresses)
        self.assertEqual(bound_addresses[LightCTLTemperatureServer.MODEL_ID[1]], 0x122)

        self.assertTrue(node.supports(Light.OnOffProperty))
        self.assertTrue(node.supports(Light.BrightnessProperty))
        self.assertTrue(node.supports(Light.TemperatureProperty))
        self.assertEqual(app.ctl_client.gets, [])
        self.assertEqual(app.ctl_client.sent[-1]["address"], 0x122)

    def test_initial_state_read_failures_do_not_prevent_commands(self):
        composition = {
            "elements": [
                {
                    "sig_models": [
                        {"model_id": MODELS.GenericOnOffServer.MODEL_ID[1]},
                        {"model_id": MODELS.LightLightnessServer.MODEL_ID[1]},
                        {"model_id": MODELS.LightCTLServer.MODEL_ID[1]},
                    ],
                    "vendor_models": [],
                },
                {"sig_models": [], "vendor_models": []},
                {
                    "sig_models": [{"model_id": LightCTLTemperatureServer.MODEL_ID[1]}],
                    "vendor_models": [],
                },
            ]
        }
        app = FakeApp(composition, fail_state_reads={"onoff", "lightness", "ctl"})

        async def run_scenario():
            node = Light(
                uuid.uuid4(),
                type="pesetech_skylight",
                unicast=0x120,
                count=3,
                config=Config(config={"brightness_scale": 65280}),
            )
            await node.bind(app)
            await node.turn_on()
            await node.set_brightness(32640)
            await node.set_mireds(1250)
            return node

        with self.assertLogs(level="WARNING") as logs:
            node = asyncio.run(run_scenario())

        joined_logs = "\n".join(logs.output)
        self.assertIn("failed initial on/off state read", joined_logs)
        self.assertIn("failed initial lightness state read", joined_logs)
        self.assertIn("failed initial CTL state read", joined_logs)

        self.assertTrue(node.supports(Light.OnOffProperty))
        self.assertTrue(node.supports(Light.BrightnessProperty))
        self.assertTrue(node.supports(Light.TemperatureProperty))
        self.assertEqual(app.onoff_client.last_set, (0x120, 0, True, {}))
        self.assertEqual(app.lightness_client.sets, [(0x120, 0, 32640, {})])
        lightness_set = next(message for message in app.lightness_client.sent if "lightness" in message["params"])
        self.assertEqual(lightness_set["address"], 0x120)
        self.assertEqual(lightness_set["params"], {"lightness": 32640, "tid": 42})
        self.assertEqual(app.ctl_client.gets, [])
        self.assertEqual(app.ctl_client.sent[-1]["address"], 0x122)

    def test_turn_on_off_falls_back_to_lightness_when_onoff_bind_fails(self):
        composition = {
            "elements": [
                {
                    "sig_models": [
                        {"model_id": MODELS.GenericOnOffServer.MODEL_ID[1]},
                        {"model_id": MODELS.LightLightnessServer.MODEL_ID[1]},
                    ],
                    "vendor_models": [],
                },
            ]
        }
        app = FakeApp(composition, fail_model_ids={MODELS.GenericOnOffServer.MODEL_ID[1]})

        async def run_scenario():
            node = Light(uuid.uuid4(), type="pesetech_skylight", unicast=0x120, count=1)
            await node.bind(app)
            await node.turn_on()
            await node.turn_off()
            return node

        with self.assertLogs(level="ERROR"):
            node = asyncio.run(run_scenario())

        self.assertTrue(node.supports(Light.OnOffProperty))
        self.assertTrue(node.supports(Light.BrightnessProperty))
        self.assertEqual(
            app.lightness_client.sets,
            [
                (0x120, 0, 32000, {}),
                (0x120, 0, 0, {}),
            ],
        )
        self.assertEqual(node.retained(Light.BrightnessProperty, None), 0)
        self.assertFalse(node.retained(Light.OnOffProperty, True))

    def test_lightness_fallback_turn_on_restores_last_nonzero_brightness_after_off(self):
        composition = {
            "elements": [
                {
                    "sig_models": [
                        {"model_id": MODELS.GenericOnOffServer.MODEL_ID[1]},
                        {"model_id": MODELS.LightLightnessServer.MODEL_ID[1]},
                    ],
                    "vendor_models": [],
                },
            ]
        }
        app = FakeApp(composition, fail_model_ids={MODELS.GenericOnOffServer.MODEL_ID[1]})

        async def run_scenario():
            node = Light(uuid.uuid4(), type="pesetech_skylight", unicast=0x120, count=1)
            await node.bind(app)
            await node.turn_off()
            await node.turn_on()
            return node

        with self.assertLogs(level="ERROR"):
            node = asyncio.run(run_scenario())

        self.assertEqual(
            app.lightness_client.sets,
            [
                (0x120, 0, 0, {}),
                (0x120, 0, 32000, {}),
            ],
        )
        self.assertEqual(node.retained(Light.BrightnessProperty, None), 32000)
        self.assertTrue(node.retained(Light.OnOffProperty, False))

    def test_turn_on_off_falls_back_to_ctl_brightness_when_onoff_bind_fails(self):
        composition = {
            "elements": [
                {
                    "sig_models": [
                        {"model_id": MODELS.GenericOnOffServer.MODEL_ID[1]},
                        {"model_id": MODELS.LightCTLServer.MODEL_ID[1]},
                    ],
                    "vendor_models": [],
                },
            ]
        }
        app = FakeApp(composition, fail_model_ids={MODELS.GenericOnOffServer.MODEL_ID[1]})

        async def run_scenario():
            node = Light(
                uuid.uuid4(),
                type="pesetech_skylight",
                unicast=0x120,
                count=1,
                config=Config(config={"brightness_scale": 65280}),
            )
            await node.bind(app)
            await node.turn_on()
            await node.turn_off()
            return node

        with self.assertLogs(level="ERROR"):
            node = asyncio.run(run_scenario())

        self.assertTrue(node.supports(Light.BrightnessProperty))
        self.assertTrue(node.supports(Light.TemperatureProperty))
        self.assertEqual(app.ctl_client.ctl_sets[0], (0x120, 0, 20000, 65280, {}))
        self.assertEqual(app.ctl_client.ctl_sets[1], (0x120, 0, 20000, 0, {}))
        self.assertEqual(app.ctl_client.last_ctl_kwargs, {"ctl_temperature": 20000, "ctl_lightness": 0})
        self.assertEqual(node.retained(Light.BrightnessProperty, None), 0)
        self.assertFalse(node.retained(Light.OnOffProperty, True))

    def test_ctl_fallback_turn_on_restores_default_brightness_after_off(self):
        composition = {
            "elements": [
                {
                    "sig_models": [
                        {"model_id": MODELS.GenericOnOffServer.MODEL_ID[1]},
                        {"model_id": MODELS.LightCTLServer.MODEL_ID[1]},
                    ],
                    "vendor_models": [],
                },
            ]
        }
        app = FakeApp(composition, fail_model_ids={MODELS.GenericOnOffServer.MODEL_ID[1]})

        async def run_scenario():
            node = Light(
                uuid.uuid4(),
                type="pesetech_skylight",
                unicast=0x120,
                count=1,
                config=Config(config={"brightness_scale": 65280}),
            )
            await node.bind(app)
            await node.turn_off()
            await node.turn_on()
            return node

        with self.assertLogs(level="ERROR"):
            node = asyncio.run(run_scenario())

        self.assertEqual(app.ctl_client.ctl_sets[0], (0x120, 0, 20000, 0, {}))
        self.assertEqual(app.ctl_client.ctl_sets[1], (0x120, 0, 20000, 65280, {}))
        self.assertEqual(node.retained(Light.BrightnessProperty, None), 65280)
        self.assertTrue(node.retained(Light.OnOffProperty, False))

    def test_ctl_fallback_turn_on_uses_default_temperature_after_invalid_retained_mireds(self):
        composition = {
            "elements": [
                {
                    "sig_models": [
                        {"model_id": MODELS.GenericOnOffServer.MODEL_ID[1]},
                        {"model_id": MODELS.LightCTLServer.MODEL_ID[1]},
                    ],
                    "vendor_models": [],
                },
            ]
        }
        app = FakeApp(composition, fail_model_ids={MODELS.GenericOnOffServer.MODEL_ID[1]})

        async def run_scenario():
            node = Light(
                uuid.uuid4(),
                type="pesetech_skylight",
                unicast=0x120,
                count=1,
                config=Config(config={"brightness_scale": 65280}),
            )
            await node.bind(app)
            node.notify(Light.TemperatureProperty, 0)
            await node.turn_on()
            return node

        with self.assertLogs(level="WARNING") as logs:
            node = asyncio.run(run_scenario())

        self.assertIn("ignored invalid mired color temperature 0", "\n".join(logs.output))
        self.assertEqual(app.ctl_client.ctl_sets[-1], (0x120, 0, 800, 65280, {}))
        self.assertEqual(node.retained(Light.BrightnessProperty, None), 65280)
        self.assertTrue(node.retained(Light.OnOffProperty, False))

    def test_temperature_notification_stays_off_after_ctl_fallback_turn_off(self):
        composition = {
            "elements": [
                {
                    "sig_models": [
                        {"model_id": MODELS.GenericOnOffServer.MODEL_ID[1]},
                        {"model_id": MODELS.LightCTLServer.MODEL_ID[1]},
                    ],
                    "vendor_models": [],
                },
            ]
        }
        app = FakeApp(composition, fail_model_ids={MODELS.GenericOnOffServer.MODEL_ID[1]})
        messenger = FakeMessenger()
        bridge = PesetechSkylightBridge(messenger)

        async def run_scenario():
            node = Light(
                uuid.uuid4(),
                type="pesetech_skylight",
                unicast=0x120,
                count=1,
                config=Config(config={"id": "skylight", "brightness_scale": 65280}),
            )
            await node.bind(app)
            await node.turn_off()
            await bridge._notify_temperature(node, 1250)
            return node

        with self.assertLogs(level="ERROR"):
            node = asyncio.run(run_scenario())

        self.assertFalse(node.retained(Light.OnOffProperty, True))
        _, topic, message, kwargs = messenger.published[-1]
        self.assertEqual(topic, "state")
        self.assertEqual(message, {"state": "OFF", "color_mode": "color_temp"})
        self.assertTrue(kwargs["retain"])

    def test_light_bind_raises_when_no_light_models_bind(self):
        composition = {
            "elements": [
                {
                    "sig_models": [
                        {"model_id": MODELS.GenericOnOffServer.MODEL_ID[1]},
                        {"model_id": MODELS.LightLightnessServer.MODEL_ID[1]},
                        {"model_id": MODELS.LightCTLServer.MODEL_ID[1]},
                    ],
                    "vendor_models": [],
                },
                {"sig_models": [{"model_id": LightCTLTemperatureServer.MODEL_ID[1]}], "vendor_models": []},
            ]
        }
        app = FakeApp(
            composition,
            fail_model_ids={
                MODELS.GenericOnOffServer.MODEL_ID[1],
                MODELS.LightLightnessServer.MODEL_ID[1],
                MODELS.LightCTLServer.MODEL_ID[1],
                LightCTLTemperatureServer.MODEL_ID[1],
            },
        )

        async def run_scenario():
            node = Light(uuid.uuid4(), type="pesetech_skylight", unicast=0x120, count=2)
            await node.bind(app)

        with self.assertLogs(level="ERROR"):
            with self.assertRaisesRegex(RuntimeError, "no bound light models"):
                asyncio.run(run_scenario())

    def test_temperature_only_node_skips_generic_ctl_state_read(self):
        composition = {
            "elements": [
                {"sig_models": [], "vendor_models": []},
                {
                    "sig_models": [{"model_id": LightCTLTemperatureServer.MODEL_ID[1]}],
                    "vendor_models": [],
                },
            ]
        }
        app = FakeApp(composition)

        async def run_scenario():
            node = Light(uuid.uuid4(), type="pesetech_skylight", unicast=0x120, count=2)
            await node.bind(app)
            await node.set_mireds(1250)
            return node

        node = asyncio.run(run_scenario())

        self.assertTrue(node.supports(Light.TemperatureProperty))
        self.assertEqual(app.ctl_client.gets, [])
        self.assertEqual(app.ctl_client.sent[-1]["address"], 0x121)

    def test_pesetech_discovery_uses_raw_ctl_ranges(self):
        messenger = FakeMessenger()
        bridge = PesetechSkylightBridge(messenger)

        asyncio.run(bridge.config(FakeBridgeNode()))

        _, topic, message, kwargs = messenger.published[0]
        self.assertEqual(topic, "config")
        self.assertEqual(message["brightness_scale"], 65280)
        self.assertEqual(message["min_mireds"], 100)
        self.assertEqual(message["max_mireds"], 556)
        self.assertEqual(message["supported_color_modes"], ["color_temp"])
        self.assertEqual(message["unique_id"], "mqtt_mesh_skylight")
        self.assertEqual(message["default_entity_id"], "light.skylight")
        self.assertNotIn("object_id", message)
        self.assertEqual(
            message["device"],
            {
                "identifiers": ["bluetooth_mesh_00112233-4455-6677-8899-aabbccddeeff"],
                "name": "Skylight",
                "manufacturer": "Pesetech/Lepu",
                "model": "Artificial Skylight",
            },
        )
        self.assertEqual(message["origin"]["name"], "pesetech-home-assistant")
        self.assertNotIn("color_mode", message)
        self.assertTrue(kwargs["retain"])

        asyncio.run(bridge._state(FakeBridgeNode(), True))

        _, topic, message, kwargs = messenger.published[1]
        self.assertEqual(topic, "state")
        self.assertEqual(message["color_mode"], "color_temp")
        self.assertEqual(message["color_temp"], 100)
        self.assertTrue(kwargs["retain"])

        asyncio.run(bridge._state(FakeBridgeNode(), False))

        _, topic, message, kwargs = messenger.published[2]
        self.assertEqual(topic, "state")
        self.assertEqual(message, {"state": "OFF", "color_mode": "color_temp"})
        self.assertTrue(kwargs["retain"])

    def test_discovery_allows_default_entity_id_override(self):
        messenger = FakeMessenger()
        bridge = PesetechSkylightBridge(messenger)

        asyncio.run(bridge.config(FakeEntityIdBridgeNode()))

        _, _, message, _ = messenger.published[0]
        self.assertEqual(message["default_entity_id"], "light.sunroom_sky")

    def test_temperature_notification_publishes_matching_state(self):
        messenger = FakeMessenger()
        bridge = PesetechSkylightBridge(messenger)
        node = FakeRetainedBridgeNode(
            {
                Light.OnOffProperty: True,
                Light.BrightnessProperty: 32640,
                Light.TemperatureProperty: 1250,
            }
        )

        asyncio.run(bridge._notify_temperature(node, 1250))

        _, topic, message, kwargs = messenger.published[0]
        self.assertEqual(topic, "state")
        self.assertEqual(
            message,
            {
                "state": "ON",
                "brightness": 32640,
                "color_mode": "color_temp",
                "color_temp": 1250,
            },
        )
        self.assertTrue(kwargs["retain"])

    def test_mqtt_node_id_config_controls_discovery_topic(self):
        config = Config(config={"mqtt": {"broker": "localhost", "node_id": "pesetech_mesh"}})
        messenger = HassMqttMessenger(config, nodes=[])

        self.assertEqual(messenger.discovery_prefix, "homeassistant")
        self.assertEqual(messenger.topic, "pesetech_mesh")
        self.assertEqual(messenger.node_topic("light", "skylight"), "homeassistant/light/pesetech_mesh/skylight")
        self.assertEqual(messenger.client.kwargs["port"], 1883)

    def test_mqtt_port_config_controls_gateway_client_port(self):
        config = Config(config={"mqtt": {"broker": "localhost", "port": 1884, "node_id": "pesetech_mesh"}})
        messenger = HassMqttMessenger(config, nodes=[])

        self.assertEqual(messenger.client.kwargs["port"], 1884)

    def test_mqtt_discovery_prefix_config_controls_discovery_topic(self):
        config = Config(
            config={"mqtt": {"broker": "localhost", "discovery_prefix": "ha_discovery", "node_id": "pesetech_mesh"}}
        )
        messenger = HassMqttMessenger(config, nodes=[])

        self.assertEqual(messenger.discovery_prefix, "ha_discovery")
        self.assertEqual(messenger.node_topic("light", "skylight"), "ha_discovery/light/pesetech_mesh/skylight")

    def test_mqtt_topic_config_overrides_node_id(self):
        config = Config(
            config={"mqtt": {"broker": "localhost", "node_id": "legacy_mesh", "topic": "explicit_mesh"}}
        )
        messenger = HassMqttMessenger(config, nodes=[])

        self.assertEqual(messenger.topic, "explicit_mesh")

    def test_filtered_set_messages_use_only_command_topic(self):
        config = Config(config={"mqtt": {"broker": "localhost", "node_id": "pesetech_mesh"}})
        messenger = HassMqttMessenger(config, nodes=[])

        topic = messenger.filtered_messages("light", "skylight", "set")

        self.assertEqual(topic, "homeassistant/light/pesetech_mesh/skylight/set")
        self.assertEqual(messenger.command_topic("light", "skylight"), "homeassistant/light/pesetech_mesh/skylight/set")

    def test_bridge_listen_filters_only_set_topic(self):
        async def run_scenario():
            messenger = FakeListenMessenger()
            bridge = TestBridge(messenger)
            node = FakeReadyNode()
            await bridge.listen(node)
            return messenger, node

        messenger, node = asyncio.run(run_scenario())
        self.assertEqual(messenger.filtered_calls, [("light", node, "set")])
        self.assertEqual(messenger.subscriptions, [("light", node, "set")])

    def test_messenger_subscribe_uses_exact_command_topic(self):
        config = Config(
            config={"mqtt": {"broker": "localhost", "discovery_prefix": "ha_discovery", "node_id": "pesetech_mesh"}}
        )
        messenger = HassMqttMessenger(config, nodes=[])

        asyncio.run(messenger.subscribe("light", "skylight", "set"))

        self.assertEqual(messenger.client.subscriptions, ["ha_discovery/light/pesetech_mesh/skylight/set"])

    def test_bridge_listen_continues_after_bad_payload_and_handler_error(self):
        async def run_scenario():
            messages = [
                types.SimpleNamespace(topic="homeassistant/light/mqtt_mesh/skylight/set", payload=b"{"),
                types.SimpleNamespace(
                    topic="homeassistant/light/mqtt_mesh/skylight/set",
                    payload=b'{"brightness": 32640}',
                ),
                types.SimpleNamespace(
                    topic="homeassistant/light/mqtt_mesh/skylight/set",
                    payload=b'{"raise": true}',
                ),
                types.SimpleNamespace(
                    topic="homeassistant/light/mqtt_mesh/skylight/set",
                    payload=b'{"color_temp": 1250}',
                ),
            ]
            messenger = FakeListenMessenger(messages)
            bridge = RecordingBridge(messenger)
            node = FakeReadyNode()
            await bridge.listen(node)
            return bridge.payloads

        with self.assertLogs(level="ERROR") as logs:
            payloads = asyncio.run(run_scenario())

        self.assertEqual(payloads, [{"brightness": 32640}, {"color_temp": 1250}])
        self.assertIn("Invalid MQTT JSON payload", "\n".join(logs.output))
        self.assertIn("Failed to handle MQTT command set", "\n".join(logs.output))

    def test_mqtt_set_turns_on_before_applying_pesetech_values(self):
        bridge = PesetechSkylightBridge(FakeMessenger())
        node = FakeCommandNode()

        asyncio.run(bridge._mqtt_set(node, {"state": "ON", "brightness": 32640, "color_temp": 1250}))

        self.assertEqual(
            node.calls,
            [
                ("set_brightness_mireds", 32640, 556, {"confirm": False, "transition_time": None}),
            ],
        )

    def test_mqtt_set_is_case_insensitive_for_state_and_off_short_circuits(self):
        bridge = PesetechSkylightBridge(FakeMessenger())
        node = FakeCommandNode()

        asyncio.run(bridge._mqtt_set(node, {"state": "off", "brightness": 32640, "color_temp": 1250}))

        self.assertEqual(node.calls, [("turn_off", {"confirm": False, "transition_time": None})])

    def test_mqtt_set_without_state_applies_values_only(self):
        bridge = PesetechSkylightBridge(FakeMessenger())
        node = FakeCommandNode()

        asyncio.run(bridge._mqtt_set(node, {"brightness": 48960, "color_temp": 96}))

        self.assertEqual(
            node.calls,
            [("set_brightness_mireds", 48960, 100, {"confirm": False, "transition_time": None})],
        )

    def test_mqtt_set_clamps_pesetech_values_to_discovery_ranges(self):
        bridge = PesetechSkylightBridge(FakeMessenger())
        node = FakeCommandNode()

        asyncio.run(bridge._mqtt_set(node, {"brightness": 99999, "color_temp": 2000}))
        asyncio.run(bridge._mqtt_set(node, {"brightness": -1, "color_temp": 1}))

        self.assertEqual(
            node.calls,
            [
                ("set_brightness_mireds", 65280, 556, {"confirm": False, "transition_time": None}),
                ("set_brightness_mireds", 0, 100, {"confirm": False, "transition_time": None}),
            ],
        )

    def test_mqtt_set_accepts_numeric_strings_for_manual_mqtt_payloads(self):
        bridge = PesetechSkylightBridge(FakeMessenger())
        node = FakeCommandNode()

        asyncio.run(bridge._mqtt_set(node, {"brightness": "32640", "color_temp": "1250"}))

        self.assertEqual(
            node.calls,
            [("set_brightness_mireds", 32640, 556, {"confirm": False, "transition_time": None})],
        )

    def test_mqtt_set_ignores_invalid_numeric_payload_values(self):
        bridge = PesetechSkylightBridge(FakeMessenger())
        node = FakeCommandNode()

        with self.assertLogs(level="WARNING") as logs:
            asyncio.run(bridge._mqtt_set(node, {"brightness": "not-a-number", "color_temp": None}))
            asyncio.run(bridge._mqtt_set(node, {"brightness": True, "color_temp": False}))

        self.assertEqual(node.calls, [])
        log_text = "\n".join(logs.output)
        self.assertIn("Ignoring invalid MQTT brightness value 'not-a-number'", log_text)
        self.assertIn("Ignoring invalid MQTT color_temp value None", log_text)
        self.assertIn("Ignoring invalid MQTT brightness value True", log_text)
        self.assertIn("Ignoring invalid MQTT color_temp value False", log_text)


if __name__ == "__main__":
    unittest.main()
