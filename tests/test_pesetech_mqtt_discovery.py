import importlib.util
import io
import queue
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "pesetech_mqtt_discovery.py"
)
spec = importlib.util.spec_from_file_location("pesetech_mqtt_discovery", SCRIPT_PATH)
discovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(discovery)


def valid_args():
    return types.SimpleNamespace(
        discovery_prefix="homeassistant",
        component="light",
        mesh_topic="mqtt_mesh",
        device_id="skylight",
        default_entity_id="light.skylight",
        brightness_scale=65280,
        min_mireds=50,
        max_mireds=1250,
        broker="homeassistant.local",
        port=1883,
        username=None,
        password=None,
        qos=0,
        mqtt_timeout=0.5,
        discovery_timeout=0.5,
        candidate_timeout=0.5,
        require_retained=True,
        dump_json=False,
        dry_run=False,
    )


def valid_payload():
    return {
        "~": "homeassistant/light/mqtt_mesh/skylight",
        "name": "Pesetech Skylight",
        "unique_id": "mqtt_mesh_skylight",
        "default_entity_id": "light.skylight",
        "command_topic": "~/set",
        "state_topic": "~/state",
        "schema": "json",
        "brightness": True,
        "brightness_scale": 65280,
        "min_mireds": 50,
        "max_mireds": 1250,
        "supported_color_modes": ["color_temp"],
        "device": {
            "identifiers": ["bluetooth_mesh_00112233-4455-6677-8899-aabbccddeeff"],
            "name": "Pesetech Skylight",
            "manufacturer": "Pesetech/Lepu",
            "model": "Artificial Skylight",
        },
        "origin": {
            "name": "pesetech-home-assistant",
            "support_url": "https://github.com/hrdwdmrbl/pesetech-home-assistant",
        },
    }


