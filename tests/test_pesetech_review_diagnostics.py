import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "pesetech_review_diagnostics.py"
)
spec = importlib.util.spec_from_file_location("pesetech_review_diagnostics", SCRIPT_PATH)
reviewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reviewer)


def write_diag_file(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(root, name, payload):
    write_diag_file(root, name, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def write_ready_bundle(root):
    write_json(
        root,
        "manifest.json",
        {
            "created_at": "2026-06-28T03:00:00-0400",
            "mode": "home_assistant_addon",
            "inputs": {
                "status_report": {"exists": True},
                "readiness_report": {"exists": True},
            },
            "options": {
                "ha_url": "http://homeassistant.local:8123",
                "ha_entity_id": "light.skylight",
                "mqtt_source": "supervisor",
                "discovery_prefix": "homeassistant",
                "mesh_topic": "mqtt_mesh",
                "device_id": "skylight",
                "broker": "172.30.33.0",
                "port": 1883,
                "username_present": True,
                "password_present": True,
            },
        },
    )
    write_json(
        root,
        "bluetooth-hardware.json",
        {
            "adapters": [{"name": "hci0"}],
            "bluetooth_meshd_candidates": [{"path": "/usr/lib/bluetooth/bluetooth-meshd", "exists": True, "executable": True}],
        },
    )
    write_json(
        root,
        "pesetech-mesh-daemon-check.json",
        {
            "status": "passed",
            "message": "bluetooth-meshd stayed running for 5s",
            "bluetooth_adapters": ["hci0"],
            "sent_light_commands": False,
            "published_mqtt": False,
            "provisioned": False,
            "imported": False,
        },
    )
    write_diag_file(root, "runtime-check.txt", "$ runtime\nexit=0\nRuntime check passed.\n")
    write_diag_file(root, "preflight.txt", "$ preflight\nexit=0\nConfig preflight passed.\n")
    write_diag_file(root, "discovery-retained.txt", "$ discovery\nexit=0\nDiscovery verification passed.\n")
    write_diag_file(root, "home-assistant-api-check.txt", "$ ha\nexit=0\nHome Assistant API check passed.\n")
    write_diag_file(root, "home-assistant-entity-check.txt", "$ entity\nexit=0\nHome Assistant entity check passed.\n")
    write_json(
        root,
        "pesetech-status.json",
        {
            "operation": "status",
            "read_only": True,
            "sent_light_commands": False,
            "published_mqtt": False,
            "suggested_next_operation": "move-test",
            "next_operation": {
                "operation": "move-test",
                "configuration_snippet": "operation: move-test",
                "moves_real_light": True,
                "no_motion_gate": False,
            },
            "next_action": "Readiness passed without light-control commands.",
        },
    )
    write_json(
        root,
        "pesetech-readiness.json",
        {
            "status": "passed",
            "sent_light_commands": False,
            "ha_url": "http://homeassistant.local:8123",
            "ha_entity_id": "light.skylight",
            "mqtt_source": "supervisor",
        },
    )


def write_verified_proof_outputs(root):
    write_diag_file(root, "proof-verification.txt", "$ verify\nexit=0\nProof verification passed.\n")
    write_diag_file(
        root,
        "ha-service-proof-verification.txt",
        "$ verify-ha\nexit=0\nHome Assistant service proof verification passed.\n",
    )


def write_status_suggestion(root, operation):
    status_report = json.loads((root / "pesetech-status.json").read_text(encoding="utf-8"))
    status_report["suggested_next_operation"] = operation
    write_json(root, "pesetech-status.json", status_report)


def write_import_check_report(root, status="passed"):
    payload = {
        "operation": "import-check",
        "status": status,
        "dry_run": True,
        "sent_light_commands": False,
        "published_mqtt": False,
        "wrote_files": False,
        "requested": {
            "mesh_candidate": 0,
            "node_uuid": "",
            "node_unicast": "",
            "local_address": "",
            "device_id": "skylight",
            "default_entity_id": "light.skylight",
        },
        "selected_node": {
            "uuid": "00112233-4455-6677-8899-aabbccddeeff",
            "unicast": "0002",
            "models": ["1000", "1300", "1303", "1306"],
        },
    }
    if status == "failed":
        payload["error"] = "--mesh-candidate must be between 1 and 1."
    write_json(root, "pesetech-import-check.json", payload)


class PesetechReviewDiagnosticsTest(unittest.TestCase):
    def test_reviews_readiness_bundle_and_points_to_movement_proof(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)

            bundle = reviewer.DiagnosticsBundle.open(root)
            items = reviewer.review(bundle)

        status_by_name = {name: status for status, name, detail in items}
        self.assertEqual(status_by_name["bluetooth"], reviewer.PASS)
        self.assertEqual(status_by_name["runtime"], reviewer.PASS)
        self.assertEqual(status_by_name["mesh_daemon"], reviewer.PASS)
        self.assertEqual(status_by_name["mqtt_discovery"], reviewer.PASS)
        self.assertEqual(status_by_name["ha_entity"], reviewer.PASS)
        self.assertEqual(status_by_name["status"], reviewer.PASS)
        self.assertEqual(status_by_name["readiness"], reviewer.PASS)
        self.assertEqual(status_by_name["mqtt_move_proof"], reviewer.MISSING)
        detail_by_name = {name: detail for status, name, detail in items}
        self.assertIn("snippet=operation: move-test", detail_by_name["status"])
        self.assertIn("moves_real_light=true", detail_by_name["status"])
        self.assertIn("operation=move-test", reviewer.next_action(items))
        self.assertFalse(reviewer.has_failure(items))

    def test_print_review_includes_target_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)

            bundle = reviewer.DiagnosticsBundle.open(root)
            items = reviewer.review(bundle)
            output = io.StringIO()
            with redirect_stdout(output):
                reviewer.print_review(items, bundle)

        text = output.getvalue()
        self.assertIn("Target context:", text)
        self.assertIn("ha_entity_id=light.skylight", text)
        self.assertIn("mqtt_source=supervisor", text)
        self.assertIn("status_suggested_next_operation=move-test", text)
        self.assertIn("status_configuration_snippet=operation: move-test", text)
        self.assertIn("status_moves_real_light=true", text)
        self.assertIn("status_no_motion_gate=false", text)
        self.assertIn("mesh_daemon_status=passed", text)
        self.assertIn("mesh_daemon_message=bluetooth-meshd stayed running for 5s", text)
        self.assertIn("discovery_prefix=homeassistant", text)
        self.assertIn("mesh_topic=mqtt_mesh", text)
        self.assertIn("device_id=skylight", text)
        self.assertIn("discovery_topic=homeassistant/light/mqtt_mesh/skylight/config", text)
        self.assertIn("username_present=true", text)
        self.assertIn("password_present=true", text)

    def test_target_context_falls_back_to_redacted_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            manifest["options"] = {"ha_entity_id": None, "discovery_prefix": None, "mesh_topic": None, "device_id": None}
            write_json(root, "manifest.json", manifest)
            write_json(
                root,
                "config.redacted.json",
                {
                    "mqtt": {"broker": "mqtt.local", "port": 1884, "discovery_prefix": "ha", "node_id": "mesh_bridge"},
                    "mesh": {
                        "kitchen_sky": {
                            "default_entity_id": "light.kitchen_sky",
                        },
                    },
                },
            )

            context = dict(reviewer.target_context(reviewer.DiagnosticsBundle.open(root)))

        self.assertEqual(context["ha_entity_id"], "light.skylight")
        self.assertEqual(context["discovery_prefix"], "ha")
        self.assertEqual(context["mesh_topic"], "mesh_bridge")
        self.assertEqual(context["device_id"], "kitchen_sky")
        self.assertEqual(context["discovery_topic"], "ha/light/mesh_bridge/kitchen_sky/config")
        self.assertEqual(context["broker"], "mqtt.local")
        self.assertEqual(context["port"], "1884")

    def test_reviews_cloud_mesh_candidate_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            manifest["inputs"]["cloud_output"] = {
                "path": "/share/pesetech_mesh.json",
                "exists": True,
                "size_bytes": 2048,
            }
            manifest["options"]["cloud_region"] = "asia"
            manifest["options"]["cloud_candidate"] = "2"
            manifest["options"]["cloud_home_id"] = "home-1"
            manifest["options"]["import_mesh_candidate"] = "2"
            write_json(root, "manifest.json", manifest)
            write_json(
                root,
                "pesetech-cloud-fetch-report.json",
                {
                    "status": "written",
                    "candidate_count": 2,
                    "selected_candidate": 2,
                    "home_count": 1,
                    "homes": [{"home_id": "home-1", "name": "Studio", "source": "home-list"}],
                },
            )
            write_diag_file(
                root,
                "cloud-mesh-candidates.txt",
                "$ extract\nexit=0\nFound 2 Telink MeshStorage candidate(s):\n2. /share/pesetech_mesh.json\n",
            )

            bundle = reviewer.DiagnosticsBundle.open(root)
            items = reviewer.review(bundle)
            context = dict(reviewer.target_context(bundle))

        status_by_name = {name: status for status, name, detail in items}
        detail_by_name = {name: detail for status, name, detail in items}
        self.assertEqual(status_by_name["cloud_fetch"], reviewer.PASS)
        self.assertEqual(status_by_name["cloud_mesh"], reviewer.PASS)
        self.assertIn("homes=home-1 (Studio)", detail_by_name["cloud_fetch"])
        self.assertEqual(context["cloud_region"], "asia")
        self.assertEqual(context["cloud_candidate"], "2")
        self.assertEqual(context["cloud_home_id"], "home-1")
        self.assertEqual(context["import_mesh_candidate"], "2")
        self.assertEqual(context["cloud_fetch_status"], "written")
        self.assertEqual(context["cloud_home_count"], "1")
        self.assertEqual(context["cloud_homes"], "home-1 (Studio)")
        self.assertEqual(context["cloud_output_exists"], "true")

    def test_reviews_passed_import_check_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            manifest["inputs"]["import_check_report"] = {"exists": True}
            write_json(root, "manifest.json", manifest)
            write_import_check_report(root)

            bundle = reviewer.DiagnosticsBundle.open(root)
            items = reviewer.review(bundle)
            context = dict(reviewer.target_context(bundle))

        status_by_name = {name: status for status, name, detail in items}
        detail_by_name = {name: detail for status, name, detail in items}
        self.assertEqual(status_by_name["import_check"], reviewer.PASS)
        self.assertIn("selected=00112233-4455-6677-8899-aabbccddeeff@0002", detail_by_name["import_check"])
        self.assertEqual(context["import_check_status"], "passed")
        self.assertEqual(context["import_check_selected_unicast"], "0002")

    def test_stale_import_check_from_status_is_warned(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)
            write_import_check_report(root)
            status_report = json.loads((root / "pesetech-status.json").read_text(encoding="utf-8"))
            status_report["suggested_next_operation"] = "import-check"
            status_report["reports"] = {"import_check": {"stale": True, "passed": False}}
            write_json(root, "pesetech-status.json", status_report)

            bundle = reviewer.DiagnosticsBundle.open(root)
            items = reviewer.review(bundle)
            output = io.StringIO()
            with redirect_stdout(output):
                reviewer.print_review(items, bundle)

        status_by_name = {name: status for status, name, detail in items}
        self.assertEqual(status_by_name["import_check"], reviewer.WARN)
        self.assertIn("operation=import-check", reviewer.next_action(items))
        self.assertIn("status_stale_evidence=import_check", output.getvalue())

    def test_import_check_context_mismatch_is_warned(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)
            write_import_check_report(root)
            status_report = json.loads((root / "pesetech-status.json").read_text(encoding="utf-8"))
            status_report["reports"] = {"import_check": {"context_mismatches": ["mesh_candidate"], "passed": False}}
            write_json(root, "pesetech-status.json", status_report)

            items = reviewer.review(reviewer.DiagnosticsBundle.open(root))

        status_by_name = {name: status for status, name, detail in items}
        detail_by_name = {name: detail for status, name, detail in items}
        self.assertEqual(status_by_name["import_check"], reviewer.WARN)
        self.assertIn("mesh_candidate", detail_by_name["import_check"])

    def test_import_check_integrity_error_from_status_is_warned(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)
            write_import_check_report(root)
            status_report = json.loads((root / "pesetech-status.json").read_text(encoding="utf-8"))
            status_report["reports"] = {"import_check": {"integrity_errors": ["source"], "passed": False}}
            write_json(root, "pesetech-status.json", status_report)

            items = reviewer.review(reviewer.DiagnosticsBundle.open(root))

        status_by_name = {name: status for status, name, detail in items}
        detail_by_name = {name: detail for status, name, detail in items}
        self.assertEqual(status_by_name["import_check"], reviewer.WARN)
        self.assertIn("source", detail_by_name["import_check"])
        self.assertIn("operation=import-check", reviewer.next_action(items))

    def test_incomplete_import_check_payload_fails_without_hiding_later_checks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)
            write_import_check_report(root)
            payload = json.loads((root / "pesetech-import-check.json").read_text(encoding="utf-8"))
            payload.pop("selected_node")
            write_json(root, "pesetech-import-check.json", payload)

            items = reviewer.review(reviewer.DiagnosticsBundle.open(root))

        status_by_name = {name: status for status, name, detail in items}
        detail_by_name = {name: detail for status, name, detail in items}
        self.assertEqual(status_by_name["import_check"], reviewer.FAIL)
        self.assertIn("selected_node", detail_by_name["import_check"])
        self.assertEqual(status_by_name["preflight"], reviewer.PASS)
        self.assertIn("operation=import-check", reviewer.next_action(items))

    def test_failed_import_check_points_back_to_import_check(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)
            write_import_check_report(root, status="failed")

            items = reviewer.review(reviewer.DiagnosticsBundle.open(root))

        status_by_name = {name: status for status, name, detail in items}
        detail_by_name = {name: detail for status, name, detail in items}
        self.assertEqual(status_by_name["import_check"], reviewer.FAIL)
        self.assertIn("--mesh-candidate", detail_by_name["import_check"])
        self.assertIn("operation=import-check", reviewer.next_action(items))
        self.assertTrue(reviewer.has_failure(items))

    def test_failed_cloud_mesh_summary_points_back_to_cloud_fetch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            manifest["inputs"]["cloud_output"] = {
                "path": "/share/pesetech_mesh.json",
                "exists": True,
                "size_bytes": 27,
            }
            write_json(root, "manifest.json", manifest)
            write_diag_file(root, "cloud-mesh-candidates.txt", "$ extract\nexit=1\nNo Telink MeshStorage candidates found.\n")

            items = reviewer.review(reviewer.DiagnosticsBundle.open(root))

        status_by_name = {name: status for status, name, detail in items}
        self.assertEqual(status_by_name["cloud_mesh"], reviewer.FAIL)
        self.assertIn("Fix cloud-fetch", reviewer.next_action(items))
        self.assertTrue(reviewer.has_failure(items))

    def test_cloud_report_with_homes_points_to_cloud_home_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)
            write_json(
                root,
                "pesetech-cloud-fetch-report.json",
                {
                    "status": "endpoint-fetch-failed",
                    "candidate_count": 0,
                    "home_count": 1,
                    "homes": [{"home_id": "home-1", "name": "Studio", "source": "home-list"}],
                    "error": "mesh endpoint failed",
                },
            )

            items = reviewer.review(reviewer.DiagnosticsBundle.open(root))

        status_by_name = {name: status for status, name, detail in items}
        detail_by_name = {name: detail for status, name, detail in items}
        self.assertEqual(status_by_name["cloud_fetch"], reviewer.WARN)
        self.assertIn("set cloud_home_id", detail_by_name["cloud_fetch"])
        self.assertIn("home-1 (Studio)", detail_by_name["cloud_fetch"])
        self.assertIn("cloud_home_id", reviewer.next_action(items))

    def test_failed_mesh_daemon_report_points_to_daemon_check(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)
            write_json(
                root,
                "pesetech-mesh-daemon-check.json",
                {
                    "status": "failed",
                    "message": "bluetooth-meshd exited during startup",
                    "bluetooth_adapters": ["hci0"],
                    "sent_light_commands": False,
                    "published_mqtt": False,
                    "provisioned": False,
                    "imported": False,
                },
            )

            items = reviewer.review(reviewer.DiagnosticsBundle.open(root))

        status_by_name = {name: status for status, name, detail in items}
        self.assertEqual(status_by_name["mesh_daemon"], reviewer.FAIL)
        self.assertIn("operation=mesh-daemon-check", reviewer.next_action(items))
        self.assertTrue(reviewer.has_failure(items))

    def test_invalid_status_report_points_back_to_status_operation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)
            write_json(
                root,
                "pesetech-status.json",
                {
                    "operation": "status",
                    "read_only": False,
                    "sent_light_commands": False,
                    "published_mqtt": False,
                    "suggested_next_operation": "move-test",
                },
            )

            items = reviewer.review(reviewer.DiagnosticsBundle.open(root))

        status_by_name = {name: status for status, name, detail in items}
        self.assertEqual(status_by_name["status"], reviewer.FAIL)
        self.assertIn("operation=status", reviewer.next_action(items))
        self.assertTrue(reviewer.has_failure(items))

    def test_status_suggestion_guides_early_diagnostics_review(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_json(
                root,
                "manifest.json",
                {
                    "created_at": "2026-06-28T03:00:00-0400",
                    "mode": "home_assistant_addon",
                    "inputs": {"status_report": {"exists": True}},
                    "options": {},
                },
            )
            write_json(
                root,
                "pesetech-status.json",
                {
                    "operation": "status",
                    "read_only": True,
                    "sent_light_commands": False,
                    "published_mqtt": False,
                    "suggested_next_operation": "runtime-check",
                    "next_action": "Run runtime-check first.",
                },
            )

            items = reviewer.review(reviewer.DiagnosticsBundle.open(root))

        status_by_name = {name: status for status, name, detail in items}
        self.assertEqual(status_by_name["status"], reviewer.PASS)
        self.assertIn("operation=runtime-check", reviewer.next_action(items))

    def test_stale_mqtt_proof_from_status_is_warned_and_next_step_uses_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)
            status_report = json.loads((root / "pesetech-status.json").read_text(encoding="utf-8"))
            status_report["suggested_next_operation"] = "move-test"
            status_report["proofs"] = {"mqtt_move": {"stale": True, "passed": False}}
            write_json(root, "pesetech-status.json", status_report)
            write_diag_file(root, "proof-verification.txt", "$ verify\nexit=0\nProof verification passed.\n")

            bundle = reviewer.DiagnosticsBundle.open(root)
            items = reviewer.review(bundle)
            output = io.StringIO()
            with redirect_stdout(output):
                reviewer.print_review(items, bundle)

        status_by_name = {name: status for status, name, detail in items}
        self.assertEqual(status_by_name["mqtt_move_proof"], reviewer.WARN)
        self.assertIn("operation=move-test", reviewer.next_action(items))
        self.assertIn("status_stale_evidence=mqtt_move_proof", output.getvalue())

    def test_stale_readiness_from_status_is_warned_and_next_step_uses_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)
            status_report = json.loads((root / "pesetech-status.json").read_text(encoding="utf-8"))
            status_report["suggested_next_operation"] = "readiness-test"
            status_report["reports"] = {"readiness": {"stale": True, "status": "passed"}}
            write_json(root, "pesetech-status.json", status_report)

            bundle = reviewer.DiagnosticsBundle.open(root)
            items = reviewer.review(bundle)
            output = io.StringIO()
            with redirect_stdout(output):
                reviewer.print_review(items, bundle)

        status_by_name = {name: status for status, name, detail in items}
        self.assertEqual(status_by_name["readiness"], reviewer.WARN)
        self.assertIn("operation=readiness-test", reviewer.next_action(items))
        self.assertIn("status_stale_evidence=readiness_report", output.getvalue())

    def test_stale_ha_service_proof_from_status_is_warned(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)
            status_report = json.loads((root / "pesetech-status.json").read_text(encoding="utf-8"))
            status_report["suggested_next_operation"] = "ha-service-test"
            status_report["proofs"] = {
                "mqtt_move": {"stale": False, "passed": True},
                "ha_service": {"stale": True, "passed": False},
            }
            write_json(root, "pesetech-status.json", status_report)
            write_diag_file(root, "proof-verification.txt", "$ verify\nexit=0\nProof verification passed.\n")
            write_diag_file(
                root,
                "ha-service-proof-verification.txt",
                "$ verify-ha\nexit=0\nHome Assistant service proof verification passed.\n",
            )

            items = reviewer.review(reviewer.DiagnosticsBundle.open(root))

        status_by_name = {name: status for status, name, detail in items}
        self.assertEqual(status_by_name["mqtt_move_proof"], reviewer.PASS)
        self.assertEqual(status_by_name["ha_service_proof"], reviewer.WARN)
        self.assertIn("operation=ha-service-test", reviewer.next_action(items))

    def test_stale_final_audit_from_status_is_warned(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)
            status_report = json.loads((root / "pesetech-status.json").read_text(encoding="utf-8"))
            status_report["suggested_next_operation"] = "proof-test"
            status_report["reports"] = {"final_audit": {"stale": True, "passed": False}}
            write_json(root, "pesetech-status.json", status_report)
            write_json(
                root,
                "pesetech-final-audit.json",
                {"passed": True, "strict_visual_proof": True, "proof_run_id": "old-host-1"},
            )

            items = reviewer.review(reviewer.DiagnosticsBundle.open(root))

        status_by_name = {name: status for status, name, detail in items}
        self.assertEqual(status_by_name["final_audit"], reviewer.WARN)
        self.assertIn("operation=proof-test", reviewer.next_action(items))

    def test_strict_final_audit_reports_objective_proven_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)
            write_verified_proof_outputs(root)
            write_status_suggestion(root, "service")
            write_json(
                root,
                "pesetech-final-audit.json",
                {"passed": True, "objective_proven": True, "strict_visual_proof": True, "proof_run_id": "host-1"},
            )

            bundle = reviewer.DiagnosticsBundle.open(root)
            items = reviewer.review(bundle)
            context = dict(reviewer.target_context(bundle))

        detail_by_name = {name: detail for status, name, detail in items}
        status_by_name = {name: status for status, name, detail in items}
        self.assertEqual(status_by_name["final_audit"], reviewer.PASS)
        self.assertEqual(context["final_audit_objective_proven"], "true")
        self.assertIn("objective_proven=true", detail_by_name["final_audit"])
        self.assertIn("objective_proven=true", reviewer.next_action(items))

    def test_strict_final_audit_without_objective_proven_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)
            write_verified_proof_outputs(root)
            write_status_suggestion(root, "service")
            write_json(
                root,
                "pesetech-final-audit.json",
                {"passed": True, "strict_visual_proof": True, "proof_run_id": "host-1"},
            )

            items = reviewer.review(reviewer.DiagnosticsBundle.open(root))

        status_by_name = {name: status for status, name, detail in items}
        detail_by_name = {name: detail for status, name, detail in items}
        self.assertEqual(status_by_name["final_audit"], reviewer.WARN)
        self.assertIn("objective_proven is false or missing", detail_by_name["final_audit"])
        self.assertIn("host prove-ha-addon", reviewer.next_action(items))

    def test_detects_setup_failures_before_movement(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)
            write_json(root, "bluetooth-hardware.json", {"adapters": [], "bluetooth_meshd_candidates": []})
            write_diag_file(root, "preflight.txt", "$ preflight\nexit=1\nmqtt_broker must be set\n")

            items = reviewer.review(reviewer.DiagnosticsBundle.open(root))

        status_by_name = {name: status for status, name, detail in items}
        self.assertEqual(status_by_name["bluetooth"], reviewer.FAIL)
        self.assertEqual(status_by_name["preflight"], reviewer.FAIL)
        self.assertIn("Fix the first failing setup gate", reviewer.next_action(items))
        self.assertTrue(reviewer.has_failure(items))

    def test_can_review_tarball_without_extracting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            root = temp_path / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)
            archive = temp_path / "pesetech-diagnostics-20260628.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(root, arcname=root.name)

            bundle = reviewer.DiagnosticsBundle.open(archive)
            items = reviewer.review(bundle)

        self.assertEqual({name: status for status, name, detail in items}["readiness"], reviewer.PASS)

    def test_cli_returns_nonzero_for_failures(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pesetech-diagnostics-20260628"
            root.mkdir()
            write_ready_bundle(root)
            write_diag_file(root, "preflight.txt", "$ preflight\nexit=1\nfailed\n")

            output = io.StringIO()
            old_argv = reviewer.sys.argv
            try:
                reviewer.sys.argv = ["pesetech_review_diagnostics.py", str(root)]
                with redirect_stdout(output):
                    exit_code = reviewer.main()
            finally:
                reviewer.sys.argv = old_argv

        self.assertEqual(exit_code, 1)
        self.assertIn("FAIL", output.getvalue())


if __name__ == "__main__":
    unittest.main()
