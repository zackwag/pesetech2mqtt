import importlib.util
import io
import json
import os
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "pesetech_ha_service_smoke.py"
)
spec = importlib.util.spec_from_file_location("pesetech_ha_service_smoke", SCRIPT_PATH)
ha_smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ha_smoke)


class FakeHomeAssistantClient:
    def __init__(self, states):
        self.states = list(states)
        self.service_calls = []
        self.get_state_calls = []

    def get_state(self, entity_id):
        self.get_state_calls.append(entity_id)
        if self.states:
            return self.states.pop(0)
        return {
            "entity_id": entity_id,
            "state": "off",
            "attributes": {"friendly_name": "Pesetech Skylight"},
        }

    def list_states(self):
        return []

    def get_config(self):
        return {"version": "2026.6.1", "location_name": "Home"}

    def call_light_service(self, service, payload):
        self.service_calls.append((service, payload))
        return {"status": 200, "response": []}


class PesetechHomeAssistantServiceSmokeTest(unittest.TestCase):
    def base_args(self, proof_log=None):
        return types.SimpleNamespace(
            url="http://homeassistant.local:8123",
            token="token",
            token_file=None,
            entity_id="light.skylight",
            brightness=ha_smoke.DEFAULT_BRIGHTNESS,
            warm_kelvin=2200,
            cool_kelvin=6500,
            delay=0,
            http_timeout=10,
            wait_state=True,
            wait_attributes=False,
            state_timeout=0,
            entity_timeout=0,
            poll_interval=0,
            brightness_tolerance=2,
            kelvin_tolerance=150,
            observe=False,
            precondition_visible_start=False,
            proof_log=proof_log,
            run_id="ha-proof-1",
            wait_mqtt_state=False,
            wait_mqtt_attributes=False,
            mqtt_config="/tmp/missing-pesetech-config.yaml",
            mqtt_broker=None,
            mqtt_port=None,
            mqtt_username=None,
            mqtt_password=None,
            mqtt_discovery_prefix=None,
            mqtt_mesh_topic=None,
            mqtt_device_id=None,
            mqtt_qos=0,
            mqtt_timeout=10,
            mqtt_state_timeout=0,
            mqtt_brightness_scale=None,
            mqtt_brightness_tolerance=ha_smoke.DEFAULT_MQTT_BRIGHTNESS_TOLERANCE,
            mqtt_mired_tolerance=ha_smoke.DEFAULT_MQTT_MIRED_TOLERANCE,
            list_candidates=False,
            candidate_search="skylight",
            dry_run=False,
        )

    def test_service_steps_use_home_assistant_light_fields(self):
        steps = ha_smoke.smoke_steps("light.skylight", 128, 2200, 6500)

        self.assertEqual([step["name"] for step in steps], ["on", "brightness", "warm", "cool", "off"])
        self.assertEqual(steps[1]["payload"], {"entity_id": "light.skylight", "brightness": 128})
        self.assertEqual(steps[2]["payload"], {"entity_id": "light.skylight", "color_temp_kelvin": 2200})
        self.assertEqual(steps[4]["service"], "turn_off")

    def test_api_paths_are_home_assistant_rest_paths(self):
        self.assertEqual(ha_smoke.service_path("light", "turn_on"), "/api/services/light/turn_on")
        self.assertEqual(ha_smoke.state_path("light.skylight"), "/api/states/light.skylight")

    def test_state_matching_can_be_state_only_or_attribute_strict(self):
        state = {
            "entity_id": "light.skylight",
            "state": "on",
            "attributes": {"brightness": 127, "color_temp_kelvin": 2240},
        }

        self.assertTrue(ha_smoke.state_matches(state, {"state": "on"}))
        self.assertTrue(
            ha_smoke.state_matches(
                state,
                {"state": "on", "attributes": {"brightness": 128, "color_temp_kelvin": 2200}},
                wait_attributes=True,
                brightness_tolerance=2,
                kelvin_tolerance=150,
            )
        )
        self.assertFalse(
            ha_smoke.state_matches(
                state,
                {"state": "on", "attributes": {"color_temp_kelvin": 6500}},
                wait_attributes=True,
                brightness_tolerance=2,
                kelvin_tolerance=150,
            )
        )

    def test_candidate_lights_filters_skylight_like_entities(self):
        states = [
            {"entity_id": "sensor.temp", "state": "1", "attributes": {}},
            {"entity_id": "light.kitchen", "state": "off", "attributes": {"friendly_name": "Kitchen"}},
            {"entity_id": "light.skylight", "state": "on", "attributes": {"friendly_name": "Pesetech Skylight"}},
        ]

        self.assertEqual(
            ha_smoke.candidate_lights(states, "skylight"),
            [{"entity_id": "light.skylight", "friendly_name": "Pesetech Skylight", "state": "on"}],
        )

    def test_resolve_token_accepts_home_assistant_app_supervisor_token(self):
        args = self.base_args()
        args.token = None
        args.token_file = None
        original = os.environ.get("SUPERVISOR_TOKEN")
        original_ha = os.environ.get("HOME_ASSISTANT_TOKEN")
        original_short = os.environ.get("HA_TOKEN")
        try:
            os.environ.pop("HOME_ASSISTANT_TOKEN", None)
            os.environ.pop("HA_TOKEN", None)
            os.environ["SUPERVISOR_TOKEN"] = "supervisor-token"

            self.assertEqual(ha_smoke.resolve_token(args), "supervisor-token")
            self.assertEqual(ha_smoke.resolve_token_with_source(args), ("supervisor-token", "SUPERVISOR_TOKEN"))
        finally:
            if original is None:
                os.environ.pop("SUPERVISOR_TOKEN", None)
            else:
                os.environ["SUPERVISOR_TOKEN"] = original
            if original_ha is None:
                os.environ.pop("HOME_ASSISTANT_TOKEN", None)
            else:
                os.environ["HOME_ASSISTANT_TOKEN"] = original_ha
            if original_short is None:
                os.environ.pop("HA_TOKEN", None)
            else:
                os.environ["HA_TOKEN"] = original_short

    def test_check_api_uses_home_assistant_config_without_moving_light(self):
        args = self.base_args()
        client = FakeHomeAssistantClient([])
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = ha_smoke.check_api(args, client=client)

        self.assertEqual(exit_code, 0)
        self.assertEqual(client.service_calls, [])
        self.assertIn("auth_source:    argument", output.getvalue())
        self.assertIn("GET /api/config", output.getvalue())
        self.assertIn("version:        2026.6.1", output.getvalue())
        self.assertIn("Home Assistant API check passed.", output.getvalue())

    def test_check_api_dry_run_does_not_require_token(self):
        args = self.base_args()
        args.token = None
        args.dry_run = True
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = ha_smoke.check_api(args)

        self.assertEqual(exit_code, 0)
        self.assertIn("auth_source:    dry_run", output.getvalue())
        self.assertIn("api_check:      GET /api/config", output.getvalue())

    def test_check_entity_verifies_target_light_without_moving_light(self):
        args = self.base_args()
        client = FakeHomeAssistantClient(
            [{"entity_id": "light.skylight", "state": "off", "attributes": {"friendly_name": "Pesetech Skylight"}}]
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = ha_smoke.check_entity(args, client=client)

        self.assertEqual(exit_code, 0)
        self.assertEqual(client.service_calls, [])
        self.assertEqual(client.get_state_calls, ["light.skylight"])
        self.assertIn("GET /api/states/light.skylight", output.getvalue())
        self.assertIn("Home Assistant entity check passed.", output.getvalue())

    def test_check_entity_lists_candidates_when_target_is_missing(self):
        class MissingEntityClient:
            service_calls = []

            def get_state(self, entity_id):
                raise ha_smoke.HomeAssistantError("missing", status=404)

            def list_states(self):
                return [
                    {"entity_id": "light.kitchen", "state": "off", "attributes": {"friendly_name": "Kitchen"}},
                    {
                        "entity_id": "light.pesetech_sky",
                        "state": "on",
                        "attributes": {"friendly_name": "Pesetech Skylight"},
                    },
                ]

        args = self.base_args()
        args.entity_id = "light.missing"
        output = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(output), redirect_stderr(stderr):
            exit_code = ha_smoke.check_entity(args, client=MissingEntityClient())

        self.assertEqual(exit_code, 1)
        self.assertIn("light.missing was not found", stderr.getvalue())
        self.assertIn("candidate lights:", output.getvalue())
        self.assertIn("light.pesetech_sky", output.getvalue())

    def test_check_entity_waits_for_delayed_mqtt_discovery_entity(self):
        class DelayedEntityClient:
            service_calls = []

            def __init__(self):
                self.get_state_calls = []

            def get_state(self, entity_id):
                self.get_state_calls.append(entity_id)
                if len(self.get_state_calls) == 1:
                    raise ha_smoke.HomeAssistantError("missing", status=404)
                return {"entity_id": entity_id, "state": "off", "attributes": {"friendly_name": "Pesetech Skylight"}}

            def list_states(self):
                return []

        args = self.base_args()
        args.entity_timeout = 0.5
        client = DelayedEntityClient()
        output = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(output), redirect_stderr(stderr):
            exit_code = ha_smoke.check_entity(args, client=client)

        self.assertEqual(exit_code, 0)
        self.assertEqual(client.get_state_calls, ["light.skylight", "light.skylight"])
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("entity_wait:    up to 0.5s", output.getvalue())
        self.assertIn("Home Assistant entity check passed.", output.getvalue())

    def test_dry_run_writes_jsonl_without_token_or_client(self):
        with tempfile.NamedTemporaryFile() as proof_file:
            args = self.base_args(proof_log=proof_file.name)
            args.token = None
            args.dry_run = True
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = ha_smoke.run_sequence(args)

            self.assertEqual(exit_code, 0)
            self.assertIn("POST /api/services/light/turn_on", output.getvalue())
            self.assertIn("run_id:         ha-proof-1", output.getvalue())
            events = [
                json.loads(line)
                for line in Path(proof_file.name).read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual([event["step"] for event in events], ["on", "brightness", "warm", "cool", "off"])
        self.assertEqual({event["run_id"] for event in events}, {"ha-proof-1"})
        self.assertEqual(events[2]["service"], "light.turn_on")
        self.assertEqual(events[2]["payload"], {"entity_id": "light.skylight", "color_temp_kelvin": 2200})
        self.assertEqual(events[2]["expected_mqtt_state"], {"state": "ON"})
        self.assertEqual(events[1]["expected_mqtt_attributes"], {"brightness": 49152})
        self.assertEqual(events[2]["expected_mqtt_attributes"], {"color_temp": 455})
        self.assertEqual(events[3]["expected_mqtt_attributes"], {"color_temp": 154})
        self.assertEqual(events[0]["auth_source"], "dry_run")
        self.assertFalse(events[1]["precondition_visible_start"])
        self.assertIsNone(events[2]["response"])
        self.assertIsNone(events[2]["observed"])

    def test_run_sequence_calls_ha_services_and_records_matched_states(self):
        states = [
            {"entity_id": "light.skylight", "state": "off", "attributes": {"friendly_name": "Pesetech Skylight"}},
            {"entity_id": "light.skylight", "state": "on", "attributes": {"friendly_name": "Pesetech Skylight"}},
            {"entity_id": "light.skylight", "state": "on", "attributes": {"brightness": ha_smoke.DEFAULT_BRIGHTNESS}},
            {"entity_id": "light.skylight", "state": "on", "attributes": {"color_temp_kelvin": 2200}},
            {"entity_id": "light.skylight", "state": "on", "attributes": {"color_temp_kelvin": 6500}},
            {"entity_id": "light.skylight", "state": "off", "attributes": {"friendly_name": "Pesetech Skylight"}},
        ]
        client = FakeHomeAssistantClient(states)
        with tempfile.NamedTemporaryFile() as proof_file:
            args = self.base_args(proof_log=proof_file.name)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = ha_smoke.run_sequence(args, client=client)

            events = [
                json.loads(line)
                for line in Path(proof_file.name).read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            client.service_calls,
            [
                ("turn_on", {"entity_id": "light.skylight"}),
                ("turn_on", {"entity_id": "light.skylight", "brightness": ha_smoke.DEFAULT_BRIGHTNESS}),
                ("turn_on", {"entity_id": "light.skylight", "color_temp_kelvin": 2200}),
                ("turn_on", {"entity_id": "light.skylight", "color_temp_kelvin": 6500}),
                ("turn_off", {"entity_id": "light.skylight"}),
            ],
        )
        self.assertEqual(events[1]["matched_state"]["attributes"]["brightness"], ha_smoke.DEFAULT_BRIGHTNESS)
        self.assertEqual(events[0]["auth_source"], "argument")
        self.assertIn("auth_source:    argument", output.getvalue())
        self.assertIn("initial_state", output.getvalue())

    def test_run_sequence_can_record_matching_mqtt_bridge_states(self):
        class FakeMqttClient:
            def __init__(self):
                self.stopped = False
                self.disconnected = False

            def loop_stop(self):
                self.stopped = True

            def disconnect(self):
                self.disconnected = True

        fake_client = FakeMqttClient()
        calls = []

        def fake_apply_config_defaults(args):
            args.broker = args.broker or "homeassistant.local"
            args.port = args.port or 1883
            args.discovery_prefix = args.discovery_prefix or "homeassistant"
            args.component = "light"
            args.mesh_topic = args.mesh_topic or "mqtt_mesh"
            args.device_id = args.device_id or "skylight"
            return args

        def fake_connect_client(args, state_messages):
            calls.append(("connect", args.broker, args.mesh_topic, args.device_id))
            return types.SimpleNamespace(client=fake_client, subscription_acks=[])

        def fake_subscribe(runtime, topic, qos, timeout):
            calls.append(("subscribe", topic, qos, timeout))

        def fake_wait_for_mqtt_step_state(
            messages,
            step,
            timeout,
            require_attributes=False,
            expected_attributes=None,
            brightness_tolerance=ha_smoke.DEFAULT_MQTT_BRIGHTNESS_TOLERANCE,
            mired_tolerance=ha_smoke.DEFAULT_MQTT_MIRED_TOLERANCE,
        ):
            calls.append(("wait", step["name"], timeout, require_attributes, expected_attributes))
            message = {**ha_smoke.expected_mqtt_state(step), "color_mode": "color_temp"}
            message.update(expected_attributes or {})
            return message, []

        original = (
            ha_smoke.apply_mqtt_config_defaults,
            ha_smoke.connect_mqtt_client,
            ha_smoke.mqtt_subscribe_and_wait,
            ha_smoke.wait_for_mqtt_step_state,
        )
        try:
            ha_smoke.apply_mqtt_config_defaults = fake_apply_config_defaults
            ha_smoke.connect_mqtt_client = fake_connect_client
            ha_smoke.mqtt_subscribe_and_wait = fake_subscribe
            ha_smoke.wait_for_mqtt_step_state = fake_wait_for_mqtt_step_state
            states = [
                {"entity_id": "light.skylight", "state": "off", "attributes": {}},
                {"entity_id": "light.skylight", "state": "on", "attributes": {}},
                {"entity_id": "light.skylight", "state": "on", "attributes": {"brightness": ha_smoke.DEFAULT_BRIGHTNESS}},
                {"entity_id": "light.skylight", "state": "on", "attributes": {"color_temp_kelvin": 2200}},
                {"entity_id": "light.skylight", "state": "on", "attributes": {"color_temp_kelvin": 6500}},
                {"entity_id": "light.skylight", "state": "off", "attributes": {}},
            ]
            client = FakeHomeAssistantClient(states)
            with tempfile.NamedTemporaryFile() as proof_file:
                args = self.base_args(proof_log=proof_file.name)
                args.wait_mqtt_state = True
                args.wait_mqtt_attributes = True
                args.mqtt_state_timeout = 0.1

                exit_code = ha_smoke.run_sequence(args, client=client)

                events = [
                    json.loads(line)
                    for line in Path(proof_file.name).read_text(encoding="utf-8").splitlines()
                ]
        finally:
            (
                ha_smoke.apply_mqtt_config_defaults,
                ha_smoke.connect_mqtt_client,
                ha_smoke.mqtt_subscribe_and_wait,
                ha_smoke.wait_for_mqtt_step_state,
            ) = original

        self.assertEqual(exit_code, 0)
        self.assertTrue(fake_client.stopped)
        self.assertTrue(fake_client.disconnected)
        self.assertIn(("subscribe", "homeassistant/light/mqtt_mesh/skylight/state", 0, 10), calls)
        self.assertEqual(events[0]["expected_mqtt_state"], {"state": "ON"})
        self.assertEqual(events[1]["required_mqtt_fields"], ["brightness"])
        self.assertEqual(events[1]["expected_mqtt_attributes"], {"brightness": 49152})
        self.assertEqual(events[1]["matched_mqtt_state"]["brightness"], 49152)
        self.assertEqual(events[2]["required_mqtt_fields"], ["color_temp"])
        self.assertEqual(events[2]["expected_mqtt_attributes"], {"color_temp": 455})
        self.assertEqual(events[3]["expected_mqtt_attributes"], {"color_temp": 154})
        self.assertEqual(events[-1]["expected_mqtt_state"], {"state": "OFF"})
        self.assertEqual(events[-1]["matched_mqtt_state"], {"state": "OFF", "color_mode": "color_temp"})

    def test_run_sequence_can_precondition_visible_start_before_proof(self):
        states = [
            {"entity_id": "light.skylight", "state": "off", "attributes": {}},
            {"entity_id": "light.skylight", "state": "on", "attributes": {"brightness": 48, "color_temp_kelvin": 6500}},
            {"entity_id": "light.skylight", "state": "off", "attributes": {}},
            {"entity_id": "light.skylight", "state": "on", "attributes": {}},
            {"entity_id": "light.skylight", "state": "on", "attributes": {"brightness": ha_smoke.DEFAULT_BRIGHTNESS}},
            {"entity_id": "light.skylight", "state": "on", "attributes": {"color_temp_kelvin": 2200}},
            {"entity_id": "light.skylight", "state": "on", "attributes": {"color_temp_kelvin": 6500}},
            {"entity_id": "light.skylight", "state": "off", "attributes": {}},
        ]
        client = FakeHomeAssistantClient(states)
        with tempfile.NamedTemporaryFile() as proof_file:
            args = self.base_args(proof_log=proof_file.name)
            args.precondition_visible_start = True
            args.wait_attributes = True

            exit_code = ha_smoke.run_sequence(args, client=client)

            events = [
                json.loads(line)
                for line in Path(proof_file.name).read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            client.service_calls[:2],
            [
                (
                    "turn_on",
                    {
                        "entity_id": "light.skylight",
                        "brightness": 48,
                        "color_temp_kelvin": 6500,
                    },
                ),
                ("turn_off", {"entity_id": "light.skylight"}),
            ],
        )
        self.assertEqual([event["step"] for event in events], ["on", "brightness", "warm", "cool", "off"])
        self.assertTrue(all(event["precondition_visible_start"] for event in events))


if __name__ == "__main__":
    unittest.main()
