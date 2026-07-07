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
    / "pesetech_verify_proof.py"
)
spec = importlib.util.spec_from_file_location("pesetech_verify_proof", SCRIPT_PATH)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def valid_event(step, payload, expected_state, run_id="mqtt-proof-1"):
    return {
        "timestamp": "2026-06-27T12:00:00-0400",
        "run_id": run_id,
        "step": step,
        "command_topic": "homeassistant/light/mqtt_mesh/skylight/set",
        "state_topic": "homeassistant/light/mqtt_mesh/skylight/state",
        "payload": payload,
        "expected_state": expected_state,
        "publish": {"rc": 0, "mid": 1, "published": True, "error": None},
        "matched_state": {**expected_state, "color_mode": "color_temp"},
        "seen_states": [],
        "state_elapsed_ms": 12,
        "observed": True,
    }


def valid_events(brightness=32640, run_id="mqtt-proof-1"):
    events = [
        valid_event("on", {"state": "ON"}, {"state": "ON"}, run_id=run_id),
        valid_event("brightness", {"state": "ON", "brightness": brightness}, {"state": "ON", "brightness": brightness}, run_id=run_id),
        valid_event("warm", {"state": "ON", "color_temp": 556}, {"state": "ON", "color_temp": 556}, run_id=run_id),
        valid_event("cool", {"state": "ON", "color_temp": 100}, {"state": "ON", "color_temp": 100}, run_id=run_id),
        valid_event("off", {"state": "OFF"}, {"state": "OFF"}, run_id=run_id),
    ]
    events[-1]["matched_state"] = {"state": "OFF", "color_mode": "color_temp"}
    return events


class PesetechVerifyProofTest(unittest.TestCase):
    def args(self, **overrides):
        defaults = {
            "discovery_prefix": "homeassistant",
            "component": "light",
            "mesh_topic": "mqtt_mesh",
            "device_id": "skylight",
            "brightness": 32640,
            "run_id": None,
            "allow_missing_state": False,
            "allow_unobserved": False,
        }
        defaults.update(overrides)
        return types.SimpleNamespace(**defaults)

    def test_valid_proof_sequence_passes(self):
        sequence, errors = verifier.validate_sequence(valid_events(), self.args())

        self.assertEqual([event["step"] for event in sequence], verifier.STEP_NAMES)
        self.assertEqual(errors, [])

    def test_uses_latest_complete_sequence(self):
        failed = valid_events(run_id="mqtt-proof-old")
        failed[-1]["observed"] = False

        sequence, errors = verifier.validate_sequence(failed + valid_events(run_id="mqtt-proof-new"), self.args())

        self.assertEqual(sequence[-1]["observed"], True)
        self.assertEqual({event["run_id"] for event in sequence}, {"mqtt-proof-new"})
        self.assertEqual(errors, [])

    def test_can_select_specific_run_id(self):
        failed = valid_events(run_id="mqtt-proof-old")
        failed[-1]["observed"] = False

        sequence, errors = verifier.validate_sequence(
            failed + valid_events(run_id="mqtt-proof-new"),
            self.args(run_id="mqtt-proof-old"),
        )

        self.assertEqual({event["run_id"] for event in sequence}, {"mqtt-proof-old"})
        self.assertIn("off: real-light observation was not confirmed true", errors)

    def test_missing_observation_fails(self):
        events = valid_events()
        events[2]["observed"] = False

        _, errors = verifier.validate_sequence(events, self.args())

        self.assertIn("warm: real-light observation was not confirmed true", errors)

    def test_missing_mqtt_state_fails(self):
        events = valid_events()
        events[1]["matched_state"] = None

        _, errors = verifier.validate_sequence(events, self.args())

        self.assertIn("brightness: missing matching MQTT state for {'state': 'ON', 'brightness': 32640}", errors)

    def test_publish_failure_in_proof_log_fails(self):
        events = valid_events()
        events[1]["publish"] = {"rc": 4, "mid": 99, "published": False, "error": None}

        _, errors = verifier.validate_sequence(events, self.args())

        self.assertIn("brightness: MQTT publish returned rc=4", errors)
        self.assertIn("brightness: MQTT publish did not complete", errors)

    def test_flags_can_relax_state_or_observation_requirement(self):
        events = valid_events()
        events[1]["matched_state"] = None
        events[2]["observed"] = False

        _, errors = verifier.validate_sequence(
            events,
            self.args(allow_missing_state=True, allow_unobserved=True),
        )

        self.assertEqual(errors, [])

    def test_sequence_summary_counts_mqtt_state_and_observations(self):
        events = valid_events()
        events[2]["observed"] = False
        events[3]["observed"] = None
        events[4]["matched_state"] = None

        summary = verifier.sequence_summary(events, self.args())

        self.assertEqual(
            summary,
            {
                "total": 5,
                "mqtt_state_matched": 4,
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

    def test_config_defaults_fill_topic_fields(self):
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as config_file:
            config_file.write(
                """mqtt:
  broker: homeassistant.local
  discovery_prefix: ha_discovery
  node_id: pesetech_mesh
mesh:
  kitchen_sky:
    uuid: 00112233-4455-6677-8899-aabbccddeeff
    type: pesetech_skylight
"""
            )
            config_file.flush()
            args = types.SimpleNamespace(
                config=config_file.name,
                discovery_prefix=None,
                mesh_topic=None,
                device_id=None,
            )

            verifier.apply_config_defaults(args)

        self.assertEqual(args.discovery_prefix, "ha_discovery")
        self.assertEqual(args.mesh_topic, "pesetech_mesh")
        self.assertEqual(args.device_id, "kitchen_sky")

    def test_explicit_topic_values_override_config_defaults(self):
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as config_file:
            config_file.write(
                """mqtt:
  broker: homeassistant.local
  discovery_prefix: ha_discovery
  node_id: pesetech_mesh
mesh:
  kitchen_sky:
    uuid: 00112233-4455-6677-8899-aabbccddeeff
"""
            )
            config_file.flush()
            args = types.SimpleNamespace(
                config=config_file.name,
                discovery_prefix="homeassistant",
                mesh_topic="mqtt_mesh",
                device_id="skylight",
            )

            verifier.apply_config_defaults(args)

        self.assertEqual(args.discovery_prefix, "homeassistant")
        self.assertEqual(args.mesh_topic, "mqtt_mesh")
        self.assertEqual(args.device_id, "skylight")

    def test_print_report_returns_failure_status(self):
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = verifier.print_report(valid_events(), ["boom"])

        self.assertEqual(exit_code, 1)
        self.assertIn("Proof verification failed", output.getvalue())
        self.assertIn("run_id: mqtt-proof-1", output.getvalue())
        self.assertIn("publish_rc=0, published=True, state_elapsed_ms=12", output.getvalue())
        self.assertIn("Summary: MQTT state matched 5/5; visual yes=5, no=0, unobserved=0", output.getvalue())

    def test_print_report_summarizes_unobserved_move_test(self):
        events = valid_events()
        for event in events:
            event["observed"] = None
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = verifier.print_report(events, [], args=self.args(allow_unobserved=True))

        self.assertEqual(exit_code, 0)
        self.assertIn("Summary: MQTT state matched 5/5; visual yes=0, no=0, unobserved=5", output.getvalue())


if __name__ == "__main__":
    unittest.main()
