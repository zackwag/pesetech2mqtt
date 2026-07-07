import asyncio
import importlib.util
import sys
import types
import unittest

from enum import IntEnum
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_bridge_module():
    mqtt_pkg = types.ModuleType("mqtt")
    mqtt_pkg.__path__ = []
    bridge_mod = types.ModuleType("mqtt.bridge")

    class HassMqttBridge:
        def __init__(self, messenger):
            self._messenger = messenger

    bridge_mod.HassMqttBridge = HassMqttBridge

    mesh_pkg = types.ModuleType("mesh")
    mesh_pkg.__path__ = []
    nodes_pkg = types.ModuleType("mesh.nodes")
    nodes_pkg.__path__ = []
    light_mod = types.ModuleType("mesh.nodes.light")

    class Light:
        OnOffProperty = "onoff"
        BrightnessProperty = "brightness"
        TemperatureProperty = "temperature"

    light_mod.Light = Light

    sys.modules["mqtt"] = mqtt_pkg
    sys.modules["mqtt.bridge"] = bridge_mod
    sys.modules["mesh"] = mesh_pkg
    sys.modules["mesh.nodes"] = nodes_pkg
    sys.modules["mesh.nodes.light"] = light_mod

    spec = importlib.util.spec_from_file_location(
        "native_light_bridge_under_test",
        REPO_ROOT / "gateway/mqtt/bridges/light.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_light_node_module():
    mesh_pkg = types.ModuleType("mesh")
    mesh_pkg.__path__ = []
    nodes_pkg = types.ModuleType("mesh.nodes")
    nodes_pkg.__path__ = []
    generic_mod = types.ModuleType("mesh.nodes.generic")

    class FakeConfig:
        def optional(self, _path, fallback=None):
            return fallback

    class Generic:
        def __init__(self):
            self.type = "pesetech_skylight"
            self.uuid = "test-uuid"
            self.unicast = 0x0802
            self.config = FakeConfig()
            self._retained = {}
            self._bound_models = set()
            self._model_addresses = {}

        def _is_model_bound(self, model):
            return model in self._bound_models

        def _model_address(self, model):
            return self._model_addresses[model]

        def notify(self, property, value):
            self._retained[property] = value

        def retained(self, property, fallback):
            return self._retained.get(property, fallback)

        def __str__(self):
            return "test-light"

    generic_mod.Generic = Generic

    class FakeModels:
        class GenericOnOffServer:
            pass

        class GenericOnOffClient:
            pass

        class LightLightnessServer:
            pass

        class LightLightnessClient:
            pass

        class LightCTLServer:
            pass

        class LightCTLClient:
            pass

    bluetooth_mesh_mod = types.ModuleType("bluetooth_mesh")
    bluetooth_mesh_mod.models = FakeModels

    onoff_mod = types.ModuleType("bluetooth_mesh.messages.generic.onoff")
    ctl_mod = types.ModuleType("bluetooth_mesh.messages.generic.light.ctl")
    lightness_mod = types.ModuleType("bluetooth_mesh.messages.generic.light.lightness")

    class GenericOnOffOpcode(IntEnum):
        GENERIC_ONOFF_SET = 0x8202
        GENERIC_ONOFF_SET_UNACKNOWLEDGED = 0x8203
        GENERIC_ONOFF_STATUS = 0x8204

    class LightCTLOpcode(IntEnum):
        LIGHT_CTL_SET = 0x825E
        LIGHT_CTL_SET_UNACKNOWLEDGED = 0x825F
        LIGHT_CTL_STATUS = 0x8260
        LIGHT_CTL_TEMPERATURE_SET = 0x8264
        LIGHT_CTL_TEMPERATURE_SET_UNACKNOWLEDGED = 0x8265
        LIGHT_CTL_TEMPERATURE_STATUS = 0x8266

    class LightLightnessOpcode(IntEnum):
        LIGHT_LIGHTNESS_SET = 0x824C
        LIGHT_LIGHTNESS_SET_UNACKNOWLEDGED = 0x824D
        LIGHT_LIGHTNESS_STATUS = 0x824E

    onoff_mod.GenericOnOffOpcode = GenericOnOffOpcode
    ctl_mod.LightCTLOpcode = LightCTLOpcode
    lightness_mod.LightLightnessOpcode = LightLightnessOpcode

    for name, module in {
        "mesh": mesh_pkg,
        "mesh.nodes": nodes_pkg,
        "mesh.nodes.generic": generic_mod,
        "bluetooth_mesh": bluetooth_mesh_mod,
        "bluetooth_mesh.messages": types.ModuleType("bluetooth_mesh.messages"),
        "bluetooth_mesh.messages.generic": types.ModuleType("bluetooth_mesh.messages.generic"),
        "bluetooth_mesh.messages.generic.light": types.ModuleType("bluetooth_mesh.messages.generic.light"),
        "bluetooth_mesh.messages.generic.onoff": onoff_mod,
        "bluetooth_mesh.messages.generic.light.ctl": ctl_mod,
        "bluetooth_mesh.messages.generic.light.lightness": lightness_mod,
    }.items():
        sys.modules[name] = module

    spec = importlib.util.spec_from_file_location(
        "mesh.nodes.light",
        REPO_ROOT / "gateway/mesh/nodes/light.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, FakeModels


BRIDGE_MODULE = load_bridge_module()
NODE_MODULE, FAKE_MODELS = load_light_node_module()


class FakeConfig:
    def __init__(self, values=None):
        self._values = values or {}

    def optional(self, path, fallback=None):
        return self._values.get(path, fallback)

    def require(self, path):
        return self._values[path]


class FakeMessenger:
    topic = "mqtt_mesh"

    def __init__(self, diagnostic_monitor=None):
        self.published = []
        self.diagnostic_monitor = diagnostic_monitor

    def node_topic(self, component, node):
        return f"homeassistant/{component}/mqtt_mesh/{node.config.require('id')}"

    async def publish(self, component, node, topic, message, **kwargs):
        self.published.append((component, node, topic, message, kwargs))


class FakeBridgeNode:
    def __init__(self):
        self.config = FakeConfig({"id": "skylight_a", "name": "Skylight A"})
        self.uuid = "uuid-a"
        self.calls = []
        self.readbacks = []
        self._retained = {}

    def supports(self, property):
        return property in {
            BRIDGE_MODULE.Light.OnOffProperty,
            BRIDGE_MODULE.Light.BrightnessProperty,
            BRIDGE_MODULE.Light.TemperatureProperty,
        }

    def retained(self, property, fallback):
        return self._retained.get(property, fallback)

    async def turn_on(self, **kwargs):
        self.calls.append(("turn_on", kwargs))

    async def turn_off(self, **kwargs):
        self.calls.append(("turn_off", kwargs))

    async def set_brightness(self, brightness, **kwargs):
        self.calls.append(("set_brightness", brightness, kwargs))

    async def set_mireds(self, color_temp, **kwargs):
        self.calls.append(("set_mireds", color_temp, kwargs))

    async def set_brightness_mireds(self, brightness, color_temp, **kwargs):
        self.calls.append(("set_brightness_mireds", brightness, color_temp, kwargs))

    def schedule_standard_readback(self, transition_time=None, diagnostic_command_id=None):
        self.readbacks.append(transition_time)


class FakeClient:
    def __init__(self):
        self.sent = []
        self.repeat_calls = []
        self.query_calls = []
        self.expected = []
        self.ack_after_sends = 1
        self.query_timeout = False
        self.status_message = None

    def tid(self):
        return 42

    def _default_status_message(self, opcode):
        key = opcode.name.lower()
        if self.status_message is not None:
            return self.status_message
        return {key: {}}

    def expect_app(self, source, app_index, destination, opcode, params):
        future = asyncio.Future()
        self.expected.append(
            {
                "source": source,
                "app_index": app_index,
                "destination": destination,
                "opcode": opcode,
                "params": params,
                "future": future,
            }
        )
        return future

    async def send_app(self, destination, app_index, opcode, params):
        self.sent.append(
            {
                "destination": destination,
                "app_index": app_index,
                "opcode": opcode,
                "params": params,
            }
        )

    async def query(self, request, status, send_interval=0.075, timeout=10.0):
        self.query_calls.append(
            {
                "send_interval": send_interval,
                "timeout": timeout,
            }
        )
        status_opcode = self.expected[-1]["opcode"]
        while not status.done():
            await request()
            if self.query_timeout:
                raise TimeoutError("quiet")
            if len(self.sent) >= self.ack_after_sends:
                status.set_result(self._default_status_message(status_opcode))
            else:
                await asyncio.sleep(0)
        return status.result()

    async def repeat(self, request, retransmissions=10, send_interval=0.075):
        self.repeat_calls.append(
            {
                "retransmissions": retransmissions,
                "send_interval": send_interval,
            }
        )
        for _index in range(retransmissions):
            await request()


class FakeApp:
    def __init__(self, clients, diagnostic_monitor=None):
        self.elements = [clients]
        self.app_keys = [(0, b"")]
        self.diagnostic_monitor = diagnostic_monitor


class FakeDiagnosticMonitor:
    enabled = True

    def __init__(self):
        self.commands = []
        self.routes = []
        self.ack_writes = []
        self.unack_writes = []
        self.readbacks = []

    def command_id(self):
        return "cmd123"

    def record_mqtt_light_command(self, command_id, node, topic, payload):
        self.commands.append((command_id, node, topic, payload))

    def record_mqtt_light_route(self, command_id, node, route, **details):
        self.routes.append((command_id, node, route, details))

    def record_ack_write(self, **kwargs):
        self.ack_writes.append(kwargs)

    def record_unack_write(self, **kwargs):
        self.unack_writes.append(kwargs)

    def record_readback(self, **kwargs):
        self.readbacks.append(kwargs)


class NativeLightBridgeTest(unittest.TestCase):
    def test_pesetech_discovery_exposes_color_temperature(self):
        messenger = FakeMessenger()
        bridge = BRIDGE_MODULE.PesetechSkylightBridge(messenger)
        node = FakeBridgeNode()

        asyncio.run(bridge.config(node))

        config = messenger.published[0][3]
        self.assertEqual(config["brightness_scale"], 65280)
        self.assertEqual(config["supported_color_modes"], ["color_temp"])
        self.assertEqual(config["min_mireds"], 100)
        self.assertEqual(config["max_mireds"], 556)
        self.assertEqual(config["device"]["manufacturer"], "Pesetech/Lepu")

    def test_mqtt_set_passes_valid_transition(self):
        bridge = BRIDGE_MODULE.PesetechSkylightBridge(FakeMessenger())
        node = FakeBridgeNode()

        asyncio.run(bridge._mqtt_set(node, {"state": "ON", "brightness": 12345, "transition": "10"}))

        self.assertEqual(node.calls, [("set_brightness", 12345, {"confirm": False, "transition_time": 10.0})])
        self.assertEqual(node.readbacks, [10.0])

    def test_mqtt_set_ignores_invalid_transition(self):
        bridge = BRIDGE_MODULE.PesetechSkylightBridge(FakeMessenger())
        node = FakeBridgeNode()

        asyncio.run(bridge._mqtt_set(node, {"state": "OFF", "transition": -1}))

        self.assertEqual(node.calls, [("turn_off", {"confirm": False, "transition_time": None})])
        self.assertEqual(node.readbacks, [None])

    def test_combined_brightness_and_color_temp_uses_single_setter_for_transition(self):
        bridge = BRIDGE_MODULE.PesetechSkylightBridge(FakeMessenger())
        node = FakeBridgeNode()

        asyncio.run(
            bridge._mqtt_set(
                node,
                {"state": "ON", "brightness": 30000, "color_temp": 250, "transition": 10},
            )
        )

        self.assertEqual(
            node.calls,
            [("set_brightness_mireds", 30000, 250, {"confirm": False, "transition_time": 10.0})],
        )
        self.assertEqual(node.readbacks, [10.0])

    def test_combined_brightness_and_color_temp_without_transition_uses_single_setter(self):
        bridge = BRIDGE_MODULE.PesetechSkylightBridge(FakeMessenger())
        node = FakeBridgeNode()

        asyncio.run(bridge._mqtt_set(node, {"state": "ON", "brightness": 30000, "color_temp": 250}))

        self.assertEqual(
            node.calls,
            [("set_brightness_mireds", 30000, 250, {"confirm": False, "transition_time": None})],
        )
        self.assertEqual(node.readbacks, [None])

    def test_monitor_records_mqtt_command_and_passes_command_id(self):
        monitor = FakeDiagnosticMonitor()
        bridge = BRIDGE_MODULE.PesetechSkylightBridge(FakeMessenger(diagnostic_monitor=monitor))
        node = FakeBridgeNode()

        asyncio.run(bridge._mqtt_set(node, {"state": "OFF"}, topic="homeassistant/light/mqtt_mesh/skylight_a/set"))

        self.assertEqual(
            monitor.commands,
            [("cmd123", node, "homeassistant/light/mqtt_mesh/skylight_a/set", {"state": "OFF"})],
        )
        self.assertEqual(
            monitor.routes,
            [("cmd123", node, "turn_off", {"state": "OFF", "transition": None})],
        )
        self.assertEqual(
            node.calls,
            [("turn_off", {"confirm": False, "transition_time": None, "diagnostic_command_id": "cmd123"})],
        )


class NativeLightNodeTest(unittest.TestCase):
    def make_node(self, bound, addresses, diagnostic_monitor=None):
        NODE_MODULE.Light._confirm_read_lock = None
        node = NODE_MODULE.Light()
        node._bound_models = set(bound)
        node._model_addresses = dict(addresses)
        clients = {
            FAKE_MODELS.GenericOnOffClient: FakeClient(),
            FAKE_MODELS.LightLightnessClient: FakeClient(),
            FAKE_MODELS.LightCTLClient: FakeClient(),
        }
        node._app = FakeApp(clients, diagnostic_monitor=diagnostic_monitor)
        return node, clients

    def test_lightness_set_uses_ack_without_transition(self):
        node, clients = self.make_node(
            [FAKE_MODELS.LightLightnessServer],
            {FAKE_MODELS.LightLightnessServer: 0x0802},
        )
        client = clients[FAKE_MODELS.LightLightnessClient]
        client.status_message = {"light_lightness_status": {"present_lightness": 32768}}

        asyncio.run(node.set_lightness_simple(32768))

        self.assertEqual(len(client.sent), 1)
        self.assertEqual(client.query_calls, [{"send_interval": 0.075, "timeout": 10.0}])
        self.assertEqual(client.expected[0]["opcode"], NODE_MODULE.LightLightnessOpcode.LIGHT_LIGHTNESS_STATUS)
        self.assertEqual(client.sent[0]["opcode"], NODE_MODULE.LightLightnessOpcode.LIGHT_LIGHTNESS_SET)
        params = client.sent[0]["params"]
        self.assertEqual(params, {"lightness": 32768, "tid": 42})

    def test_lightness_set_includes_transition_and_stops_after_late_ack(self):
        node, clients = self.make_node(
            [FAKE_MODELS.LightLightnessServer],
            {FAKE_MODELS.LightLightnessServer: 0x0802},
        )
        client = clients[FAKE_MODELS.LightLightnessClient]
        client.ack_after_sends = 3
        client.status_message = {"light_lightness_status": {"target_lightness": 32768}}

        asyncio.run(node.set_lightness_simple(32768, transition_time=10))

        self.assertEqual(len(client.sent), 3)
        self.assertEqual(client.query_calls, [{"send_interval": 0.075, "timeout": 10.0}])
        params = client.sent[0]["params"]
        self.assertEqual(params, {"lightness": 32768, "tid": 42, "transition_time": 10.0, "delay": 0.0})

    def test_ctl_temperature_set_uses_ack_with_transition(self):
        node, clients = self.make_node(
            [NODE_MODULE.LightCTLTemperatureServer],
            {NODE_MODULE.LightCTLTemperatureServer: 0x0803},
        )
        client = clients[FAKE_MODELS.LightCTLClient]
        client.status_message = {"light_ctl_temperature_status": {"target_ctl_temperature": 800}}

        asyncio.run(node.set_ctl_temperature(800, transition_time=10))

        self.assertEqual(len(client.sent), 1)
        self.assertEqual(client.query_calls, [{"send_interval": 0.075, "timeout": 10.0}])
        sent = client.sent[0]
        self.assertEqual(sent["destination"], 0x0803)
        self.assertEqual(sent["opcode"], NODE_MODULE.LightCTLOpcode.LIGHT_CTL_TEMPERATURE_SET)
        self.assertEqual(
            sent["params"],
            {"ctl_temperature": 800, "ctl_delta_uv": 0, "tid": 42, "transition_time": 10.0, "delay": 0.0},
        )

    def test_pesetech_cct_conversion_matches_app_range(self):
        node, _clients = self.make_node([], {})

        self.assertEqual(node._mireds_to_ctl_temperature(556), 800)
        self.assertEqual(node._mireds_to_ctl_temperature(100), 20000)
        self.assertEqual(node._ctl_temperature_to_mireds(800), 556)
        self.assertEqual(node._ctl_temperature_to_mireds(20000), 100)

    def test_combined_brightness_cct_uses_ctl_set_with_transition(self):
        node, clients = self.make_node(
            [FAKE_MODELS.LightCTLServer],
            {FAKE_MODELS.LightCTLServer: 0x0802},
        )
        client = clients[FAKE_MODELS.LightCTLClient]
        client.status_message = {
            "light_ctl_status": {
                "target_ctl_lightness": 32768,
                "target_ctl_temperature": 800,
            }
        }

        asyncio.run(node.set_brightness_mireds(32768, 556, confirm=False, transition_time=10))

        self.assertEqual(len(client.sent), 1)
        sent = client.sent[0]
        self.assertEqual(sent["destination"], 0x0802)
        self.assertEqual(sent["opcode"], NODE_MODULE.LightCTLOpcode.LIGHT_CTL_SET)
        self.assertEqual(
            sent["params"],
            {
                "ctl_temperature": 800,
                "ctl_lightness": 32768,
                "ctl_delta_uv": 0,
                "tid": 42,
                "transition_time": 10.0,
                "delay": 0.0,
            },
        )

    def test_onoff_set_uses_ack(self):
        node, clients = self.make_node(
            [FAKE_MODELS.GenericOnOffServer],
            {FAKE_MODELS.GenericOnOffServer: 0x0802},
        )
        client = clients[FAKE_MODELS.GenericOnOffClient]
        client.status_message = {"generic_onoff_status": {"present_onoff": 1}}

        asyncio.run(node.set_onoff(True))

        self.assertEqual(len(client.sent), 1)
        self.assertEqual(client.query_calls, [{"send_interval": 0.075, "timeout": 10.0}])
        self.assertEqual(client.expected[0]["opcode"], NODE_MODULE.GenericOnOffOpcode.GENERIC_ONOFF_STATUS)
        self.assertEqual(client.sent[0]["opcode"], NODE_MODULE.GenericOnOffOpcode.GENERIC_ONOFF_SET)
        self.assertEqual(client.sent[0]["params"], {"onoff": 1, "tid": 42})

    def test_ack_timeout_returns_none_without_raising(self):
        node, clients = self.make_node(
            [FAKE_MODELS.GenericOnOffServer],
            {FAKE_MODELS.GenericOnOffServer: 0x0802},
        )
        client = clients[FAKE_MODELS.GenericOnOffClient]
        client.query_timeout = True

        with self.assertLogs(level="WARNING") as logs:
            result = asyncio.run(node.set_onoff(True))

        self.assertIsNone(result)
        self.assertIn("failed acknowledged on/off", "\n".join(logs.output))

    def test_ack_status_mismatch_logs_warning_but_returns_payload(self):
        node, clients = self.make_node(
            [FAKE_MODELS.LightLightnessServer],
            {FAKE_MODELS.LightLightnessServer: 0x0802},
        )
        client = clients[FAKE_MODELS.LightLightnessClient]
        client.status_message = {"light_lightness_status": {"present_lightness": 1}}

        with self.assertLogs(level="WARNING") as logs:
            result = asyncio.run(node.set_lightness_simple(32768))

        self.assertEqual(result, {"present_lightness": 1})
        self.assertIn("did not prove target", "\n".join(logs.output))

    def test_ack_write_records_monitor_event(self):
        monitor = FakeDiagnosticMonitor()
        node, clients = self.make_node(
            [FAKE_MODELS.LightLightnessServer],
            {FAKE_MODELS.LightLightnessServer: 0x0802},
            diagnostic_monitor=monitor,
        )
        client = clients[FAKE_MODELS.LightLightnessClient]
        client.ack_after_sends = 2
        client.status_message = {"light_lightness_status": {"target_lightness": 32768}}

        asyncio.run(node.set_lightness_simple(32768, diagnostic_command_id="cmd123"))

        self.assertEqual(len(monitor.ack_writes), 1)
        event = monitor.ack_writes[0]
        self.assertEqual(event["command_id"], "cmd123")
        self.assertEqual(event["label"], "lightness")
        self.assertEqual(event["address"], 0x0802)
        self.assertEqual(event["attempts"], 2)
        self.assertEqual(event["outcome"], "verified")

    def test_vendor_runtime_brightness_repeats_ten_times(self):
        node, clients = self.make_node(
            [FAKE_MODELS.LightLightnessServer],
            {FAKE_MODELS.LightLightnessServer: 0x0802},
        )

        asyncio.run(node.set_pesetech_runtime_brightness(32768))

        client = clients[FAKE_MODELS.LightLightnessClient]
        self.assertEqual(len(client.sent), 10)
        self.assertEqual(client.repeat_calls, [{"retransmissions": 10, "send_interval": 0.075}])
        self.assertEqual(client.sent[0]["opcode"], NODE_MODULE.PESETECH_VENDOR_OPCODE)

    def test_confirm_read_succeeds_after_earlier_failures(self):
        node, _clients = self.make_node([], {})
        calls = {"count": 0}

        async def read(timeout=None):
            calls["count"] += 1
            if calls["count"] < 3:
                raise TimeoutError("quiet")
            return {"ok": True}

        result = asyncio.run(node._confirm_read("test", read, attempts=10, retry_delay=0))

        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls["count"], 3)

    def test_confirm_read_fails_only_after_all_attempts_fail(self):
        node, _clients = self.make_node([], {})
        calls = {"count": 0}

        async def read(timeout=None):
            calls["count"] += 1
            raise TimeoutError("quiet")

        result = asyncio.run(node._confirm_read("test", read, attempts=10, retry_delay=0))

        self.assertIsNone(result)
        self.assertEqual(calls["count"], 10)


if __name__ == "__main__":
    unittest.main()
