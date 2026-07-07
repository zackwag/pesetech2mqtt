import importlib.util
import io
import json
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "pesetech_verify_ha_service_proof.py"
)
spec = importlib.util.spec_from_file_location("pesetech_verify_ha_service_proof", SCRIPT_PATH)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def valid_event(step, service, payload, expected_state, matched_state, run_id="ha-proof-1"):
    expected_mqtt_state = {"state": "OFF" if service == "light.turn_off" else "ON"}
    expected_mqtt_attributes = verifier.expected_mqtt_attributes_for_step(
        {"step": step, "service": service, "payload": payload}
    )
    matched_mqtt_state = {**expected_mqtt_state, "color_mode": "color_temp"}
    matched_mqtt_state.update(expected_mqtt_attributes)
    return {
        "timestamp": "2026-06-27T12:00:00-0400",
        "run_id": run_id,
        "step": step,
        "home_assistant_url": "http://homeassistant.local:8123",
        "entity_id": "light.skylight",
        "service": service,
        "service_path": "/api/services/" + service.replace(".", "/"),
        "payload": payload,
        "expected_state": expected_state,
        "response": {"status": 200, "response": []},
        "response_error": None,
        "matched_state": matched_state,
        "seen_states": [],
        "state_elapsed_ms": 12,
        "mqtt_state_topic": "homeassistant/light/mqtt_mesh/skylight/state",
        "expected_mqtt_state": expected_mqtt_state,
        "expected_mqtt_attributes": expected_mqtt_attributes,
        "required_mqtt_fields": verifier.expected_required_mqtt_fields(step),
        "matched_mqtt_state": matched_mqtt_state,
        "seen_mqtt_states": [],
        "mqtt_state_elapsed_ms": 14,
        "observed": True,
    }


def valid_events(run_id="ha-proof-1"):
    return [
        valid_event("on", "light.turn_on", {"entity_id": "light.skylight"}, {"state": "on"}, {"state": "on"}, run_id=run_id),
        valid_event(
            "brightness",
            "light.turn_on",
            {"entity_id": "light.skylight", "brightness": verifier.DEFAULT_BRIGHTNESS},
            {"state": "on", "attributes": {"brightness": verifier.DEFAULT_BRIGHTNESS}},
            {"state": "on", "attributes": {"brightness": verifier.DEFAULT_BRIGHTNESS - 1}},
            run_id=run_id,
        ),
        valid_event(
            "warm",
            "light.turn_on",
            {"entity_id": "light.skylight", "color_temp_kelvin": 2200},
            {"state": "on", "attributes": {"color_temp_kelvin": 2200}},
            {"state": "on", "attributes": {"color_temp_kelvin": 2240}},
            run_id=run_id,
        ),
        valid_event(
            "cool",
            "light.turn_on",
            {"entity_id": "light.skylight", "color_temp_kelvin": 6500},
            {"state": "on", "attributes": {"color_temp_kelvin": 6500}},
            {"state": "on", "attributes": {"color_temp_kelvin": 6450}},
            run_id=run_id,
        ),
        valid_event("off", "light.turn_off", {"entity_id": "light.skylight"}, {"state": "off"}, {"state": "off"}, run_id=run_id),
    ]


