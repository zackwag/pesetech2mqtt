import importlib.util
import json
import os
import tempfile
import types
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "pesetech_diagnostics.py"
)
spec = importlib.util.spec_from_file_location("pesetech_diagnostics", SCRIPT_PATH)
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


class PesetechDiagnosticsTest(unittest.TestCase):
    def sample_mesh(self):
        return {
            "ivIndex": "0000002A",
            "sequenceNumber": 17,
            "netKeys": [{"index": 0, "key": "00112233445566778899AABBCCDDEEFF"}],
            "appKeys": [{"index": 0, "boundNetKey": 0, "key": "112233445566778899AABBCCDDEEFF00"}],
            "provisioners": [{"UUID": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}],
            "nodes": [
                {
                    "UUID": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    "unicastAddress": "0001",
                    "deviceKey": "00000000000000000000000000000000",
                    "elements": [{"index": 0, "models": []}],
                },
                {
                    "UUID": "00112233-4455-6677-8899-aabbccddeeff",
                    "name": "Skylight",
                    "unicastAddress": "0002",
                    "deviceKey": "FFEEDDCCBBAA99887766554433221100",
                    "elements": [{"index": 0, "models": [{"modelId": "1306"}]}],
                },
            ],
        }

    def test_redact_nested_secrets(self):
        data = {
            "mqtt": {"username": "ha", "password": "secret"},
            "keychain": {"network_key": "abc", "app_key": "def"},
            "mesh": {"skylight": {"uuid": "00112233-4455-6677-8899-aabbccddeeff"}},
        }

        redacted = diagnostics.redact(data)

        self.assertEqual(redacted["mqtt"]["username"], "<redacted>")
        self.assertEqual(redacted["mqtt"]["password"], "<redacted>")
        self.assertEqual(redacted["keychain"], "<redacted>")
        self.assertEqual(redacted["mesh"]["skylight"]["uuid"], "00112233-4455-6677-8899-aabbccddeeff")

    def test_line_redaction_without_pyyaml(self):
        text = """
mqtt:
  username: ha
  password: secret
keychain:
  network_key: abc
  app_key: def
mesh:
  skylight:
    uuid: 00112233-4455-6677-8899-aabbccddeeff
"""

        redacted = diagnostics.redact_yaml_lines(text)

        self.assertIn("username: <redacted>", redacted)
        self.assertIn("password: <redacted>", redacted)
        self.assertIn("network_key: <redacted>", redacted)
        self.assertIn("app_key: <redacted>", redacted)
        self.assertIn("uuid: 00112233-4455-6677-8899-aabbccddeeff", redacted)
        self.assertNotIn("secret", redacted)

    def test_bluetooth_hardware_snapshot_lists_visible_adapters_and_meshd(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            bluetooth_dir = temp_path / "bluetooth"
            adapter = bluetooth_dir / "hci2"
            adapter.mkdir(parents=True)
            (adapter / "address").write_text("00:11:22:33:44:55\n", encoding="utf-8")
            (adapter / "type").write_text("BR/EDR\n", encoding="utf-8")
            meshd = temp_path / "bluetooth-meshd"
            meshd.write_text("#!/bin/sh\n", encoding="utf-8")
            os.chmod(meshd, 0o755)

            snapshot = diagnostics.bluetooth_hardware_snapshot(
                types.SimpleNamespace(
                    bluetooth_sys_class=str(bluetooth_dir),
                    bluetooth_meshd_candidates=[str(meshd)],
                )
            )

        self.assertEqual(snapshot["sys_class"], str(bluetooth_dir))
        self.assertEqual(snapshot["adapters"][0]["name"], "hci2")
        self.assertEqual(snapshot["adapters"][0]["address"], "00:11:22:33:44:55")
        self.assertEqual(snapshot["adapters"][0]["type"], "BR/EDR")
        self.assertTrue(snapshot["bluetooth_meshd_candidates"][0]["exists"])
        self.assertTrue(snapshot["bluetooth_meshd_candidates"][0]["executable"])

    def test_collect_writes_preflight_and_missing_proof_marker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config = temp_path / "config.yaml"
            store = temp_path / "store.yaml"
            final_audit_report = temp_path / "pesetech-final-audit.json"
            preflight_report = temp_path / "pesetech-preflight.json"
            import_check_report = temp_path / "pesetech-import-check.json"
            readiness_report = temp_path / "pesetech-readiness.json"
            status_report = temp_path / "pesetech-status.json"
            runtime_report = temp_path / "pesetech-runtime-check.json"
            mesh_daemon_report = temp_path / "pesetech-mesh-daemon-check.json"
            compose_dir = temp_path / "docker"
            output_dir = temp_path / "out"
            compose_dir.mkdir()
            config.write_text(
                """
mqtt:
  broker: homeassistant.local
mesh:
  skylight:
    uuid: 00112233-4455-6677-8899-aabbccddeeff
    type: pesetech_skylight
""",
                encoding="utf-8",
            )
            store.write_text("nodes: {}\n", encoding="utf-8")
            final_audit_report.write_text('{"passed": true}\n', encoding="utf-8")
            preflight_report.write_text(
                '{"operation": "preflight", "status": "passed", "sent_light_commands": false}\n',
                encoding="utf-8",
            )
            import_check_report.write_text(
                json.dumps(
                    {
                        "operation": "import-check",
                        "status": "passed",
                        "dry_run": True,
                        "sent_light_commands": False,
                        "published_mqtt": False,
                        "requested": {"mesh_candidate": 0, "device_id": "skylight"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            readiness_report.write_text('{"status": "passed", "sent_light_commands": false}\n', encoding="utf-8")
            status_report.write_text(
                '{"operation": "status", "read_only": true, "suggested_next_operation": "move-test"}\n',
                encoding="utf-8",
            )
            runtime_report.write_text(
                '{"operation": "runtime-check", "status": "passed", "exit_code": 0}\n',
                encoding="utf-8",
            )
            mesh_daemon_report.write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "bluetooth_adapters": ["hci0"],
                        "sent_light_commands": False,
                        "published_mqtt": False,
                        "provisioned": False,
                        "imported": False,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            args = types.SimpleNamespace(
                config=str(config),
                store=str(store),
                proof_log=str(temp_path / "missing-proof.jsonl"),
                final_audit_report=str(final_audit_report),
                preflight_report=str(preflight_report),
                import_check_report=str(import_check_report),
                readiness_report=str(readiness_report),
                status_report=str(status_report),
                runtime_report=str(runtime_report),
                mesh_daemon_report=str(mesh_daemon_report),
                discovery_prefix="homeassistant",
                mesh_topic="mqtt_mesh",
                device_id="skylight",
                compose_dir=str(compose_dir),
                output_dir=str(output_dir),
                log_lines=1,
                skip_docker=False,
            )

            archive = diagnostics.collect(args)
            diagnostic_dir = archive.with_suffix("").with_suffix("")

            self.assertTrue((diagnostic_dir / "preflight.txt").exists())
            self.assertTrue((diagnostic_dir / "bluetooth-hardware.json").exists())
            hardware = json.loads((diagnostic_dir / "bluetooth-hardware.json").read_text(encoding="utf-8"))
            self.assertIn("adapters", hardware)
            self.assertIn("bluetooth_meshd_candidates", hardware)
            self.assertIn("Pesetech/Home Assistant preflight", (diagnostic_dir / "preflight.txt").read_text())
            self.assertTrue((diagnostic_dir / "runtime-check.txt").exists())
            self.assertIn("pesetech_runtime_check.py", (diagnostic_dir / "runtime-check.txt").read_text())
            self.assertIn(
                "homeassistant/light/mqtt_mesh/skylight/config",
                (diagnostic_dir / "discovery-dry-run.txt").read_text(),
            )
            self.assertIn("Proof log not found", (diagnostic_dir / "proof-log.txt").read_text())
            self.assertEqual(
                json.loads((diagnostic_dir / "pesetech-final-audit.json").read_text(encoding="utf-8")),
                {"passed": True},
            )
            self.assertEqual(
                json.loads((diagnostic_dir / "pesetech-preflight.json").read_text(encoding="utf-8")),
                {"operation": "preflight", "status": "passed", "sent_light_commands": False},
            )
            copied_import_check = json.loads((diagnostic_dir / "pesetech-import-check.json").read_text(encoding="utf-8"))
            self.assertEqual(copied_import_check["operation"], "import-check")
            self.assertEqual(copied_import_check["status"], "passed")
            self.assertEqual(
                json.loads((diagnostic_dir / "pesetech-readiness.json").read_text(encoding="utf-8")),
                {"status": "passed", "sent_light_commands": False},
            )
            copied_status = json.loads((diagnostic_dir / "pesetech-status.json").read_text(encoding="utf-8"))
            self.assertTrue(copied_status["read_only"])
            self.assertEqual(copied_status["suggested_next_operation"], "move-test")
            copied_runtime = json.loads((diagnostic_dir / "pesetech-runtime-check.json").read_text(encoding="utf-8"))
            self.assertEqual(copied_runtime["status"], "passed")
            self.assertEqual(copied_runtime["exit_code"], 0)
            copied_mesh_daemon = json.loads((diagnostic_dir / "pesetech-mesh-daemon-check.json").read_text(encoding="utf-8"))
            self.assertEqual(copied_mesh_daemon["status"], "passed")
            self.assertEqual(copied_mesh_daemon["bluetooth_adapters"], ["hci0"])
            manifest = json.loads((diagnostic_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["mode"], "docker_or_host")
            self.assertEqual(manifest["options"]["discovery_prefix"], "homeassistant")
            self.assertEqual(manifest["options"]["mesh_topic"], "mqtt_mesh")
            self.assertEqual(manifest["options"]["device_id"], "skylight")
            self.assertEqual(manifest["inputs"]["config"]["path"], str(config.resolve()))
            self.assertTrue(manifest["inputs"]["config"]["exists"])
            self.assertFalse(manifest["inputs"]["proof_log"]["exists"])
            self.assertTrue(manifest["inputs"]["final_audit_report"]["exists"])
            self.assertTrue(manifest["inputs"]["preflight_report"]["exists"])
            self.assertTrue(manifest["inputs"]["import_check_report"]["exists"])
            self.assertTrue(manifest["inputs"]["readiness_report"]["exists"])
            self.assertTrue(manifest["inputs"]["status_report"]["exists"])
            self.assertTrue(manifest["inputs"]["runtime_report"]["exists"])
            self.assertTrue(manifest["inputs"]["mesh_daemon_report"]["exists"])
            self.assertIn(
                "preflight.txt",
                {entry["path"] for entry in manifest["collected_files"]},
            )
            self.assertIn(
                "bluetooth-hardware.json",
                {entry["path"] for entry in manifest["collected_files"]},
            )
            self.assertIn(
                "manifest.json",
                {entry["path"] for entry in manifest["collected_files"]},
            )
            self.assertTrue(archive.exists())

    def test_collect_summarizes_cloud_mesh_without_copying_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config = temp_path / "config.yaml"
            store = temp_path / "store.yaml"
            cloud_output = temp_path / "pesetech_mesh.json"
            raw_output = temp_path / "raw-cloud.json"
            cloud_report = temp_path / "cloud-report.json"
            token_file = temp_path / "token.txt"
            username_file = temp_path / "username.txt"
            password_file = temp_path / "password.txt"
            output_dir = temp_path / "out"
            config.write_text(
                """
mqtt:
  broker: homeassistant.local
mesh:
  skylight:
    uuid: 00112233-4455-6677-8899-aabbccddeeff
    type: pesetech_skylight
""",
                encoding="utf-8",
            )
            store.write_text("nodes: {}\n", encoding="utf-8")
            cloud_output.write_text(json.dumps(self.sample_mesh()) + "\n", encoding="utf-8")
            raw_output.write_text('{"secret": "contains mesh keys"}\n', encoding="utf-8")
            cloud_report.write_text(
                json.dumps(
                    {
                        "status": "written",
                        "region": "asia",
                        "candidate_count": 1,
                        "selected_candidate": 1,
                        "candidates": [{"summary": ["1. cloud:home-list", "   likely light nodes:", "     - Skylight"]}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            token_file.write_text("cloud-token\n", encoding="utf-8")
            username_file.write_text("person@example.com\n", encoding="utf-8")
            password_file.write_text("cloud-password\n", encoding="utf-8")
            args = types.SimpleNamespace(
                config=str(config),
                store=str(store),
                proof_log=str(temp_path / "missing-proof.jsonl"),
                cloud_output=str(cloud_output),
                cloud_raw_output=str(raw_output),
                cloud_report=str(cloud_report),
                cloud_token_file=str(token_file),
                cloud_username_file=str(username_file),
                cloud_password_file=str(password_file),
                cloud_region="asia",
                cloud_candidate="1",
                cloud_home_id="home-1",
                import_mesh_candidate="2",
                discovery_prefix=None,
                mesh_topic=None,
                device_id=None,
                compose_dir=str(temp_path / "docker"),
                output_dir=str(output_dir),
                log_lines=1,
                skip_docker=True,
            )

            archive = diagnostics.collect(args)
            diagnostic_dir = archive.with_suffix("").with_suffix("")

            cloud_summary = (diagnostic_dir / "cloud-mesh-candidates.txt").read_text(encoding="utf-8")
            self.assertIn("Found 1 Telink MeshStorage candidate", cloud_summary)
            self.assertIn("Skylight", cloud_summary)
            self.assertNotIn("00112233445566778899AABBCCDDEEFF", cloud_summary)
            self.assertNotIn("FFEEDDCCBBAA99887766554433221100", cloud_summary)
            self.assertFalse((diagnostic_dir / "pesetech_mesh.json").exists())
            self.assertFalse((diagnostic_dir / "raw-cloud.json").exists())
            copied_report = json.loads((diagnostic_dir / "pesetech-cloud-fetch-report.json").read_text(encoding="utf-8"))
            self.assertEqual(copied_report["status"], "written")
            self.assertEqual(copied_report["selected_candidate"], 1)
            all_diagnostics_text = "\n".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in diagnostic_dir.rglob("*")
                if path.is_file()
            )
            self.assertNotIn("cloud-token", all_diagnostics_text)
            self.assertNotIn("cloud-password", all_diagnostics_text)
            self.assertNotIn("112233445566778899AABBCCDDEEFF00", all_diagnostics_text)
            manifest = json.loads((diagnostic_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["inputs"]["cloud_output"]["exists"])
            self.assertTrue(manifest["inputs"]["cloud_raw_output"]["exists"])
            self.assertTrue(manifest["inputs"]["cloud_report"]["exists"])
            self.assertTrue(manifest["inputs"]["cloud_token_file"]["exists"])
            self.assertTrue(manifest["inputs"]["cloud_username_file"]["exists"])
            self.assertTrue(manifest["inputs"]["cloud_password_file"]["exists"])
            self.assertEqual(manifest["options"]["cloud_region"], "asia")
            self.assertEqual(manifest["options"]["cloud_candidate"], "1")
            self.assertEqual(manifest["options"]["cloud_home_id"], "home-1")
            self.assertEqual(manifest["options"]["import_mesh_candidate"], "2")

    def test_collect_includes_home_assistant_service_proof_verification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config = temp_path / "config.yaml"
            store = temp_path / "store.yaml"
            ha_proof = temp_path / "ha-proof.jsonl"
            output_dir = temp_path / "out"
            config.write_text(
                """
mqtt:
  broker: homeassistant.local
mesh:
  skylight:
    uuid: 00112233-4455-6677-8899-aabbccddeeff
    type: pesetech_skylight
""",
                encoding="utf-8",
            )
            store.write_text("nodes: {}\n", encoding="utf-8")
            for event in [
                ("on", "light.turn_on", {"entity_id": "light.skylight"}, {"state": "on"}, {"state": "on"}),
                (
                    "brightness",
                    "light.turn_on",
                    {"entity_id": "light.skylight", "brightness": 192},
                    {"state": "on", "attributes": {"brightness": 192}},
                    {"state": "on", "attributes": {"brightness": 192}},
                ),
                (
                    "warm",
                    "light.turn_on",
                    {"entity_id": "light.skylight", "color_temp_kelvin": 2200},
                    {"state": "on", "attributes": {"color_temp_kelvin": 2200}},
                    {"state": "on", "attributes": {"color_temp_kelvin": 2200}},
                ),
                (
                    "cool",
                    "light.turn_on",
                    {"entity_id": "light.skylight", "color_temp_kelvin": 6500},
                    {"state": "on", "attributes": {"color_temp_kelvin": 6500}},
                    {"state": "on", "attributes": {"color_temp_kelvin": 6500}},
                ),
                ("off", "light.turn_off", {"entity_id": "light.skylight"}, {"state": "off"}, {"state": "off"}),
            ]:
                step, service, payload, expected_state, matched_state = event
                expected_mqtt_state = {"state": "OFF" if service == "light.turn_off" else "ON"}
                required_mqtt_fields = []
                expected_mqtt_attributes = {}
                matched_mqtt_state = dict(expected_mqtt_state)
                if step == "brightness":
                    required_mqtt_fields = ["brightness"]
                    expected_mqtt_attributes = {"brightness": 49152}
                    matched_mqtt_state["brightness"] = 49152
                elif step in {"warm", "cool"}:
                    required_mqtt_fields = ["color_temp"]
                    expected_mqtt_attributes = {"color_temp": 455 if step == "warm" else 154}
                    matched_mqtt_state["color_temp"] = expected_mqtt_attributes["color_temp"]
                with ha_proof.open("a", encoding="utf-8") as proof_file:
                    proof_file.write(
                        json.dumps(
                            {
                                "run_id": "ha-proof-1",
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
                                "expected_mqtt_state": expected_mqtt_state,
                                "expected_mqtt_attributes": expected_mqtt_attributes,
                                "required_mqtt_fields": required_mqtt_fields,
                                "matched_mqtt_state": matched_mqtt_state,
                                "observed": True,
                                "state_elapsed_ms": 12,
                            }
                        )
                        + "\n"
                    )
            args = types.SimpleNamespace(
                config=str(config),
                store=str(store),
                proof_log=str(temp_path / "missing-proof.jsonl"),
                ha_proof_log=str(ha_proof),
                ha_require_attributes=True,
                ha_require_mqtt_state=True,
                ha_require_mqtt_attributes=True,
                ha_mqtt_brightness_scale=65280,
                ha_mqtt_brightness_tolerance=2,
                ha_mqtt_mired_tolerance=2,
                proof_run_id="ha-proof-1",
                discovery_prefix=None,
                mesh_topic=None,
                device_id=None,
                compose_dir=str(temp_path / "docker"),
                output_dir=str(output_dir),
                log_lines=1,
                skip_docker=True,
            )

            archive = diagnostics.collect(args)
            diagnostic_dir = archive.with_suffix("").with_suffix("")

            self.assertTrue((diagnostic_dir / "pesetech-ha-service-proof.jsonl").exists())
            self.assertIn(
                "Home Assistant service proof verification passed",
                (diagnostic_dir / "ha-service-proof-verification.txt").read_text(),
            )
            verification = (diagnostic_dir / "ha-service-proof-verification.txt").read_text()
            self.assertIn("--require-attributes", verification)
            self.assertIn("--require-mqtt-state", verification)
            self.assertIn("--require-mqtt-attributes", verification)
            self.assertIn("--mqtt-brightness-scale 65280", verification)
            self.assertIn("--mqtt-brightness-tolerance 2", verification)
            self.assertIn("--mqtt-mired-tolerance 2", verification)
            self.assertIn("--run-id ha-proof-1", verification)

    def test_collect_can_capture_live_retained_discovery_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config = temp_path / "config.yaml"
            store = temp_path / "store.yaml"
            output_dir = temp_path / "out"
            config.write_text(
                """
mqtt:
  broker: homeassistant.local
mesh:
  skylight:
    uuid: 00112233-4455-6677-8899-aabbccddeeff
    type: pesetech_skylight
""",
                encoding="utf-8",
            )
            store.write_text("nodes: {}\n", encoding="utf-8")
            args = types.SimpleNamespace(
                config=str(config),
                store=str(store),
                proof_log=str(temp_path / "missing-proof.jsonl"),
                discovery_prefix="homeassistant",
                mesh_topic="mqtt_mesh",
                device_id="skylight",
                live_discovery=True,
                broker="mqtt.local",
                port=1884,
                mqtt_source="manual",
                username="mqtt-user",
                password="mqtt-secret",
                mqtt_timeout=1.0,
                discovery_timeout=2.0,
                candidate_timeout=3.0,
                compose_dir=str(temp_path / "docker"),
                output_dir=str(output_dir),
                log_lines=1,
                skip_docker=True,
            )
            original_run_command = diagnostics.run_command
            try:
                diagnostics.run_command = lambda command, cwd=None: "$ " + " ".join(diagnostics.redact_command(command)) + "\n"
                archive = diagnostics.collect(args)
            finally:
                diagnostics.run_command = original_run_command
            diagnostic_dir = archive.with_suffix("").with_suffix("")

            retained = (diagnostic_dir / "discovery-retained.txt").read_text(encoding="utf-8")
            self.assertIn("pesetech_mqtt_discovery.py", retained)
            self.assertIn("--require-retained", retained)
            self.assertIn("--dump-json", retained)
            self.assertIn("--broker mqtt.local", retained)
            self.assertIn("--port 1884", retained)
            self.assertIn("--username <redacted>", retained)
            self.assertIn("--password <redacted>", retained)
            self.assertNotIn("mqtt-user", retained)
            self.assertNotIn("mqtt-secret", retained)
            self.assertIn("--mqtt-timeout 1.0", retained)
            self.assertIn("--discovery-timeout 2.0", retained)
            self.assertIn("--candidate-timeout 3.0", retained)
            manifest = json.loads((diagnostic_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["options"]["broker"], "mqtt.local")
            self.assertEqual(manifest["options"]["port"], 1884)
            self.assertEqual(manifest["options"]["mqtt_source"], "manual")
            self.assertTrue(manifest["options"]["username_present"])
            self.assertTrue(manifest["options"]["password_present"])
            self.assertEqual(manifest["options"]["candidate_timeout"], 3.0)

    def test_collect_can_capture_home_assistant_api_and_entity_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config = temp_path / "config.yaml"
            store = temp_path / "store.yaml"
            token_file = temp_path / "ha-token.txt"
            output_dir = temp_path / "out"
            config.write_text(
                """
mqtt:
  broker: homeassistant.local
mesh:
  skylight:
    uuid: 00112233-4455-6677-8899-aabbccddeeff
    type: pesetech_skylight
""",
                encoding="utf-8",
            )
            store.write_text("nodes: {}\n", encoding="utf-8")
            token_file.write_text("secret-token\n", encoding="utf-8")
            args = types.SimpleNamespace(
                config=str(config),
                store=str(store),
                proof_log=str(temp_path / "missing-proof.jsonl"),
                ha_url="http://ha.local:8123",
                ha_entity_id="light.pesetech_sky",
                ha_token_file=str(token_file),
                ha_api_context=True,
                ha_candidate_search="pesetech",
                discovery_prefix=None,
                mesh_topic=None,
                device_id=None,
                compose_dir=str(temp_path / "docker"),
                output_dir=str(output_dir),
                log_lines=1,
                skip_docker=True,
            )
            original_run_command = diagnostics.run_command
            try:
                diagnostics.run_command = lambda command, cwd=None: "$ " + " ".join(command) + "\n"
                archive = diagnostics.collect(args)
            finally:
                diagnostics.run_command = original_run_command
            diagnostic_dir = archive.with_suffix("").with_suffix("")

            api_check = (diagnostic_dir / "home-assistant-api-check.txt").read_text(encoding="utf-8")
            candidates = (diagnostic_dir / "home-assistant-light-candidates.txt").read_text(encoding="utf-8")
            self.assertIn("pesetech_ha_service_smoke.py", api_check)
            self.assertIn("--url http://ha.local:8123", api_check)
            self.assertIn("--check-api", api_check)
            self.assertIn(f"--token-file {token_file}", api_check)
            entity_check = (diagnostic_dir / "home-assistant-entity-check.txt").read_text(encoding="utf-8")
            self.assertIn("--entity-id light.pesetech_sky", entity_check)
            self.assertIn("--check-entity", entity_check)
            self.assertIn("--candidate-search pesetech", entity_check)
            self.assertIn(f"--token-file {token_file}", entity_check)
            self.assertIn("--entity-id light.pesetech_sky", candidates)
            self.assertIn("--list-candidates", candidates)
            self.assertIn("--candidate-search pesetech", candidates)

    def test_collect_can_skip_docker_for_home_assistant_addon(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config = temp_path / "config.yaml"
            store = temp_path / "store.yaml"
            output_dir = temp_path / "out"
            config.write_text(
                """
mqtt:
  broker: homeassistant.local
mesh:
  skylight:
    uuid: 00112233-4455-6677-8899-aabbccddeeff
    type: pesetech_skylight
""",
                encoding="utf-8",
            )
            store.write_text("nodes: {}\n", encoding="utf-8")
            args = types.SimpleNamespace(
                config=str(config),
                store=str(store),
                proof_log=str(temp_path / "missing-proof.jsonl"),
                discovery_prefix=None,
                mesh_topic=None,
                device_id=None,
                compose_dir=str(temp_path / "docker"),
                output_dir=str(output_dir),
                log_lines=1,
                skip_docker=True,
            )

            archive = diagnostics.collect(args)
            diagnostic_dir = archive.with_suffix("").with_suffix("")

            self.assertIn("--skip-docker", (diagnostic_dir / "preflight.txt").read_text())
            self.assertIn("pesetech_runtime_check.py", (diagnostic_dir / "runtime-check.txt").read_text())
            manifest = json.loads((diagnostic_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["mode"], "home_assistant_addon")
            self.assertTrue(manifest["options"]["skip_docker"])
            self.assertEqual((diagnostic_dir / "docker.txt").read_text(), "Docker diagnostics skipped (--skip-docker).\n")


if __name__ == "__main__":
    unittest.main()