class PesetechMqttDiscoveryTest(unittest.TestCase):
    def test_topics_match_home_assistant_discovery_shape(self):
        self.assertEqual(
            discovery.discovery_topic("homeassistant", "light", "mqtt_mesh", "skylight"),
            "homeassistant/light/mqtt_mesh/skylight/config",
        )
        self.assertEqual(
            discovery.base_topic("homeassistant", "light", "mqtt_mesh", "skylight"),
            "homeassistant/light/mqtt_mesh/skylight",
        )

    def test_validates_expected_pesetech_discovery_payload(self):
        self.assertEqual(discovery.validate_discovery(valid_payload(), valid_args()), [])

    def test_expected_default_entity_id_can_come_from_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "mqtt:",
                        "  broker: homeassistant.local",
                        "  node_id: mqtt_mesh",
                        "mesh:",
                        "  skylight:",
                        "    uuid: 00112233-4455-6677-8899-aabbccddeeff",
                        "    default_entity_id: light.sunroom_sky",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            args = valid_args()
            args.config = str(config_path)
            args.default_entity_id = None

            args = discovery.apply_discovery_config_defaults(args)

            self.assertEqual(args.default_entity_id, "light.sunroom_sky")

    def test_validation_catches_home_assistant_breaking_fields(self):
        payload = valid_payload()
        payload["brightness_scale"] = 255
        payload["supported_color_modes"] = ["brightness"]
        payload["color_mode"] = "color_temp"
        payload["object_id"] = "skylight"
        payload["device"]["manufacturer"] = "Bluetooth Mesh"
        payload["default_entity_id"] = "light.other"

        errors = discovery.validate_discovery(payload, valid_args())

        self.assertIn("brightness_scale: expected 65280, got 255", errors)
        self.assertIn("default_entity_id: expected 'light.skylight', got 'light.other'", errors)
        self.assertIn("supported_color_modes: expected ['color_temp'], got ['brightness']", errors)
        self.assertIn("object_id must not be present in Home Assistant discovery config; use default_entity_id", errors)
        self.assertIn("color_mode must not be present in Home Assistant discovery config", errors)
        self.assertIn("device.manufacturer: expected 'Pesetech/Lepu', got 'Bluetooth Mesh'", errors)

    def test_wait_for_discovery_returns_matching_topic_and_seen_history(self):
        messages = queue.Queue()
        messages.put({"topic": "other/topic", "payload": {}})
        messages.put({"topic": "homeassistant/light/mqtt_mesh/skylight/config", "payload": valid_payload()})

        matched, seen = discovery.wait_for_discovery(
            messages,
            "homeassistant/light/mqtt_mesh/skylight/config",
            0.5,
        )

        self.assertEqual(matched["payload"]["unique_id"], "mqtt_mesh_skylight")
        self.assertEqual([message["topic"] for message in seen], ["other/topic", "homeassistant/light/mqtt_mesh/skylight/config"])

    def test_verify_discovery_checks_retained_payload_from_broker(self):
        class FakeClient:
            def __init__(self):
                self.subscriptions = []

            def subscribe(self, topic, qos):
                self.subscriptions.append((topic, qos))
                return 0, 7

            def loop_stop(self):
                pass

            def disconnect(self):
                pass

        runtime = discovery.DiscoveryRuntime(FakeClient(), queue.Queue())
        runtime.subscription_acks.put(7)
        original_connect_client = discovery.connect_client
        try:
            def fake_connect(args, messages):
                messages.put(
                    {
                        "topic": "homeassistant/light/mqtt_mesh/skylight/config",
                        "retain": True,
                        "payload": valid_payload(),
                        "raw_payload": "{}",
                    }
                )
                return runtime

            discovery.connect_client = fake_connect
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = discovery.verify_discovery(valid_args())
        finally:
            discovery.connect_client = original_connect_client

        self.assertEqual(exit_code, 0)
        self.assertIn("Discovery verification passed.", output.getvalue())
        self.assertEqual(runtime.client.subscriptions, [("homeassistant/light/mqtt_mesh/skylight/config", 0)])

    def test_verify_discovery_fails_when_required_retained_flag_missing(self):
        class FakeClient:
            def subscribe(self, topic, qos):
                return 0, 7

            def loop_stop(self):
                pass

            def disconnect(self):
                pass

        runtime = discovery.DiscoveryRuntime(FakeClient(), queue.Queue())
        runtime.subscription_acks.put(7)
        original_connect_client = discovery.connect_client
        try:
            def fake_connect(args, messages):
                messages.put(
                    {
                        "topic": "homeassistant/light/mqtt_mesh/skylight/config",
                        "retain": False,
                        "payload": valid_payload(),
                        "raw_payload": "{}",
                    }
                )
                return runtime

            discovery.connect_client = fake_connect
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = discovery.verify_discovery(valid_args())
        finally:
            discovery.connect_client = original_connect_client

        self.assertEqual(exit_code, 1)
        self.assertIn("discovery message was not marked retained", output.getvalue())

    def test_verify_discovery_lists_candidate_retained_configs_when_exact_topic_missing(self):
        subscriptions = []
        acks = queue.Queue()

        class FakeClient:
            def subscribe(self, topic, qos):
                subscriptions.append((topic, qos))
                mid = len(subscriptions)
                acks.put(mid)
                return 0, mid

            def loop_stop(self):
                pass

            def disconnect(self):
                pass

        runtime = discovery.DiscoveryRuntime(FakeClient(), acks)
        original_connect_client = discovery.connect_client
        try:
            def fake_connect(args, messages):
                candidate = valid_payload()
                candidate["default_entity_id"] = "light.kitchen_sky"
                candidate["unique_id"] = "mqtt_mesh_kitchen_sky"
                messages.put(
                    {
                        "topic": "homeassistant/light/mqtt_mesh/kitchen_sky/config",
                        "retain": True,
                        "payload": candidate,
                        "raw_payload": "{}",
                    }
                )
                return runtime

            args = valid_args()
            args.discovery_timeout = 0.0
            discovery.connect_client = fake_connect
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = discovery.verify_discovery(args)
        finally:
            discovery.connect_client = original_connect_client

        self.assertEqual(exit_code, 1)
        self.assertEqual(
            subscriptions,
            [
                ("homeassistant/light/mqtt_mesh/skylight/config", 0),
                ("homeassistant/light/#", 0),
            ],
        )
        report = output.getvalue()
        self.assertIn("no retained discovery message received", report)
        self.assertIn("Candidate retained light discovery configs:", report)
        self.assertIn("homeassistant/light/mqtt_mesh/kitchen_sky/config", report)
        self.assertIn("default_entity_id='light.kitchen_sky'", report)

    def test_dry_run_prints_expected_topic_without_connecting(self):
        args = valid_args()
        args.dry_run = True
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = discovery.verify_discovery(args)

        self.assertEqual(exit_code, 0)
        self.assertIn("homeassistant/light/mqtt_mesh/skylight/config", output.getvalue())


if __name__ == "__main__":
    unittest.main()
