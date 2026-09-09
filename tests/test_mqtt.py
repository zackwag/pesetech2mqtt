import json
import unittest
from types import SimpleNamespace
from uuid import UUID

from support import install_stubs

install_stubs()

from app import mqtt
from app.skylight import BRIGHTNESS, ONOFF, TEMPERATURE


class MessageStream:
    def __init__(self, messages):
        self.messages = list(messages)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)


class FakeMqttClient:
    messages = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.published = []
        self.subscriptions = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def filtered_messages(self, topic):
        return MessageStream(self.messages)

    async def subscribe(self, topic):
        self.subscriptions.append(topic)

    async def publish(self, topic, payload, **kwargs):
        self.published.append((topic, json.loads(payload), kwargs))


class FakeNode:
    def __init__(self, client=None, fail=False):
        self.uuid = UUID("00112233-4455-6677-8899-aabbccddeeff")
        self.id = "skylight_a"
        self.name = "Skylight A"
        self.default_entity_id = "light.skylight_a"
        self.state = {ONOFF: True, BRIGHTNESS: 65280, TEMPERATURE: 100}
        self.calls = []
        self.client = client
        self.fail = fail

    def __str__(self):
        return self.name

    def retained(self, key, fallback):
        return self.state.get(key, fallback)

    def set_desired(self, onoff=None, brightness=None, temperature=None):
        if onoff is not None:
            self.state[ONOFF] = onoff
        if brightness is not None:
            self.state[BRIGHTNESS] = brightness
            self.state[ONOFF] = brightness > 0
        if temperature is not None:
            self.state[TEMPERATURE] = temperature

    def state_payload(self):
        return {
            "state": "ON" if self.state[ONOFF] else "OFF",
            "color_mode": "color_temp",
            **(
                {"brightness": self.state[BRIGHTNESS], "color_temp": self.state[TEMPERATURE]}
                if self.state[ONOFF]
                else {}
            ),
        }

    async def turn_on(self, transition_time=None):
        self.calls.append(("on", transition_time))

    async def turn_off(self, transition_time=None):
        self.calls.append(("off", transition_time))
        if self.client is not None:
            self.published_before_command = bool(self.client.published)
        if self.fail:
            raise TimeoutError("no acknowledgement")

    async def set_brightness(self, value, transition_time=None):
        self.calls.append(("brightness", value, transition_time))

    async def set_mireds(self, value, transition_time=None):
        self.calls.append(("temperature", value, transition_time))

    async def set_brightness_mireds(self, brightness, temperature, transition_time=None):
        self.calls.append(("combined", brightness, temperature, transition_time))

    @staticmethod
    def readback_delay(_transition):
        return 100

    async def read_state(self):
        return {}


