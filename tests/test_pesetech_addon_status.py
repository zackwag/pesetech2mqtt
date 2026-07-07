import importlib.util
import io
import json
import os
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "pesetech_addon_status.py"
)
spec = importlib.util.spec_from_file_location("pesetech_addon_status", SCRIPT_PATH)
addon_status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(addon_status)


class PesetechAddonStatusTest(unittest.TestCase):
    def args(self, temp_path):
        return types.SimpleNamespace(
            mesh_json=str(temp_path / "pesetech_mesh.json"),
            config=str(temp_path / "config.yaml"),
            store=str(temp_path / "store.yaml"),
            cloud_report=str(temp_path / "pesetech_cloud_fetch_report.json"),
            import_check_report=str(temp_path / "pesetech-import-check.json"),
            runtime_report=str(temp_path / "pesetech-runtime-check.json"),
            mesh_daemon_report=str(temp_path / "pesetech-mesh-daemon-check.json"),
            preflight_report=str(temp_path / "pesetech-preflight.json"),
            readiness_report=str(temp_path / "pesetech-readiness.json"),
            proof_log=str(temp_path / "pesetech-move-test.jsonl"),
            ha_proof_log=str(temp_path / "pesetech-ha-service-proof.jsonl"),
            final_audit_report=str(temp_path / "pesetech-final-audit.json"),
            ha_url="http://supervisor/core",
            ha_entity_id="light.skylight",
            mqtt_source="supervisor",
            mqtt_broker="",
            mqtt_port=1883,
            discovery_prefix="homeassistant",
            mesh_topic="mqtt_mesh",
            device_id="skylight",
            import_mesh_candidate=0,
            import_node_uuid="",
            import_node_unicast="",
            import_local_address="",
        )

    def write_json(self, path, payload):
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    def write_runtime_pass(self, temp_path):
        self.write_json(temp_path / "pesetech-runtime-check.json", {"status": "passed", "exit_code": 0})

    def write_mesh_daemon_pass(self, temp_path):
        self.write_json(
            temp_path / "pesetech-mesh-daemon-check.json",
            {
                "status": "passed",
                "sent_light_commands": False,
                "published_mqtt": False,
                "provisioned": False,
                "imported": False,
            },
        )

    def write_imported_state(self, temp_path):
        (temp_path / "config.yaml").write_text(
            "\n".join(
                [
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
        (temp_path / "store.yaml").write_text("nodes: {}\n", encoding="utf-8")

    def write_preflight_pass(self, temp_path):
        self.write_json(
            temp_path / "pesetech-preflight.json",
            {
                "status": "passed",
                "operation": "preflight",
                "exit_code": 0,
                "sent_light_commands": False,
                "published_mqtt": False,
            },
        )

    def write_readiness_pass(self, temp_path):
        self.write_json(temp_path / "pesetech-readiness.json", {"status": "passed", "sent_light_commands": False})

    def write_import_check_pass(self, temp_path, **requested_overrides):
        requested = {
            "mesh_candidate": 0,
            "node_uuid": "",
            "node_unicast": "",
            "local_address": "",
            "device_id": "skylight",
            "default_entity_id": "light.skylight",
        }
        requested.update(requested_overrides)
        self.write_json(
            temp_path / "pesetech-import-check.json",
            {
                "status": "passed",
                "operation": "import-check",
                "dry_run": True,
                "sent_light_commands": False,
                "published_mqtt": False,
                "wrote_files": False,
                "source": str(temp_path / "pesetech_mesh.json"),
                "requested": requested,
                "selected_node": {
                    "uuid": "00112233-4455-6677-8899-aabbccddeeff",
                    "name": "Skylight",
                    "unicast": "0002",
                    "element_count": 3,
                    "models": ["1000", "1300", "1303", "1306"],
                },
            },
        )

    def set_mtime(self, path, timestamp):
        os.utime(path, (timestamp, timestamp))

    def write_move_proof(self, path, *, matched=True):
        steps = [
            ("on", {"state": "ON"}),
            ("brightness", {"state": "ON", "brightness": 32640}),
            ("warm", {"state": "ON", "color_temp": 1250}),
            ("cool", {"state": "ON", "color_temp": 50}),
            ("off", {"state": "OFF"}),
        ]
        events = []
        for step, expected_state in steps:
            events.append(
                {
                    "step": step,
                    "run_id": "move-1",
                    "command_topic": "homeassistant/light/mqtt_mesh/skylight/set",
                    "state_topic": "homeassistant/light/mqtt_mesh/skylight/state",
                    "expected_state": expected_state,
                    "matched_state": expected_state if matched else None,
                    "publish": {"rc": 0, "published": True, "error": None},
                    "observed": None,
                    "precondition_visible_start": True,
                }
            )
        path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")

    def write_ha_proof(self, path, *, matched=True, mqtt_attributes_matched=True):
        steps = [
            ("on", "light.turn_on", {"state": "on"}, {"state": "ON"}, [], {}),
            (
                "brightness",
                "light.turn_on",
                {"state": "on", "attributes": {"brightness": 192}},
                {"state": "ON"},
                ["brightness"],
                {"brightness": 49152},
            ),
            (
                "warm",
                "light.turn_on",
                {"state": "on", "attributes": {"color_temp_kelvin": 2200}},
                {"state": "ON"},
                ["color_temp"],
                {"color_temp": 455},
            ),
            (
                "cool",
                "light.turn_on",
                {"state": "on", "attributes": {"color_temp_kelvin": 6500}},
                {"state": "ON"},
                ["color_temp"],
                {"color_temp": 154},
            ),
            ("off", "light.turn_off", {"state": "off"}, {"state": "OFF"}, [], {}),
        ]
        events = []
        for step, service, expected_state, expected_mqtt_state, required_mqtt_fields, expected_mqtt_attributes in steps:
            matched_mqtt_state = dict(expected_mqtt_state)
            for field, value in expected_mqtt_attributes.items():
                matched_mqtt_state[field] = value if mqtt_attributes_matched else value + 1000
            events.append(
                {
                    "step": step,
                    "run_id": "ha-1",
                    "home_assistant_url": "http://supervisor/core",
                    "auth_source": "SUPERVISOR_TOKEN",
                    "entity_id": "light.skylight",
                    "service": service,
                    "expected_state": expected_state,
                    "matched_state": expected_state if matched else None,
                    "mqtt_state_topic": "homeassistant/light/mqtt_mesh/skylight/state",
                    "expected_mqtt_state": expected_mqtt_state,
                    "matched_mqtt_state": matched_mqtt_state if matched else None,
                    "required_mqtt_fields": required_mqtt_fields,
                    "expected_mqtt_attributes": expected_mqtt_attributes,
                    "response": {"status": 200},
                    "observed": None,
                    "precondition_visible_start": True,
                }
            )
        path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")

    def test_no_files_suggests_runtime_check_and_stays_read_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report = addon_status.build_status(self.args(Path(temp_dir)))

        self.assertEqual(report["suggested_next_operation"], "runtime-check")
        self.assertTrue(report["read_only"])
        self.assertFalse(report["sent_light_commands"])
        self.assertFalse(report["published_mqtt"])
        self.assertEqual(report["next_operation"]["configuration_snippet"], "operation: runtime-check")
        self.assertTrue(report["next_operation"]["no_motion_gate"])
        self.assertFalse(report["next_operation"]["moves_real_light"])

    def test_cloud_candidate_failure_suggests_cloud_fetch_with_candidate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_json(
                temp_path / "pesetech_cloud_fetch_report.json",
                {"status": "candidate-selection-failed", "candidate_count": 3, "selected_candidate": None},
            )

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "cloud-fetch")
        self.assertIn("cloud_candidate", report["next_action"])
        self.assertEqual(report["reports"]["cloud_fetch"]["candidate_count"], 3)
        self.assertIn("/share/pesetech_cloud_token.txt", " ".join(report["next_operation"]["notes"]))

    def test_cloud_failure_with_homes_suggests_cloud_home_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_json(
                temp_path / "pesetech_cloud_fetch_report.json",
                {
                    "status": "endpoint-fetch-failed",
                    "home_count": 1,
                    "homes": [{"home_id": "home-1", "name": "Studio", "source": "home-list"}],
                    "candidate_count": 0,
                    "error": "mesh endpoint failed",
                },
            )

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "cloud-fetch")
        self.assertIn("cloud_home_id", report["next_action"])
        self.assertEqual(report["reports"]["cloud_fetch"]["home_count"], 1)
        self.assertEqual(report["reports"]["cloud_fetch"]["homes"][0]["home_id"], "home-1")

    def test_mesh_json_without_runtime_report_suggests_runtime_check(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "pesetech_mesh.json").write_text('{"mesh": true}\n', encoding="utf-8")

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "runtime-check")

    def test_mesh_json_after_setup_gates_suggests_import_check(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "pesetech_mesh.json").write_text('{"mesh": true}\n', encoding="utf-8")
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "import-check")

    def test_mesh_json_after_zero_scan_still_suggests_import_check(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "pesetech_mesh.json").write_text('{"mesh": true}\n', encoding="utf-8")
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)
            self.write_json(
                temp_path / "pesetech-mesh-scan.json",
                {
                    "operation": "scan",
                    "status": "passed",
                    "found_count": 0,
                    "unprovisioned_uuids": [],
                    "sent_light_commands": False,
                    "published_mqtt": False,
                },
            )

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "import-check")
        self.assertIn("Mesh JSON exists", report["next_action"])

    def test_passed_import_check_after_setup_gates_suggests_import(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "pesetech_mesh.json").write_text('{"mesh": true}\n', encoding="utf-8")
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)
            self.write_import_check_pass(temp_path)

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "import")
        self.assertTrue(report["reports"]["import_check"]["passed"])
        self.assertFalse(report["next_operation"]["moves_real_light"])
        self.assertFalse(report["next_operation"]["no_motion_gate"])
        self.assertIn("/data/config.yaml", " ".join(report["next_operation"]["notes"]))

    def test_stale_import_check_suggests_import_check_again(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "pesetech_mesh.json").write_text('{"mesh": true}\n', encoding="utf-8")
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)
            self.write_import_check_pass(temp_path)
            self.set_mtime(temp_path / "pesetech-import-check.json", 100)
            self.set_mtime(temp_path / "pesetech_mesh.json", 200)

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "import-check")
        self.assertTrue(report["reports"]["import_check"]["stale"])
        self.assertFalse(report["reports"]["import_check"]["passed"])
        self.assertIn("stale", report["next_action"])

    def test_import_check_with_different_options_suggests_import_check_again(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "pesetech_mesh.json").write_text('{"mesh": true}\n', encoding="utf-8")
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)
            self.write_import_check_pass(temp_path, mesh_candidate=2)

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "import-check")
        self.assertEqual(report["reports"]["import_check"]["context_mismatches"], ["mesh_candidate"])
        self.assertFalse(report["reports"]["import_check"]["passed"])

    def test_import_check_with_wrong_source_suggests_import_check_again(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "pesetech_mesh.json").write_text('{"mesh": true}\n', encoding="utf-8")
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)
            self.write_import_check_pass(temp_path)
            report_data = json.loads((temp_path / "pesetech-import-check.json").read_text(encoding="utf-8"))
            report_data["source"] = str(temp_path / "old_mesh.json")
            self.write_json(temp_path / "pesetech-import-check.json", report_data)

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "import-check")
        self.assertEqual(report["reports"]["import_check"]["integrity_errors"], ["source"])
        self.assertFalse(report["reports"]["import_check"]["passed"])
        self.assertIn("does not match the current mesh JSON", report["next_action"])

    def test_import_check_without_selected_node_suggests_import_check_again(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "pesetech_mesh.json").write_text('{"mesh": true}\n', encoding="utf-8")
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)
            self.write_import_check_pass(temp_path)
            report_data = json.loads((temp_path / "pesetech-import-check.json").read_text(encoding="utf-8"))
            report_data.pop("selected_node")
            self.write_json(temp_path / "pesetech-import-check.json", report_data)

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "import-check")
        self.assertEqual(report["reports"]["import_check"]["integrity_errors"], ["selected_node"])
        self.assertFalse(report["reports"]["import_check"]["passed"])

    def test_import_check_that_wrote_files_suggests_import_check_again(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "pesetech_mesh.json").write_text('{"mesh": true}\n', encoding="utf-8")
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)
            self.write_import_check_pass(temp_path)
            report_data = json.loads((temp_path / "pesetech-import-check.json").read_text(encoding="utf-8"))
            report_data["wrote_files"] = True
            self.write_json(temp_path / "pesetech-import-check.json", report_data)

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "import-check")
        self.assertEqual(report["reports"]["import_check"]["integrity_errors"], ["wrote_files"])
        self.assertFalse(report["reports"]["import_check"]["passed"])

    def test_failed_import_check_suggests_import_check_again(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "pesetech_mesh.json").write_text('{"mesh": true}\n', encoding="utf-8")
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)
            self.write_json(
                temp_path / "pesetech-import-check.json",
                {
                    "status": "failed",
                    "operation": "import-check",
                    "dry_run": True,
                    "sent_light_commands": False,
                    "published_mqtt": False,
                    "requested": {
                        "mesh_candidate": 0,
                        "node_uuid": "",
                        "node_unicast": "",
                        "local_address": "",
                        "device_id": "skylight",
                        "default_entity_id": "light.skylight",
                    },
                    "error": "multiple meshes",
                },
            )

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "import-check")
        self.assertEqual(report["reports"]["import_check"]["status"], "failed")
        self.assertIn("previous import-check failed", report["next_action"])

    def test_imported_state_with_failed_mesh_daemon_suggests_mesh_daemon_check(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_imported_state(temp_path)
            self.write_runtime_pass(temp_path)
            self.write_json(temp_path / "pesetech-mesh-daemon-check.json", {"status": "failed"})

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "mesh-daemon-check")

    def test_imported_state_without_runtime_report_suggests_runtime_check(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_imported_state(temp_path)

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "runtime-check")

    def test_imported_state_without_mesh_daemon_pass_suggests_mesh_daemon_check(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_imported_state(temp_path)
            self.write_runtime_pass(temp_path)

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "mesh-daemon-check")

    def test_imported_state_after_setup_gates_suggests_preflight(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_imported_state(temp_path)
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "preflight")

    def test_preflight_pass_after_imported_state_suggests_readiness_test(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_imported_state(temp_path)
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)
            self.write_preflight_pass(temp_path)

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "readiness-test")
        self.assertFalse(report["reports"]["preflight"]["stale"])

    def test_failed_preflight_after_imported_state_suggests_preflight_again(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_imported_state(temp_path)
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)
            self.write_json(
                temp_path / "pesetech-preflight.json",
                {"status": "failed", "operation": "preflight", "exit_code": 1, "sent_light_commands": False},
            )

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "preflight")
        self.assertIn("Preflight failed", report["next_action"])

    def test_stale_preflight_does_not_advance_to_readiness_test(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_imported_state(temp_path)
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)
            self.write_preflight_pass(temp_path)
            self.set_mtime(temp_path / "pesetech-preflight.json", 100)
            self.set_mtime(temp_path / "config.yaml", 200)

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "preflight")
        self.assertTrue(report["reports"]["preflight"]["stale"])

    def test_readiness_passed_suggests_move_test(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_imported_state(temp_path)
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)
            self.write_preflight_pass(temp_path)
            self.write_readiness_pass(temp_path)

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "move-test")
        self.assertEqual(report["next_operation"]["configuration_snippet"], "operation: move-test")
        self.assertTrue(report["next_operation"]["moves_real_light"])
        self.assertFalse(report["next_operation"]["no_motion_gate"])
        self.assertIn("watching the light", " ".join(report["next_operation"]["notes"]))

    def test_stale_readiness_after_new_preflight_suggests_readiness_test(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_imported_state(temp_path)
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)
            self.write_preflight_pass(temp_path)
            self.write_readiness_pass(temp_path)
            self.set_mtime(temp_path / "config.yaml", 100)
            self.set_mtime(temp_path / "store.yaml", 100)
            self.set_mtime(temp_path / "pesetech-readiness.json", 200)
            self.set_mtime(temp_path / "pesetech-preflight.json", 300)

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "readiness-test")
        self.assertTrue(report["reports"]["readiness"]["stale"])
        self.assertIn("Readiness report is older", report["next_action"])
        self.assertTrue(report["next_operation"]["no_motion_gate"])
        self.assertFalse(report["next_operation"]["moves_real_light"])

    def test_stale_readiness_without_runtime_report_suggests_runtime_check(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_json(temp_path / "pesetech-readiness.json", {"status": "passed", "sent_light_commands": False})

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "runtime-check")

    def test_stale_readiness_without_mesh_daemon_pass_suggests_mesh_daemon_check(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_runtime_pass(temp_path)
            self.write_json(temp_path / "pesetech-readiness.json", {"status": "passed", "sent_light_commands": False})

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "mesh-daemon-check")

    def test_move_proof_suggests_ha_service_test(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_imported_state(temp_path)
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)
            self.write_preflight_pass(temp_path)
            self.write_readiness_pass(temp_path)
            self.write_move_proof(temp_path / "pesetech-move-test.jsonl")

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "ha-service-test")
        self.assertTrue(report["proofs"]["mqtt_move"]["passed"])
        self.assertEqual(report["proofs"]["mqtt_move"]["run_id"], "move-1")
        self.assertEqual(report["proofs"]["mqtt_move"]["observed_unrecorded"], 5)
        self.assertEqual(report["proofs"]["mqtt_move"]["matched_state_steps"], ["on", "brightness", "warm", "cool", "off"])
        self.assertEqual(report["proofs"]["mqtt_move"]["command_topics"], ["homeassistant/light/mqtt_mesh/skylight/set"])

    def test_stale_move_proof_without_setup_gates_suggests_runtime_check(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_move_proof(temp_path / "pesetech-move-test.jsonl")

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "runtime-check")

    def test_failed_move_proof_does_not_advance_past_readiness(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_imported_state(temp_path)
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)
            self.write_preflight_pass(temp_path)
            self.write_readiness_pass(temp_path)
            self.write_move_proof(temp_path / "pesetech-move-test.jsonl", matched=False)

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "move-test")
        self.assertFalse(report["proofs"]["mqtt_move"]["passed"])

    def test_stale_move_proof_does_not_advance_past_new_readiness(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_imported_state(temp_path)
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)
            self.write_preflight_pass(temp_path)
            self.write_move_proof(temp_path / "pesetech-move-test.jsonl")
            self.write_readiness_pass(temp_path)
            self.set_mtime(temp_path / "config.yaml", 100)
            self.set_mtime(temp_path / "store.yaml", 100)
            self.set_mtime(temp_path / "pesetech-preflight.json", 100)
            self.set_mtime(temp_path / "pesetech-move-test.jsonl", 100)
            self.set_mtime(temp_path / "pesetech-readiness.json", 200)

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "move-test")
        self.assertTrue(report["proofs"]["mqtt_move"]["stale"])
        self.assertFalse(report["proofs"]["mqtt_move"]["passed"])

    def test_move_and_ha_proof_suggests_combined_proof_or_host_strict_proof(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_imported_state(temp_path)
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)
            self.write_preflight_pass(temp_path)
            self.write_readiness_pass(temp_path)
            self.write_move_proof(temp_path / "pesetech-move-test.jsonl")
            self.write_ha_proof(temp_path / "pesetech-ha-service-proof.jsonl")

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "proof-test")
        self.assertTrue(report["proofs"]["mqtt_move"]["passed"])
        self.assertTrue(report["proofs"]["ha_service"]["passed"])
        self.assertTrue(report["next_operation"]["moves_real_light"])
        self.assertIn("strict final proof", " ".join(report["next_operation"]["notes"]))
        self.assertEqual(report["proofs"]["ha_service"]["run_id"], "ha-1")
        self.assertEqual(report["proofs"]["ha_service"]["auth_sources"], ["SUPERVISOR_TOKEN"])
        self.assertEqual(report["proofs"]["ha_service"]["entity_ids"], ["light.skylight"])
        self.assertEqual(report["proofs"]["ha_service"]["matched_mqtt_state_steps"], ["on", "brightness", "warm", "cool", "off"])

    def test_failed_ha_proof_does_not_advance_to_combined_proof(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_imported_state(temp_path)
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)
            self.write_preflight_pass(temp_path)
            self.write_readiness_pass(temp_path)
            self.write_move_proof(temp_path / "pesetech-move-test.jsonl")
            self.write_ha_proof(temp_path / "pesetech-ha-service-proof.jsonl", matched=False)

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "ha-service-test")
        self.assertFalse(report["proofs"]["ha_service"]["passed"])

    def test_stale_ha_proof_does_not_advance_past_new_move_proof(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_imported_state(temp_path)
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)
            self.write_preflight_pass(temp_path)
            self.write_readiness_pass(temp_path)
            self.write_ha_proof(temp_path / "pesetech-ha-service-proof.jsonl")
            self.write_move_proof(temp_path / "pesetech-move-test.jsonl")
            self.set_mtime(temp_path / "config.yaml", 100)
            self.set_mtime(temp_path / "store.yaml", 100)
            self.set_mtime(temp_path / "pesetech-preflight.json", 100)
            self.set_mtime(temp_path / "pesetech-readiness.json", 100)
            self.set_mtime(temp_path / "pesetech-ha-service-proof.jsonl", 200)
            self.set_mtime(temp_path / "pesetech-move-test.jsonl", 300)

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "ha-service-test")
        self.assertTrue(report["proofs"]["ha_service"]["stale"])
        self.assertFalse(report["proofs"]["ha_service"]["passed"])

    def test_ha_proof_with_wrong_mqtt_attribute_values_does_not_advance_to_combined_proof(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_imported_state(temp_path)
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)
            self.write_preflight_pass(temp_path)
            self.write_readiness_pass(temp_path)
            self.write_move_proof(temp_path / "pesetech-move-test.jsonl")
            self.write_ha_proof(temp_path / "pesetech-ha-service-proof.jsonl", mqtt_attributes_matched=False)

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "ha-service-test")
        self.assertFalse(report["proofs"]["ha_service"]["passed"])

    def test_empty_move_proof_does_not_advance_gate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "pesetech-move-test.jsonl").write_text("", encoding="utf-8")

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "runtime-check")

    def test_strict_proof_hint_uses_external_placeholders_for_supervisor_mqtt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report = addon_status.build_status(self.args(Path(temp_dir)))

        proof = report["strict_proof"]
        self.assertIn("--readiness-only", proof["readiness_argv"])
        self.assertIn("<home_assistant_url_reachable_from_workstation>", proof["readiness_argv"])
        self.assertIn("<externally_reachable_mqtt_host>", proof["readiness_argv"])
        self.assertNotIn("--readiness-only", proof["full_argv"])
        self.assertIn("supervisor", proof["mqtt_source"])
        self.assertIn("not http://supervisor/core", " ".join(proof["notes"]))
        self.assertIn("external broker", " ".join(proof["notes"]))
        self.assertIn("HOME_ASSISTANT_TOKEN", " ".join(proof["notes"]))

    def test_strict_proof_hint_uses_manual_broker_and_topic_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            args = self.args(Path(temp_dir))
            args.ha_url = "http://ha.local:8123"
            args.ha_entity_id = "light.studio_skylight"
            args.mqtt_source = "manual"
            args.mqtt_broker = "192.0.2.20"
            args.mqtt_port = 1884
            args.discovery_prefix = "ha"
            args.mesh_topic = "pesetech_mesh"
            args.device_id = "studio_sky"

            report = addon_status.build_status(args)

        proof = report["strict_proof"]
        self.assertIn("http://ha.local:8123", proof["full_argv"])
        self.assertIn("light.studio_skylight", proof["full_argv"])
        self.assertIn("192.0.2.20", proof["full_argv"])
        self.assertIn("1884", proof["full_argv"])
        self.assertIn("ha", proof["full_argv"])
        self.assertIn("pesetech_mesh", proof["full_argv"])
        self.assertIn("studio_sky", proof["full_argv"])

    def test_non_strict_audit_suggests_service_but_names_host_proof(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_json(
                temp_path / "pesetech-final-audit.json",
                {"passed": True, "objective_proven": False, "strict_visual_proof": False, "proof_run_id": "addon-1"},
            )

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "service")
        self.assertFalse(report["reports"]["final_audit"]["objective_proven"])
        self.assertIn("strict visual proof is false", report["next_action"])
        self.assertIn("host prove-ha-addon", report["next_action"])

    def test_stale_final_audit_does_not_override_newer_proof_logs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_imported_state(temp_path)
            self.write_runtime_pass(temp_path)
            self.write_mesh_daemon_pass(temp_path)
            self.write_preflight_pass(temp_path)
            self.write_readiness_pass(temp_path)
            self.write_json(
                temp_path / "pesetech-final-audit.json",
                {
                    "passed": True,
                    "objective_proven": True,
                    "strict_visual_proof": True,
                    "proof_run_id": "old-host-1",
                    "objective": {
                        "technical_state_proven": True,
                        "next_action": "Objective proven. Leave the gateway running in service mode for Home Assistant control.",
                    },
                    "requirements": {"on/off": True, "brightness": True, "color_temperature": True},
                },
            )
            self.write_move_proof(temp_path / "pesetech-move-test.jsonl")
            self.write_ha_proof(temp_path / "pesetech-ha-service-proof.jsonl")
            self.set_mtime(temp_path / "config.yaml", 100)
            self.set_mtime(temp_path / "store.yaml", 100)
            self.set_mtime(temp_path / "pesetech-preflight.json", 100)
            self.set_mtime(temp_path / "pesetech-readiness.json", 100)
            self.set_mtime(temp_path / "pesetech-final-audit.json", 200)
            self.set_mtime(temp_path / "pesetech-move-test.jsonl", 300)
            self.set_mtime(temp_path / "pesetech-ha-service-proof.jsonl", 400)

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "proof-test")
        self.assertTrue(report["reports"]["final_audit"]["stale"])
        self.assertFalse(report["reports"]["final_audit"]["passed"])
        self.assertFalse(report["reports"]["final_audit"]["objective_proven"])
        self.assertFalse(report["reports"]["final_audit"]["technical_state_proven"])
        self.assertEqual(report["reports"]["final_audit"]["errors_count"], 0)
        self.assertIn("older than current setup/proof inputs", report["reports"]["final_audit"]["next_action"])

    def test_strict_audit_without_objective_proven_requests_host_proof_again(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_json(
                temp_path / "pesetech-final-audit.json",
                {"passed": True, "objective_proven": False, "strict_visual_proof": True, "proof_run_id": "host-1"},
            )

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "service")
        self.assertFalse(report["reports"]["final_audit"]["objective_proven"])
        self.assertIn("objective_proven is not true", report["next_action"])
        self.assertIn("host prove-ha-addon", report["next_action"])

    def test_strict_audit_suggests_service(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self.write_json(
                temp_path / "pesetech-final-audit.json",
                {
                    "passed": True,
                    "objective_proven": True,
                    "strict_visual_proof": True,
                    "proof_run_id": "host-1",
                    "objective": {
                        "technical_state_proven": True,
                        "next_action": "Objective proven. Leave the gateway running in service mode for Home Assistant control.",
                    },
                    "requirements": {"on/off": True, "brightness": True, "color_temperature": True},
                },
            )

            report = addon_status.build_status(self.args(temp_path))

        self.assertEqual(report["suggested_next_operation"], "service")
        self.assertTrue(report["reports"]["final_audit"]["objective_proven"])
        self.assertTrue(report["reports"]["final_audit"]["technical_state_proven"])
        self.assertEqual(report["reports"]["final_audit"]["requirements"]["on/off"], True)
        self.assertIn("objective_proven true", report["next_action"])

    def test_main_writes_status_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output = temp_path / "status.json"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = addon_status.main(
                    [
                        "--mesh-json",
                        str(temp_path / "missing-mesh.json"),
                        "--config",
                        str(temp_path / "missing-config.yaml"),
                        "--store",
                        str(temp_path / "missing-store.yaml"),
                        "--cloud-report",
                        str(temp_path / "missing-cloud.json"),
                        "--runtime-report",
                        str(temp_path / "missing-runtime.json"),
                        "--mesh-daemon-report",
                        str(temp_path / "missing-mesh-daemon.json"),
                        "--preflight-report",
                        str(temp_path / "missing-preflight.json"),
                        "--readiness-report",
                        str(temp_path / "missing-readiness.json"),
                        "--proof-log",
                        str(temp_path / "missing-proof.jsonl"),
                        "--ha-proof-log",
                        str(temp_path / "missing-ha-proof.jsonl"),
                        "--final-audit-report",
                        str(temp_path / "missing-final-audit.json"),
                        "--ha-url",
                        "http://ha.local:8123",
                        "--ha-entity-id",
                        "light.studio_skylight",
                        "--mqtt-source",
                        "manual",
                        "--mqtt-broker",
                        "192.0.2.20",
                        "--mqtt-port",
                        "1884",
                        "--discovery-prefix",
                        "ha",
                        "--mesh-topic",
                        "pesetech_mesh",
                        "--device-id",
                        "studio_sky",
                        "--output-json",
                        str(output),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output.exists())
            written = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(written["operation"], "status")
            self.assertEqual(written["next_operation"]["configuration_snippet"], "operation: runtime-check")
            self.assertIn("192.0.2.20", written["strict_proof"]["full_argv"])
            self.assertIn("Pesetech add-on status", stdout.getvalue())
            self.assertIn("configuration_snippet", stdout.getvalue())
            self.assertIn("moves_real_light: false", stdout.getvalue())
            self.assertIn("Strict host proof", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
