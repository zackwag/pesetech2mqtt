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
    / "pesetech_real_device_audit.py"
)
SCRIPT_DIR = SCRIPT_PATH.parent
spec = importlib.util.spec_from_file_location("pesetech_real_device_audit", SCRIPT_PATH)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def write_jsonl(path, events):
    path.write_text("\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n", encoding="utf-8")


def write_config(path):
    path.write_text(
        "\n".join(
            [
                "mqtt:",
                "  broker: homeassistant.local",
                "  port: 1883",
                "  discovery_prefix: homeassistant",
                "  node_id: mqtt_mesh",
                "mesh:",
                "  skylight:",
                "    uuid: 00112233-4455-6677-8899-aabbccddeeff",
                "    name: Pesetech Skylight",
                "    type: pesetech_skylight",
                "",
            ]
        ),
        encoding="utf-8",
    )


def mqtt_events(run_id="proof-1", observed=True):
    topic = "homeassistant/light/mqtt_mesh/skylight"
    steps = [
        ("on", "2026-06-28T00:00:00+0000", {"state": "ON"}, {"state": "ON"}),
        ("brightness", "2026-06-28T00:00:01+0000", {"state": "ON", "brightness": 32640}, {"state": "ON", "brightness": 32640}),
        ("warm", "2026-06-28T00:00:02+0000", {"state": "ON", "color_temp": 556}, {"state": "ON", "color_temp": 556}),
        ("cool", "2026-06-28T00:00:03+0000", {"state": "ON", "color_temp": 100}, {"state": "ON", "color_temp": 100}),
        ("off", "2026-06-28T00:00:04+0000", {"state": "OFF"}, {"state": "OFF"}),
    ]
    return [
        {
            "timestamp": timestamp,
            "run_id": run_id,
            "step": name,
            "command_topic": f"{topic}/set",
            "state_topic": f"{topic}/state",
            "payload": payload,
            "expected_state": expected,
            "matched_state": expected,
            "publish": {"rc": 0, "published": True},
            "observed": observed,
            "precondition_visible_start": True,
        }
        for name, timestamp, payload, expected in steps
    ]


def ha_events(run_id="proof-1", observed=True):
    steps = [
        (
            "on",
            "2026-06-28T00:01:00+0000",
            "light.turn_on",
            "/api/services/light/turn_on",
            {"entity_id": "light.skylight"},
            {"state": "on"},
            {"state": "ON"},
            [],
        ),
        (
            "brightness",
            "2026-06-28T00:01:01+0000",
            "light.turn_on",
            "/api/services/light/turn_on",
            {"entity_id": "light.skylight", "brightness": 192},
            {"state": "on", "attributes": {"brightness": 192}},
            {"state": "ON", "brightness": 49152},
            ["brightness"],
        ),
        (
            "warm",
            "2026-06-28T00:01:02+0000",
            "light.turn_on",
            "/api/services/light/turn_on",
            {"entity_id": "light.skylight", "color_temp_kelvin": 2200},
            {"state": "on", "attributes": {"color_temp_kelvin": 2200}},
            {"state": "ON", "color_temp": 455},
            ["color_temp"],
        ),
        (
            "cool",
            "2026-06-28T00:01:03+0000",
            "light.turn_on",
            "/api/services/light/turn_on",
            {"entity_id": "light.skylight", "color_temp_kelvin": 6500},
            {"state": "on", "attributes": {"color_temp_kelvin": 6500}},
            {"state": "ON", "color_temp": 154},
            ["color_temp"],
        ),
        (
            "off",
            "2026-06-28T00:01:04+0000",
            "light.turn_off",
            "/api/services/light/turn_off",
            {"entity_id": "light.skylight"},
            {"state": "off"},
            {"state": "OFF"},
            [],
        ),
    ]
    events = []
    for name, timestamp, service, path, payload, expected_state, mqtt_state, fields in steps:
        expected_mqtt_attributes = audit.ha_verify.expected_mqtt_attributes_for_step(
            {"step": name, "service": service, "payload": payload}
        )
        events.append(
            {
                "timestamp": timestamp,
                "run_id": run_id,
                "step": name,
                "home_assistant_url": "http://homeassistant.local:8123",
                "entity_id": "light.skylight",
                "service": service,
                "service_path": path,
                "payload": payload,
                "expected_state": expected_state,
                "matched_state": {
                    "entity_id": "light.skylight",
                    "state": expected_state["state"],
                    "attributes": expected_state.get("attributes", {}),
                },
                "response": {"status": 200, "response": []},
                "mqtt_state_topic": "homeassistant/light/mqtt_mesh/skylight/state",
                "expected_mqtt_state": {"state": "OFF" if name == "off" else "ON"},
                "expected_mqtt_attributes": expected_mqtt_attributes,
                "required_mqtt_fields": fields,
                "matched_mqtt_state": mqtt_state,
                "observed": observed,
                "precondition_visible_start": True,
            }
        )
    return events


