import importlib.util
import io
import json
import queue
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "pesetech_mqtt_smoke.py"
)
spec = importlib.util.spec_from_file_location("pesetech_mqtt_smoke", SCRIPT_PATH)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class PesetechMqttSmokeTest(unittest.TestCase):
    def base_args(self, config):
        return types.SimpleNamespace(
            config=config,
            broker=None,
            port=None,
            username=None,
            password=None,
            discovery_prefix=None,
            component="light",
            mesh_topic=None,
            device_id=None,
        )

    def test_topics_and_payloads_match_home_assistant_bridge(self):
        self.assertEqual(
            smoke.command_topic("homeassistant", "light", "mqtt_mesh", "skylight"),
            "homeassistant/light/mqtt_mesh/skylight/set",
        )
        self.assertEqual(
            smoke.state_topic("homeassistant", "light", "mqtt_mesh", "skylight"),
            "homeassistant/light/mqtt_mesh/skylight/state",
        )
        self.assertEqual(
            smoke.smoke_payloads(32640),
            [
                {"state": "ON"},
                {"state": "ON", "brightness": 32640},
                {"state": "ON", "color_temp": 556},
                {"state": "ON", "color_temp": 100},
                {"state": "OFF"},
            ],
        )

    def test_state_matching_accepts_extra_home_assistant_state_fields(self):
        message = {"state": "ON", "brightness": 32640, "color_mode": "color_temp", "color_temp": 100}

        self.assertTrue(smoke.matches_expected_state(message, {"state": "ON", "brightness": 32640}))
        self.assertTrue(smoke.matches_expected_state({"state": "OFF", "color_mode": "color_temp"}, {"state": "OFF"}))
        self.assertFalse(smoke.matches_expected_state(message, {"state": "OFF"}))
        self.assertFalse(smoke.matches_expected_state("ON", {"state": "ON"}))

    def test_decode_state_rejects_invalid_json(self):
        self.assertEqual(smoke.decode_state(b'{"state":"ON"}'), {"state": "ON"})
        self.assertIsNone(smoke.decode_state(b"{"))

    def test_config_defaults_fill_mqtt_topic_and_single_device(self):
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as config_file:
            config_file.write(
                """mqtt:
  broker: homeassistant.local
  port: 1884
  username: mqtt
  password: secret
  discovery_prefix: homeassistant
  node_id: mqtt_mesh
mesh:
  skylight:
    uuid: 00112233-4455-6677-8899-aabbccddeeff
    type: pesetech_skylight
"""
            )
            config_file.flush()

            args = smoke.apply_config_defaults(self.base_args(config_file.name))

        self.assertEqual(args.broker, "homeassistant.local")
        self.assertEqual(args.port, 1884)
        self.assertEqual(args.username, "mqtt")
        self.assertEqual(args.password, "secret")
        self.assertEqual(args.discovery_prefix, "homeassistant")
        self.assertEqual(args.mesh_topic, "mqtt_mesh")
        self.assertEqual(args.device_id, "skylight")

    def test_config_topic_beats_node_id_and_can_pick_nondefault_single_device(self):
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as config_file:
            config_file.write(
                """mqtt:
  broker: homeassistant.local
  topic: mesh_bridge
  node_id: ignored_node
mesh:
  kitchen_sky:
    uuid: 00112233-4455-6677-8899-aabbccddeeff
    type: pesetech_skylight
"""
            )
            config_file.flush()

            args = smoke.apply_config_defaults(self.base_args(config_file.name))

        self.assertEqual(args.mesh_topic, "mesh_bridge")
        self.assertEqual(args.device_id, "kitchen_sky")

    def test_explicit_cli_values_override_config_defaults(self):
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as config_file:
            config_file.write(
                """mqtt:
  broker: homeassistant.local
  username: config_user
  password: config_secret
  discovery_prefix: config_prefix
  node_id: config_mesh
mesh:
  skylight:
    uuid: 00112233-4455-6677-8899-aabbccddeeff
"""
            )
            config_file.flush()
            args = self.base_args(config_file.name)
            args.broker = "cli.local"
            args.port = 1884
            args.username = "cli_user"
            args.password = "cli_secret"
            args.discovery_prefix = "cli_prefix"
            args.mesh_topic = "cli_mesh"
            args.device_id = "cli_sky"

            smoke.apply_config_defaults(args)

        self.assertEqual(args.broker, "cli.local")
        self.assertEqual(args.port, 1884)
        self.assertEqual(args.username, "cli_user")
        self.assertEqual(args.password, "cli_secret")
        self.assertEqual(args.discovery_prefix, "cli_prefix")
        self.assertEqual(args.mesh_topic, "cli_mesh")
        self.assertEqual(args.device_id, "cli_sky")

    def test_missing_broker_exits_with_actionable_error(self):
        args = self.base_args("/tmp/does-not-exist-pesetech-config.yaml")

        with self.assertRaises(SystemExit) as error:
            smoke.apply_config_defaults(args)

        self.assertIn("pass --broker", str(error.exception))

    def test_wait_for_expected_state_returns_first_matching_message_and_seen_history(self):
        messages = queue.Queue()
        messages.put({"state": "ON", "brightness": 100})
        messages.put({"state": "ON", "brightness": 32640, "color_temp": 100})

        matched, seen = smoke.wait_for_expected_state(messages, {"state": "ON", "brightness": 32640}, 0.5)

        self.assertEqual(matched, {"state": "ON", "brightness": 32640, "color_temp": 100})
        self.assertEqual(
            seen,
            [
                {"state": "ON", "brightness": 100},
                {"state": "ON", "brightness": 32640, "color_temp": 100},
            ],
        )

    def test_dry_run_writes_jsonl_proof_events_without_paho(self):
        with tempfile.NamedTemporaryFile() as proof_file:
            args = types.SimpleNamespace(
                broker="homeassistant.local",
                port=1883,
                username=None,
                password=None,
                discovery_prefix="homeassistant",
                component="light",
                mesh_topic="mqtt_mesh",
                device_id="skylight",
                brightness=32640,
                delay=0,
                qos=0,
                wait_state=True,
                state_timeout=0,
                observe=True,
                proof_log=proof_file.name,
                run_id="mqtt-proof-1",
                dry_run=True,
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = smoke.publish_sequence(args)

            self.assertIn("command: homeassistant/light/mqtt_mesh/skylight/set", output.getvalue())
            self.assertIn("state:   homeassistant/light/mqtt_mesh/skylight/state", output.getvalue())
            self.assertIn("run_id:  mqtt-proof-1", output.getvalue())
            self.assertEqual(exit_code, 0)

            events = [
                json.loads(line)
                for line in Path(proof_file.name).read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([event["step"] for event in events], ["on", "brightness", "warm", "cool", "off"])
            self.assertEqual({event["run_id"] for event in events}, {"mqtt-proof-1"})
            self.assertEqual(events[1]["payload"], {"state": "ON", "brightness": 32640})
            self.assertEqual(events[1]["publish"], {"rc": None, "mid": None, "published": None, "error": None})
            self.assertIsNone(events[1]["matched_state"])
            self.assertIsNone(events[1]["state_elapsed_ms"])
            self.assertIsNone(events[1]["observed"])
            self.assertFalse(events[1]["precondition_visible_start"])

    def test_subscribe_and_wait_requires_matching_ack(self):
        class FakeClient:
            def __init__(self):
                self.subscriptions = []

            def subscribe(self, topic, qos):
                self.subscriptions.append((topic, qos))
                return 0, 42

        runtime = smoke.MqttRuntime(FakeClient(), queue.Queue())
        runtime.subscription_acks.put(41)
        runtime.subscription_acks.put(42)

        smoke.subscribe_and_wait(runtime, "homeassistant/light/mqtt_mesh/skylight/state", 0, 0.5)

        self.assertEqual(runtime.client.subscriptions, [("homeassistant/light/mqtt_mesh/skylight/state", 0)])

    def test_subscribe_and_wait_times_out_without_ack(self):
        class FakeClient:
            def subscribe(self, topic, qos):
                return 0, 42

        runtime = smoke.MqttRuntime(FakeClient(), queue.Queue())

        with self.assertRaises(SystemExit) as error:
            smoke.subscribe_and_wait(runtime, "homeassistant/light/mqtt_mesh/skylight/state", 0, 0)

        self.assertIn("Timed out waiting", str(error.exception))

    def test_publish_sequence_waits_for_subscription_before_first_command(self):
        events = []

        class FakePublishResult:
            def wait_for_publish(self):
                events.append("wait_for_publish")

        class FakeClient:
            def subscribe(self, topic, qos):
                events.append(("subscribe", topic, qos))
                return 0, 7

            def publish(self, topic, payload, qos):
                events.append(("publish", topic, payload, qos))
                return FakePublishResult()

            def loop_stop(self):
                events.append("loop_stop")

            def disconnect(self):
                events.append("disconnect")

        runtime = smoke.MqttRuntime(FakeClient(), queue.Queue())
        runtime.subscription_acks.put(7)
        original_connect_client = smoke.connect_client
        try:
            smoke.connect_client = lambda args, state_messages: runtime
            args = types.SimpleNamespace(
                broker="homeassistant.local",
                port=1883,
                username=None,
                password=None,
                discovery_prefix="homeassistant",
                component="light",
                mesh_topic="mqtt_mesh",
                device_id="skylight",
                brightness=32640,
                delay=0,
                qos=0,
                mqtt_timeout=0.5,
                wait_state=True,
                state_timeout=0,
                observe=False,
                proof_log=None,
                dry_run=False,
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = smoke.publish_sequence(args)
        finally:
            smoke.connect_client = original_connect_client

        first_publish = next(index for index, event in enumerate(events) if isinstance(event, tuple) and event[0] == "publish")
        first_subscribe = next(index for index, event in enumerate(events) if isinstance(event, tuple) and event[0] == "subscribe")
        self.assertLess(first_subscribe, first_publish)
        self.assertEqual(events[-2:], ["loop_stop", "disconnect"])
        self.assertEqual(exit_code, 1)
        self.assertIn("Smoke test failed", output.getvalue())
        self.assertIn("missing matching MQTT state", output.getvalue())

    def test_publish_sequence_fails_when_observation_is_rejected(self):
        events = []

        class FakePublishResult:
            def wait_for_publish(self):
                events.append("wait_for_publish")

        class FakeClient:
            def publish(self, topic, payload, qos):
                events.append(("publish", topic, payload, qos))
                return FakePublishResult()

            def loop_stop(self):
                events.append("loop_stop")

            def disconnect(self):
                events.append("disconnect")

        runtime = smoke.MqttRuntime(FakeClient(), queue.Queue())
        original_connect_client = smoke.connect_client
        original_observe_step = smoke.observe_step
        try:
            smoke.connect_client = lambda args, state_messages: runtime
            smoke.observe_step = lambda step: False
            args = types.SimpleNamespace(
                broker="homeassistant.local",
                port=1883,
                username=None,
                password=None,
                discovery_prefix="homeassistant",
                component="light",
                mesh_topic="mqtt_mesh",
                device_id="skylight",
                brightness=32640,
                delay=0,
                qos=0,
                mqtt_timeout=0.5,
                wait_state=False,
                state_timeout=0,
                observe=True,
                proof_log=None,
                dry_run=False,
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = smoke.publish_sequence(args)
        finally:
            smoke.connect_client = original_connect_client
            smoke.observe_step = original_observe_step

        self.assertEqual(exit_code, 1)
        self.assertEqual(events[-2:], ["loop_stop", "disconnect"])
        self.assertIn("real-light observation was not confirmed", output.getvalue())

    def test_publish_sequence_records_publish_status_and_state_latency(self):
        events = []
        state_queue = {}

        class FakePublishResult:
            def __init__(self, payload):
                self.rc = 0
                self.mid = 99
                self.payload = payload

            def wait_for_publish(self):
                events.append("wait_for_publish")
                state_queue["queue"].put(json.loads(self.payload))

            def is_published(self):
                return True

        class FakeClient:
            def subscribe(self, topic, qos):
                events.append(("subscribe", topic, qos))
                return 0, 7

            def publish(self, topic, payload, qos):
                events.append(("publish", topic, payload, qos))
                return FakePublishResult(payload)

            def loop_stop(self):
                events.append("loop_stop")

            def disconnect(self):
                events.append("disconnect")

        runtime = smoke.MqttRuntime(FakeClient(), queue.Queue())
        runtime.subscription_acks.put(7)
        original_connect_client = smoke.connect_client
        try:
            def fake_connect_client(args, state_messages):
                state_queue["queue"] = state_messages
                return runtime

            smoke.connect_client = fake_connect_client
            with tempfile.NamedTemporaryFile() as proof_file:
                args = types.SimpleNamespace(
                    broker="homeassistant.local",
                    port=1883,
                    username=None,
                    password=None,
                    discovery_prefix="homeassistant",
                    component="light",
                    mesh_topic="mqtt_mesh",
                    device_id="skylight",
                    brightness=32640,
                    delay=0,
                    qos=0,
                    mqtt_timeout=0.5,
                    wait_state=True,
                    state_timeout=0.5,
                    observe=False,
                    proof_log=proof_file.name,
                    dry_run=False,
                )
                output = io.StringIO()

                with redirect_stdout(output):
                    exit_code = smoke.publish_sequence(args)

                proof_events = [
                    json.loads(line)
                    for line in Path(proof_file.name).read_text(encoding="utf-8").splitlines()
                ]
        finally:
            smoke.connect_client = original_connect_client

        self.assertEqual(exit_code, 0)
        self.assertIn("publish: rc=0, mid=99, published=True", output.getvalue())
        self.assertEqual(proof_events[0]["publish"], {"rc": 0, "mid": 99, "published": True, "error": None})
        self.assertEqual(proof_events[1]["matched_state"], {"state": "ON", "brightness": 32640})
        self.assertIsInstance(proof_events[1]["state_elapsed_ms"], int)
        self.assertEqual(events[-2:], ["loop_stop", "disconnect"])

    def test_publish_sequence_can_precondition_visible_start_before_proof(self):
        events = []
        state_queue = {}

        class FakePublishResult:
            def __init__(self, payload):
                self.rc = 0
                self.mid = 99
                self.payload = payload

            def wait_for_publish(self):
                events.append("wait_for_publish")
                state_queue["queue"].put(json.loads(self.payload))

            def is_published(self):
                return True

        class FakeClient:
            def subscribe(self, topic, qos):
                events.append(("subscribe", topic, qos))
                return 0, 7

            def publish(self, topic, payload, qos):
                events.append(("publish", json.loads(payload)))
                return FakePublishResult(payload)

            def loop_stop(self):
                events.append("loop_stop")

            def disconnect(self):
                events.append("disconnect")

        runtime = smoke.MqttRuntime(FakeClient(), queue.Queue())
        runtime.subscription_acks.put(7)
        original_connect_client = smoke.connect_client
        try:
            def fake_connect_client(args, state_messages):
                state_queue["queue"] = state_messages
                return runtime

            smoke.connect_client = fake_connect_client
            with tempfile.NamedTemporaryFile() as proof_file:
                args = types.SimpleNamespace(
                    broker="homeassistant.local",
                    port=1883,
                    username=None,
                    password=None,
                    discovery_prefix="homeassistant",
                    component="light",
                    mesh_topic="mqtt_mesh",
                    device_id="skylight",
                    brightness=32640,
                    delay=0,
                    qos=0,
                    mqtt_timeout=0.5,
                    wait_state=True,
                    state_timeout=0.5,
                    observe=False,
                    proof_log=proof_file.name,
                    precondition_visible_start=True,
                    dry_run=False,
                )

                exit_code = smoke.publish_sequence(args)

                proof_events = [
                    json.loads(line)
                    for line in Path(proof_file.name).read_text(encoding="utf-8").splitlines()
                ]
        finally:
            smoke.connect_client = original_connect_client

        published_payloads = [event[1] for event in events if isinstance(event, tuple) and event[0] == "publish"]
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            published_payloads[:2],
            [
                {"state": "ON", "brightness": 8160, "color_temp": 100},
                {"state": "OFF"},
            ],
        )
        self.assertEqual([event["step"] for event in proof_events], ["on", "brightness", "warm", "cool", "off"])
        self.assertTrue(all(event["precondition_visible_start"] for event in proof_events))


if __name__ == "__main__":
    unittest.main()