class PesetechVerifyHomeAssistantServiceProofTest(unittest.TestCase):
    def args(self, **overrides):
        defaults = {
            "url": "http://homeassistant.local:8123/",
            "entity_id": "light.skylight",
            "brightness": verifier.DEFAULT_BRIGHTNESS,
            "warm_kelvin": 2200,
            "cool_kelvin": 6500,
            "run_id": None,
            "require_attributes": False,
            "brightness_tolerance": 2,
            "kelvin_tolerance": 150,
            "mqtt_brightness_scale": verifier.DEFAULT_MQTT_BRIGHTNESS_SCALE,
            "mqtt_brightness_tolerance": verifier.DEFAULT_MQTT_BRIGHTNESS_TOLERANCE,
            "mqtt_mired_tolerance": verifier.DEFAULT_MQTT_MIRED_TOLERANCE,
            "allow_missing_state": False,
            "require_mqtt_state": False,
            "require_mqtt_attributes": False,
            "allow_service_error": False,
            "allow_unobserved": False,
        }
        defaults.update(overrides)
        return types.SimpleNamespace(**defaults)

    def test_valid_home_assistant_service_proof_passes(self):
        sequence, errors = verifier.validate_sequence(valid_events(), self.args())

        self.assertEqual([event["step"] for event in sequence], verifier.STEP_NAMES)
        self.assertEqual(errors, [])

    def test_require_attributes_allows_tolerated_rounding(self):
        _, errors = verifier.validate_sequence(valid_events(), self.args(require_attributes=True))

        self.assertEqual(errors, [])

    def test_require_attributes_fails_when_kelvin_is_wrong(self):
        events = valid_events()
        events[2]["matched_state"]["attributes"]["color_temp_kelvin"] = 3000

        _, errors = verifier.validate_sequence(events, self.args(require_attributes=True))

        self.assertIn(
            "warm: missing matching Home Assistant state for {'state': 'on', 'attributes': {'color_temp_kelvin': 2200}}",
            errors,
        )

    def test_uses_latest_complete_sequence(self):
        failed = valid_events(run_id="ha-proof-old")
        failed[-1]["observed"] = False

        sequence, errors = verifier.validate_sequence(failed + valid_events(run_id="ha-proof-new"), self.args())

        self.assertEqual(sequence[-1]["observed"], True)
        self.assertEqual({event["run_id"] for event in sequence}, {"ha-proof-new"})
        self.assertEqual(errors, [])

    def test_can_select_specific_run_id(self):
        failed = valid_events(run_id="ha-proof-old")
        failed[-1]["observed"] = False

        sequence, errors = verifier.validate_sequence(
            failed + valid_events(run_id="ha-proof-new"),
            self.args(run_id="ha-proof-old"),
        )

        self.assertEqual({event["run_id"] for event in sequence}, {"ha-proof-old"})
        self.assertIn("off: real-light observation was not confirmed true", errors)

    def test_missing_observation_fails(self):
        events = valid_events()
        events[2]["observed"] = False

        _, errors = verifier.validate_sequence(events, self.args())

        self.assertIn("warm: real-light observation was not confirmed true", errors)

    def test_missing_ha_state_fails(self):
        events = valid_events()
        events[1]["matched_state"] = None

        _, errors = verifier.validate_sequence(events, self.args())

        self.assertIn(
            f"brightness: missing matching Home Assistant state for {{'state': 'on', 'attributes': {{'brightness': {verifier.DEFAULT_BRIGHTNESS}}}}}",
            errors,
        )

    def test_service_error_fails(self):
        events = valid_events()
        events[1]["response"] = {"status": 500, "response": []}
        events[2]["response_error"] = "HTTP 401"

        _, errors = verifier.validate_sequence(events, self.args())

        self.assertIn("brightness: Home Assistant service returned status 500", errors)
        self.assertIn("warm: Home Assistant service call failed: HTTP 401", errors)

    def test_require_mqtt_state_fails_when_bridge_state_missing(self):
        events = valid_events()
        events[1]["matched_mqtt_state"] = None

        _, errors = verifier.validate_sequence(events, self.args(require_mqtt_state=True))

        self.assertIn("brightness: missing matching MQTT bridge state for {'state': 'ON'}", errors)

    def test_require_mqtt_attributes_fails_when_bridge_attribute_missing(self):
        events = valid_events()
        events[1]["matched_mqtt_state"] = {"state": "ON", "color_mode": "color_temp"}

        _, errors = verifier.validate_sequence(events, self.args(require_mqtt_attributes=True))

        self.assertIn("brightness: missing MQTT bridge attribute fields: brightness", errors)

    def test_require_mqtt_attributes_fails_when_bridge_attribute_value_is_wrong(self):
        events = valid_events()
        events[2]["matched_mqtt_state"]["color_temp"] = 123

        _, errors = verifier.validate_sequence(events, self.args(require_mqtt_attributes=True))

        self.assertIn(
            "warm: MQTT bridge attributes {'state': 'ON', 'color_mode': 'color_temp', 'color_temp': 123} do not match expected {'color_temp': 455}",
            errors,
        )

    def test_flags_can_relax_state_service_or_observation_requirement(self):
        events = valid_events()
        events[1]["matched_state"] = None
        events[1]["response"] = {"status": 500, "response": []}
        events[2]["observed"] = False

        _, errors = verifier.validate_sequence(
            events,
            self.args(
                allow_missing_state=True,
                allow_service_error=True,
                allow_unobserved=True,
            ),
        )

        self.assertEqual(errors, [])

    def test_sequence_summary_counts_state_service_and_observation(self):
        events = valid_events()
        events[2]["observed"] = False
        events[3]["observed"] = None
        events[4]["matched_state"] = None
        events[1]["response"] = {"status": 500, "response": []}

        summary = verifier.sequence_summary(events, self.args())

        self.assertEqual(
            summary,
            {
                "total": 5,
                "ha_state_matched": 4,
                "mqtt_state_matched": 5,
                "service_ok": 4,
                "observed_yes": 3,
                "observed_no": 1,
                "observed_missing": 1,
            },
        )

    def test_load_jsonl_reports_invalid_lines(self):
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as proof_file:
            proof_file.write(json.dumps(valid_events()[0]) + "\n")
            proof_file.write("{\n")
            proof_file.flush()

            events, errors = verifier.load_jsonl(proof_file.name)

        self.assertEqual(events[0]["step"], "on")
        self.assertEqual(len(errors), 1)
        self.assertIn("line 2: invalid JSON", errors[0])

    def test_print_report_returns_failure_status(self):
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = verifier.print_report(valid_events(), ["boom"], args=self.args())

        self.assertEqual(exit_code, 1)
        self.assertIn("Home Assistant service proof verification failed", output.getvalue())
        self.assertIn("run_id: ha-proof-1", output.getvalue())
        self.assertIn("Summary: HA state matched 5/5; MQTT state matched 5/5; service ok=5/5; visual yes=5, no=0, unobserved=0", output.getvalue())


if __name__ == "__main__":
    unittest.main()