class PesetechRealDeviceAuditTest(unittest.TestCase):
    def base_args(self, temp_path):
        return types.SimpleNamespace(
            config=str(temp_path / "config.yaml"),
            proof_log=str(temp_path / "pesetech-proof.jsonl"),
            ha_proof_log=str(temp_path / "pesetech-ha-service-proof.jsonl"),
            proof_run_id=None,
            discovery_prefix=None,
            mesh_topic=None,
            device_id=None,
            mqtt_brightness=32640,
            ha_url="http://homeassistant.local:8123",
            ha_entity_id="light.skylight",
            ha_brightness=192,
            ha_warm_kelvin=2200,
            ha_cool_kelvin=6500,
            ha_brightness_tolerance=2,
            ha_kelvin_tolerance=150,
            ha_mqtt_brightness_scale=audit.ha_verify.DEFAULT_MQTT_BRIGHTNESS_SCALE,
            ha_mqtt_brightness_tolerance=audit.ha_verify.DEFAULT_MQTT_BRIGHTNESS_TOLERANCE,
            ha_mqtt_mired_tolerance=audit.ha_verify.DEFAULT_MQTT_MIRED_TOLERANCE,
            allow_unobserved=False,
            allow_different_run_ids=False,
            allow_missing_timestamps=False,
            allow_out_of_order_phases=False,
            allow_missing_ha_attributes=False,
            allow_missing_ha_mqtt_attributes=False,
            allow_missing_ha_mqtt_topic=False,
            output_json=None,
        )

    def write_proofs(self, temp_path, mqtt_run_id="proof-1", ha_run_id="proof-1", observed=True):
        write_config(temp_path / "config.yaml")
        write_jsonl(temp_path / "pesetech-proof.jsonl", mqtt_events(run_id=mqtt_run_id, observed=observed))
        write_jsonl(temp_path / "pesetech-ha-service-proof.jsonl", ha_events(run_id=ha_run_id, observed=observed))

    def test_audit_passes_for_strict_shared_run_visual_proof(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_proofs(temp_path)
            args = self.base_args(temp_path)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = audit.audit(args)

        self.assertEqual(exit_code, 0)
        self.assertIn("MQTT command proof: PASS", output.getvalue())
        self.assertIn("Home Assistant service proof: PASS", output.getvalue())
        self.assertIn("on/off: PASS", output.getvalue())
        self.assertIn("Final audit passed.", output.getvalue())
        self.assertIn("Home Assistant control of the real Pesetech skylight is proven", output.getvalue())

    def test_audit_writes_json_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_proofs(temp_path)
            args = self.base_args(temp_path)
            args.output_json = str(temp_path / "pesetech-final-audit.json")

            self.assertEqual(audit.audit(args), 0)

            report = json.loads(Path(args.output_json).read_text(encoding="utf-8"))
            self.assertTrue(report["passed"])
            self.assertTrue(report["objective_proven"])
            self.assertTrue(report["strict_visual_proof"])
            self.assertTrue(report["objective"]["proven"])
            self.assertTrue(report["objective"]["technical_state_proven"])
            self.assertEqual(report["objective"]["proof_run_id"], "proof-1")
            self.assertEqual(report["objective"]["ha_entity_id"], "light.skylight")
            self.assertEqual(report["objective"]["mqtt_command_topic"], "homeassistant/light/mqtt_mesh/skylight/set")
            self.assertEqual(report["objective"]["mqtt_state_topic"], "homeassistant/light/mqtt_mesh/skylight/state")
            self.assertIn("Objective proven", report["objective"]["next_action"])
            self.assertEqual(report["target"]["ha_entity_id"], "light.skylight")
            self.assertEqual(report["requirements"]["brightness"], True)
            self.assertEqual(report["checks"][0]["run_id"], "proof-1")
            self.assertEqual(
                report["checks"][0]["summary"],
                {
                    "total": 5,
                    "mqtt_state_matched": 5,
                    "observed_yes": 5,
                    "observed_no": 0,
                    "observed_missing": 0,
                },
            )
            self.assertEqual(report["checks"][0]["steps"][0]["step"], "on")
            self.assertEqual(report["checks"][0]["steps"][1]["payload"], {"state": "ON", "brightness": 32640})
            self.assertEqual(report["checks"][0]["steps"][1]["matched_state"], {"state": "ON", "brightness": 32640})
            self.assertEqual(report["checks"][0]["steps"][1]["publish"], {"rc": 0, "published": True})
            self.assertEqual(report["checks"][0]["steps"][1]["precondition_visible_start"], True)
            self.assertEqual(
                report["checks"][1]["summary"],
                {
                    "total": 5,
                    "ha_state_matched": 5,
                    "mqtt_state_matched": 5,
                    "service_ok": 5,
                    "observed_yes": 5,
                    "observed_no": 0,
                    "observed_missing": 0,
                },
            )
            self.assertEqual(report["checks"][1]["steps"][1]["response_status"], 200)
            self.assertEqual(report["checks"][1]["steps"][1]["expected_mqtt_attributes"], {"brightness": 49152})
            self.assertEqual(report["checks"][1]["steps"][1]["matched_mqtt_state"], {"state": "ON", "brightness": 49152})

    def test_allow_unobserved_passes_but_does_not_claim_strict_visual_proof(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_proofs(temp_path, observed=None)
            args = self.base_args(temp_path)
            args.allow_unobserved = True
            args.output_json = str(temp_path / "pesetech-final-audit.json")
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = audit.audit(args)

            report = json.loads(Path(args.output_json).read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertTrue(report["passed"])
        self.assertFalse(report["objective_proven"])
        self.assertFalse(report["strict_visual_proof"])
        self.assertFalse(report["objective"]["proven"])
        self.assertTrue(report["objective"]["technical_state_proven"])
        self.assertIn("visually confirmed", report["objective"]["next_action"])
        self.assertIn("Technical Home Assistant/MQTT proof passed", output.getvalue())
        self.assertIn("strict real-light visual proof is still required", output.getvalue())
        self.assertNotIn("Home Assistant control of the real Pesetech skylight is proven", output.getvalue())

    def test_audit_fails_when_visual_observation_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_proofs(temp_path, observed=None)
            args = self.base_args(temp_path)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = audit.audit(args)

        self.assertEqual(exit_code, 1)
        self.assertIn("real-light observation was not confirmed true", output.getvalue())
        self.assertIn("Final audit failed:", output.getvalue())

    def test_audit_fails_when_proof_logs_do_not_share_run_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_proofs(temp_path, mqtt_run_id="mqtt-proof", ha_run_id="ha-proof")
            args = self.base_args(temp_path)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = audit.audit(args)

        self.assertEqual(exit_code, 1)
        self.assertIn("proof logs do not share the same run_id", output.getvalue())

    def test_audit_fails_when_proof_logs_have_no_run_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_proofs(temp_path, mqtt_run_id=None, ha_run_id=None)
            args = self.base_args(temp_path)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = audit.audit(args)

        self.assertEqual(exit_code, 1)
        self.assertIn("MQTT command proof is missing run_id", output.getvalue())
        self.assertIn("Home Assistant service proof is missing run_id", output.getvalue())

    def test_audit_fails_when_one_step_is_missing_run_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            write_config(temp_path / "config.yaml")
            mqtt = mqtt_events(run_id="proof-1", observed=True)
            mqtt[2].pop("run_id")
            write_jsonl(temp_path / "pesetech-proof.jsonl", mqtt)
            write_jsonl(temp_path / "pesetech-ha-service-proof.jsonl", ha_events(run_id="proof-1", observed=True))
            args = self.base_args(temp_path)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = audit.audit(args)

        self.assertEqual(exit_code, 1)
        self.assertIn("MQTT command proof is missing run_id on steps: warm", output.getvalue())

    def test_audit_fails_when_timestamp_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            write_config(temp_path / "config.yaml")
            mqtt = mqtt_events(run_id="proof-1", observed=True)
            mqtt[0].pop("timestamp")
            write_jsonl(temp_path / "pesetech-proof.jsonl", mqtt)
            write_jsonl(temp_path / "pesetech-ha-service-proof.jsonl", ha_events(run_id="proof-1", observed=True))
            args = self.base_args(temp_path)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = audit.audit(args)

        self.assertEqual(exit_code, 1)
        self.assertIn("MQTT command proof has missing or invalid timestamp on step on", output.getvalue())

    def test_audit_fails_when_timestamp_is_invalid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            write_config(temp_path / "config.yaml")
            ha = ha_events(run_id="proof-1", observed=True)
            ha[3]["timestamp"] = "not-a-timestamp"
            write_jsonl(temp_path / "pesetech-proof.jsonl", mqtt_events(run_id="proof-1", observed=True))
            write_jsonl(temp_path / "pesetech-ha-service-proof.jsonl", ha)
            args = self.base_args(temp_path)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = audit.audit(args)

        self.assertEqual(exit_code, 1)
        self.assertIn("Home Assistant service proof has missing or invalid timestamp on step cool", output.getvalue())

    def test_audit_fails_when_timestamp_has_no_timezone(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            write_config(temp_path / "config.yaml")
            ha = ha_events(run_id="proof-1", observed=True)
            ha[0]["timestamp"] = "2026-06-28T00:01:00"
            write_jsonl(temp_path / "pesetech-proof.jsonl", mqtt_events(run_id="proof-1", observed=True))
            write_jsonl(temp_path / "pesetech-ha-service-proof.jsonl", ha)
            args = self.base_args(temp_path)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = audit.audit(args)

        self.assertEqual(exit_code, 1)
        self.assertIn("Home Assistant service proof has missing or invalid timestamp on step on", output.getvalue())

    def test_audit_fails_when_timestamps_go_backwards(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            write_config(temp_path / "config.yaml")
            mqtt = mqtt_events(run_id="proof-1", observed=True)
            mqtt[3]["timestamp"] = "2026-06-27T23:59:59+0000"
            write_jsonl(temp_path / "pesetech-proof.jsonl", mqtt)
            write_jsonl(temp_path / "pesetech-ha-service-proof.jsonl", ha_events(run_id="proof-1", observed=True))
            args = self.base_args(temp_path)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = audit.audit(args)

        self.assertEqual(exit_code, 1)
        self.assertIn("MQTT command proof timestamps are not chronological", output.getvalue())
        self.assertIn("cool", output.getvalue())
        self.assertIn("warm", output.getvalue())

    def test_audit_fails_when_ha_proof_starts_before_mqtt_proof_finishes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            write_config(temp_path / "config.yaml")
            ha = ha_events(run_id="proof-1", observed=True)
            for index, event in enumerate(ha):
                event["timestamp"] = f"2026-06-28T00:00:0{index}+0000"
            write_jsonl(temp_path / "pesetech-proof.jsonl", mqtt_events(run_id="proof-1", observed=True))
            write_jsonl(temp_path / "pesetech-ha-service-proof.jsonl", ha)
            args = self.base_args(temp_path)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = audit.audit(args)

        self.assertEqual(exit_code, 1)
        self.assertIn("Home Assistant service proof starts before MQTT command proof finishes", output.getvalue())

    def test_audit_fails_when_ha_mqtt_topic_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            write_config(temp_path / "config.yaml")
            ha = ha_events(run_id="proof-1", observed=True)
            ha[0].pop("mqtt_state_topic")
            write_jsonl(temp_path / "pesetech-proof.jsonl", mqtt_events(run_id="proof-1", observed=True))
            write_jsonl(temp_path / "pesetech-ha-service-proof.jsonl", ha)
            args = self.base_args(temp_path)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = audit.audit(args)

        self.assertEqual(exit_code, 1)
        self.assertIn("Home Assistant service proof on: mqtt_state_topic None !=", output.getvalue())

    def test_audit_fails_when_ha_mqtt_topic_targets_different_device(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            write_config(temp_path / "config.yaml")
            ha = ha_events(run_id="proof-1", observed=True)
            ha[2]["mqtt_state_topic"] = "homeassistant/light/mqtt_mesh/other_light/state"
            write_jsonl(temp_path / "pesetech-proof.jsonl", mqtt_events(run_id="proof-1", observed=True))
            write_jsonl(temp_path / "pesetech-ha-service-proof.jsonl", ha)
            args = self.base_args(temp_path)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = audit.audit(args)

        self.assertEqual(exit_code, 1)
        self.assertIn("Home Assistant service proof warm: mqtt_state_topic", output.getvalue())
        self.assertIn("other_light", output.getvalue())
        self.assertIn("skylight/state", output.getvalue())


if __name__ == "__main__":
    unittest.main()