class MqttTest(unittest.IsolatedAsyncioTestCase):
    def test_command_validation(self):
        self.assertEqual(
            mqtt.parse_command(b'{"state":"on","brightness":123,"color_temp":200,"transition":4.5}'),
            {"state": "ON", "brightness": 123, "color_temp": 200, "transition": 4.5},
        )
        for payload in (b"no", b"[]", b'{"state":"maybe"}', b'{"brightness":70000}', b'{"transition":1}'):
            with self.subTest(payload=payload):
                with self.assertRaises(mqtt.InvalidMqttCommand):
                    mqtt.parse_command(payload)

    async def test_discovery_keeps_stable_topics_and_identifiers(self):
        client = FakeMqttClient()
        bridge = mqtt.PesetechMqttLightBridge(client, FakeNode())

        await bridge.publish_discovery()

        topic, payload, options = client.published[0]
        self.assertEqual(topic, "homeassistant/light/pesetech2mqtt/skylight_a/config")
        self.assertEqual(payload["unique_id"], "pesetech2mqtt_skylight_a")
        self.assertEqual(payload["default_entity_id"], "light.skylight_a")
        self.assertEqual(payload["device"]["identifiers"], ["bluetooth_mesh_00112233-4455-6677-8899-aabbccddeeff"])
        self.assertEqual(payload["supported_color_modes"], ["color_temp"])
        self.assertTrue(options["retain"])

    async def test_desired_state_is_published_before_mesh_command(self):
        client = FakeMqttClient()
        node = FakeNode(client)
        bridge = mqtt.PesetechMqttLightBridge(client, node)

        await bridge.handle_command({"state": "OFF"})

        self.assertTrue(node.published_before_command)
        self.assertEqual(client.published[0][1]["state"], "OFF")
        self.assertEqual(node.calls, [("off", None)])
        await bridge.close()

    async def test_combined_values_use_one_combined_command(self):
        client = FakeMqttClient()
        node = FakeNode(client)
        bridge = mqtt.PesetechMqttLightBridge(client, node)

        await bridge.handle_command({"state": "ON", "brightness": 1234, "color_temp": 200, "transition": 5})

        self.assertEqual(node.calls, [("combined", 1234, 200, 5)])
        await bridge.close()

    async def test_internal_handler_failure_propagates_from_mqtt_service(self):
        original_client = mqtt.Client
        original_settings = mqtt.supervisor_mqtt_settings
        node = FakeNode(fail=True)
        FakeMqttClient.messages = [SimpleNamespace(payload=b'{"state":"OFF"}')]
        mqtt.Client = FakeMqttClient
        mqtt.supervisor_mqtt_settings = lambda: {
            "hostname": "mqtt",
            "port": 1883,
            "username": None,
            "password": None,
            "tls_context": None,
        }
        try:
            with self.assertRaisesRegex(TimeoutError, "no acknowledgement"):
                await mqtt.run_mqtt([node])
        finally:
            mqtt.Client = original_client
            mqtt.supervisor_mqtt_settings = original_settings
            FakeMqttClient.messages = []


    def test_parse_command_rejects_boolean_brightness(self):
        for value in ("true", "false"):
            payload = json.dumps({"state": "ON", "brightness": True if value == "true" else False}).encode()
            with self.subTest(value=value):
                with self.assertRaises(mqtt.InvalidMqttCommand, msg="brightness must be an integer"):
                    mqtt.parse_command(payload)

    def test_parse_command_rejects_boolean_color_temp(self):
        for value in (True, False):
            payload = json.dumps({"state": "ON", "color_temp": value}).encode()
            with self.subTest(value=value):
                with self.assertRaises(mqtt.InvalidMqttCommand, msg="color_temp must be an integer"):
                    mqtt.parse_command(payload)

    def test_parse_command_rejects_boolean_transition(self):
        for value in (True, False):
            payload = json.dumps({"state": "ON", "transition": value}).encode()
            with self.subTest(value=value):
                with self.assertRaises(mqtt.InvalidMqttCommand, msg="transition must be a number"):
                    mqtt.parse_command(payload)

    def test_parse_command_boundary_values(self):
        from app.skylight import BRIGHTNESS_SCALE, MAX_MIRED, MIN_MIRED

        # brightness 0
        result = mqtt.parse_command(json.dumps({"state": "ON", "brightness": 0}).encode())
        self.assertEqual(result["brightness"], 0)

        # brightness at max (BRIGHTNESS_SCALE = 65280)
        result = mqtt.parse_command(json.dumps({"state": "ON", "brightness": BRIGHTNESS_SCALE}).encode())
        self.assertEqual(result["brightness"], BRIGHTNESS_SCALE)

        # color_temp at MIN_MIRED
        result = mqtt.parse_command(json.dumps({"color_temp": MIN_MIRED}).encode())
        self.assertEqual(result["color_temp"], MIN_MIRED)

        # color_temp at MAX_MIRED
        result = mqtt.parse_command(json.dumps({"color_temp": MAX_MIRED}).encode())
        self.assertEqual(result["color_temp"], MAX_MIRED)

        # brightness just beyond max should be rejected
        with self.assertRaises(mqtt.InvalidMqttCommand):
            mqtt.parse_command(json.dumps({"state": "ON", "brightness": BRIGHTNESS_SCALE + 1}).encode())

        # color_temp just below min should be rejected
        with self.assertRaises(mqtt.InvalidMqttCommand):
            mqtt.parse_command(json.dumps({"color_temp": MIN_MIRED - 1}).encode())

        # color_temp just above max should be rejected
        with self.assertRaises(mqtt.InvalidMqttCommand):
            mqtt.parse_command(json.dumps({"color_temp": MAX_MIRED + 1}).encode())

    async def test_handle_command_brightness_only(self):
        client = FakeMqttClient()
        node = FakeNode(client)
        bridge = mqtt.PesetechMqttLightBridge(client, node)

        await bridge.handle_command({"brightness": 1234})

        self.assertEqual(node.calls, [("brightness", 1234, None)])
        await bridge.close()

    async def test_handle_command_color_temp_only(self):
        client = FakeMqttClient()
        node = FakeNode(client)
        bridge = mqtt.PesetechMqttLightBridge(client, node)

        await bridge.handle_command({"color_temp": 200})

        self.assertEqual(node.calls, [("temperature", 200, None)])
        await bridge.close()

    async def test_handle_command_color_temp_with_state_on(self):
        client = FakeMqttClient()
        node = FakeNode(client)
        bridge = mqtt.PesetechMqttLightBridge(client, node)

        await bridge.handle_command({"state": "ON", "color_temp": 300})

        self.assertEqual(node.calls, [("temperature", 300, None), ("on", None)])
        await bridge.close()

    async def test_schedule_readback_cancel_replace(self):
        import asyncio

        client = FakeMqttClient()
        node = FakeNode(client)
        bridge = mqtt.PesetechMqttLightBridge(client, node)

        await bridge.handle_command({"state": "ON"})
        first_task = bridge._readback_task
        self.assertIsNotNone(first_task)

        await bridge.handle_command({"state": "ON"})
        second_task = bridge._readback_task
        self.assertIsNotNone(second_task)

        # The second command must have replaced the first task.
        self.assertIsNot(first_task, second_task)

        # Yield so the cancelled first task's CancelledError is processed.
        # The run() coroutine catches CancelledError and returns, so the task
        # ends as "done" (not asyncio-cancelled).
        await asyncio.sleep(0)
        self.assertTrue(first_task.done())

        # The replacement task is still pending (readback_delay is 100s).
        self.assertFalse(second_task.done())

        await bridge.close()


if __name__ == "__main__":
    unittest.main()
