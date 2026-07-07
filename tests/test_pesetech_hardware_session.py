import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "pesetech_hardware_session.py"
)
spec = importlib.util.spec_from_file_location("pesetech_hardware_session", SCRIPT_PATH)
session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session)


class PesetechHardwareSessionTest(unittest.TestCase):
    def test_replace_device_uuid_updates_skylight_only(self):
        text = """mqtt:
  broker: homeassistant.local
mesh:
  other:
    uuid: 11111111-1111-1111-1111-111111111111
    type: pesetech_skylight
  skylight:
    uuid: <uuid_from_gateway_scan>
    type: pesetech_skylight
"""

        updated = session.replace_device_uuid(
            text,
            "skylight",
            "00112233-4455-6677-8899-aabbccddeeff",
        )

        self.assertIn("uuid: 11111111-1111-1111-1111-111111111111", updated)
        self.assertIn("uuid: 00112233-4455-6677-8899-aabbccddeeff", updated)
        self.assertNotIn("<uuid_from_gateway_scan>", updated)

    def test_replace_device_uuid_errors_when_device_missing(self):
        with self.assertRaises(ValueError):
            session.replace_device_uuid("mesh:\n  other:\n    uuid: abc\n", "skylight", "00112233-4455-6677-8899-aabbccddeeff")

    def test_set_config_uuid_validates_uuid_and_writes_config(self):
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as config_file, tempfile.TemporaryDirectory() as other_dir:
            config_path = Path(config_file.name)
            config_file.write("mesh:\n  skylight:\n    uuid: <uuid_from_gateway_scan>\n")
            config_file.flush()
            old_cwd = os.getcwd()
            os.chdir(other_dir)

            try:
                uuid = session.set_config_uuid(config_path, "skylight", "00112233-4455-6677-8899-aabbccddeeff")
            finally:
                os.chdir(old_cwd)

            self.assertEqual(uuid, "00112233-4455-6677-8899-aabbccddeeff")
            self.assertIn("uuid: 00112233-4455-6677-8899-aabbccddeeff", config_path.read_text())

    def test_relative_paths_resolve_against_repo_root(self):
        self.assertEqual(
            session.resolve_repo_path("docker/config/config.yaml"),
            session.repo_root() / "docker/config/config.yaml",
        )
        self.assertEqual(session.resolve_repo_path("/tmp/config.yaml"), Path("/tmp/config.yaml"))

    def test_addon_cli_action_shell_supports_apps_and_addons_namespaces(self):
        start_command = session.addon_cli_action_shell("start", "pesetech_ble_mesh")
        restart_command = session.addon_cli_action_shell("restart", "pesetech_ble_mesh")
        namespace_command = session.addon_host_cli_namespace_shell()

        self.assertIn("ha apps --help", start_command)
        self.assertIn("ha apps start pesetech_ble_mesh", start_command)
        self.assertIn("ha addons start pesetech_ble_mesh", start_command)
        self.assertIn("ha apps restart pesetech_ble_mesh", restart_command)
        self.assertIn("ha addons restart pesetech_ble_mesh", restart_command)
        self.assertIn("ha apps stop pesetech_ble_mesh", restart_command)
        self.assertIn("ha addons stop pesetech_ble_mesh", restart_command)
        self.assertIn("ha apps --help", namespace_command)
        self.assertIn("ha addons --help", namespace_command)

    def test_addon_ssh_builders_include_timeout_and_batch_mode(self):
        args = types.SimpleNamespace(
            ha_host="ha.local",
            ssh_user="root",
            ssh_connect_timeout=5,
            ssh_batch_mode=True,
        )

        self.assertEqual(
            session.addon_ssh_command(args, "true"),
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", "root@ha.local", "true"],
        )
        self.assertEqual(
            session.addon_scp_command(args, "local.txt", "root@ha.local:/share/local.txt"),
            ["scp", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", "local.txt", "root@ha.local:/share/local.txt"],
        )

    def test_addon_ssh_builders_can_omit_options_for_existing_callers(self):
        args = types.SimpleNamespace(ha_host="ha.local", ssh_user="")

        self.assertEqual(session.addon_ssh_command(args, "true"), ["ssh", "ha.local", "true"])
        self.assertEqual(session.addon_scp_command(args, "local.txt", "ha.local:/share/local.txt"), ["scp", "local.txt", "ha.local:/share/local.txt"])

    def test_command_echo_redacts_sensitive_values(self):
        command = session.env_command(
            [
                "python3",
                "tool.py",
                "--username",
                "mqtt-user",
                "--password",
                "mqtt-secret",
                "--mqtt-password=mqtt-secret-too",
                "--password-file",
                "/tmp/password.txt",
            ],
            {"MQTT_PASSWORD": "env-secret", "GATEWAY_MODE": "service"},
        )

        self.assertIn("GATEWAY_MODE=service", command)
        self.assertIn("MQTT_PASSWORD=", command)
        self.assertIn("--username '<redacted>'", command)
        self.assertIn("--password '<redacted>'", command)
        self.assertIn("--mqtt-password=<redacted>", command)
        self.assertIn("--password-file /tmp/password.txt", command)
        self.assertNotIn("mqtt-user", command)
        self.assertNotIn("mqtt-secret", command)
        self.assertNotIn("mqtt-secret-too", command)
        self.assertNotIn("env-secret", command)

    def test_shell_command_dry_run_prints_gateway_mode(self):
        args = types.SimpleNamespace(compose_dir="docker", dry_run=True)
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.shell(args)

        self.assertEqual(exit_code, 0)
        self.assertIn("GATEWAY_MODE=shell docker compose up -d --force-recreate", output.getvalue())
        self.assertIn(str(session.repo_root() / "docker"), output.getvalue())

    def test_preflight_command_uses_config_and_store_paths(self):
        args = types.SimpleNamespace(
            config="docker/config/config.yaml",
            store="docker/config/custom-store.yaml",
            host=True,
            skip_mqtt_connect_check=False,
            mqtt_connect_timeout=2.5,
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.preflight(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("scripts/pesetech_preflight.py", command)
        self.assertIn("--config docker/config/config.yaml", command)
        self.assertIn("--store docker/config/custom-store.yaml", command)
        self.assertIn("--host", command)
        self.assertIn("--check-mqtt", command)
        self.assertIn("--mqtt-connect-timeout 2.5", command)

    def test_smoke_command_includes_proof_mode(self):
        args = types.SimpleNamespace(
            config="docker/config/config.yaml",
            broker=None,
            username=None,
            password=None,
            discovery_prefix=None,
            mesh_topic=None,
            device_id=None,
            proof_log="docker/config/pesetech-proof.jsonl",
            proof_run_id="proof-123",
            precondition_visible_start=False,
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.smoke(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("scripts/pesetech_mqtt_smoke.py", command)
        self.assertIn("--config docker/config/config.yaml", command)
        self.assertIn("--wait-state --observe", command)
        self.assertIn("--proof-log docker/config/pesetech-proof.jsonl", command)
        self.assertIn("--run-id proof-123", command)
        self.assertNotIn("--broker", command)
        self.assertNotIn("--precondition-visible-start", command)

    def test_smoke_command_can_precondition_visible_start(self):
        args = types.SimpleNamespace(
            config="docker/config/config.yaml",
            broker=None,
            username=None,
            password=None,
            discovery_prefix=None,
            mesh_topic=None,
            device_id=None,
            proof_log="docker/config/pesetech-proof.jsonl",
            proof_run_id="proof-123",
            precondition_visible_start=True,
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.smoke(args)

        self.assertEqual(exit_code, 0)
        self.assertIn("--precondition-visible-start", output.getvalue())

    def test_smoke_command_can_override_config_values(self):
        args = types.SimpleNamespace(
            config="docker/config/config.yaml",
            broker="homeassistant.local",
            port=1884,
            username="mqtt",
            password="secret",
            discovery_prefix="homeassistant",
            mesh_topic="mqtt_mesh",
            device_id="skylight",
            proof_log="docker/config/pesetech-proof.jsonl",
            proof_run_id="proof-123",
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.smoke(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("--broker homeassistant.local", command)
        self.assertIn("--port 1884", command)
        self.assertIn("--username '<redacted>'", command)
        self.assertIn("--password '<redacted>'", command)
        self.assertNotIn("secret", command)
        self.assertIn("--discovery-prefix homeassistant", command)
        self.assertIn("--mesh-topic mqtt_mesh", command)
        self.assertIn("--device-id skylight", command)
        self.assertIn("--run-id proof-123", command)

    def test_discovery_command_verifies_retained_home_assistant_config(self):
        args = types.SimpleNamespace(
            config="docker/config/config.yaml",
            broker=None,
            username=None,
            password=None,
            discovery_prefix=None,
            mesh_topic=None,
            device_id=None,
            discovery_timeout=30.0,
            candidate_timeout=2.0,
            dump_json=False,
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.discovery(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("scripts/pesetech_mqtt_discovery.py", command)
        self.assertIn("--config docker/config/config.yaml", command)
        self.assertIn("--require-retained", command)
        self.assertIn("--discovery-timeout 30.0", command)
        self.assertIn("--candidate-timeout 2.0", command)
        self.assertNotIn("--broker", command)

    def test_discovery_command_can_override_config_values(self):
        args = types.SimpleNamespace(
            config="docker/config/config.yaml",
            broker="homeassistant.local",
            port=1884,
            username="mqtt",
            password="secret",
            discovery_prefix="homeassistant",
            mesh_topic="mqtt_mesh",
            device_id="skylight",
            default_entity_id="light.kitchen_sky",
            discovery_timeout=45.0,
            candidate_timeout=6.5,
            dump_json=True,
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.discovery(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("--broker homeassistant.local", command)
        self.assertIn("--port 1884", command)
        self.assertIn("--username '<redacted>'", command)
        self.assertIn("--password '<redacted>'", command)
        self.assertNotIn("secret", command)
        self.assertIn("--discovery-prefix homeassistant", command)
        self.assertIn("--mesh-topic mqtt_mesh", command)
        self.assertIn("--device-id skylight", command)
        self.assertIn("--default-entity-id light.kitchen_sky", command)
        self.assertIn("--discovery-timeout 45.0", command)
        self.assertIn("--candidate-timeout 6.5", command)
        self.assertIn("--dump-json", command)

    def test_runtime_check_runs_inside_gateway_container(self):
        args = types.SimpleNamespace(compose_dir="docker", dry_run=True)
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.runtime_check(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("docker compose exec -T app", command)
        self.assertIn("/opt/hass-ble-mesh/scripts/pesetech_runtime_check.py", command)

    def test_scan_uses_noninteractive_container_exec(self):
        args = types.SimpleNamespace(compose_dir="docker", dry_run=True)
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.scan(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("docker compose exec -T app", command)
        self.assertIn("python3 gateway.py --basedir /config scan", command)

    def test_provision_uses_noninteractive_container_exec(self):
        args = types.SimpleNamespace(
            compose_dir="docker",
            dry_run=True,
            uuid="00112233-4455-6677-8899-aabbccddeeff",
            update_config=False,
            config="docker/config/config.yaml",
            device_id="skylight",
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.provision(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertEqual(command.count("docker compose exec -T app"), 3)
        self.assertIn("prov --uuid 00112233-4455-6677-8899-aabbccddeeff add", command)
        self.assertIn("prov --uuid 00112233-4455-6677-8899-aabbccddeeff config", command)
        self.assertIn("prov list", command)

    def test_import_mesh_command_runs_telink_importer(self):
        args = types.SimpleNamespace(
            mesh_json="/tmp/mesh.json",
            config="docker/config/config.yaml",
            store="docker/config/store.yaml",
            device_id="skylight",
            device_name="Pesetech Skylight",
            default_entity_id="light.skylight",
            mesh_candidate=2,
            node_uuid="00112233-4455-6677-8899-aabbccddeeff",
            node_unicast=None,
            local_address="0005",
            force=True,
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.import_mesh(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("scripts/pesetech_import_telink_mesh.py /tmp/mesh.json", command)
        self.assertIn("--config docker/config/config.yaml", command)
        self.assertIn("--store docker/config/store.yaml", command)
        self.assertIn("--device-id skylight", command)
        self.assertIn("--device-name 'Pesetech Skylight'", command)
        self.assertIn("--default-entity-id light.skylight", command)
        self.assertIn("--mesh-candidate 2", command)
        self.assertIn("--node-uuid 00112233-4455-6677-8899-aabbccddeeff", command)
        self.assertIn("--local-address 0005", command)
        self.assertIn("--force", command)

    def test_extract_mesh_command_runs_capture_extractor(self):
        args = types.SimpleNamespace(
            inputs=["/tmp/capture.har"],
            output="/tmp/pesetech_mesh.json",
            candidate=2,
            list=True,
            no_recursive=True,
            max_bytes=12345,
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.extract_mesh(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("scripts/pesetech_extract_mesh_json.py /tmp/capture.har", command)
        self.assertIn("--output /tmp/pesetech_mesh.json", command)
        self.assertIn("--candidate 2", command)
        self.assertIn("--list", command)
        self.assertIn("--no-recursive", command)
        self.assertIn("--max-bytes 12345", command)

    def test_fetch_cloud_mesh_command_uses_token_file_or_env(self):
        args = types.SimpleNamespace(
            output="/tmp/pesetech_mesh.json",
            candidate=2,
            list=True,
            raw_output="/tmp/pesetech-cloud.json",
            report_output="/tmp/pesetech-cloud-report.json",
            region="asia",
            base_url="https://service.lepuiot.com",
            endpoint=["home-list", "sync-data"],
            home_id=["home-1"],
            token_file="/tmp/token.txt",
            token_env="PESETECH_CLOUD_TOKEN",
            username_file="/tmp/user.txt",
            username_env="PESETECH_CLOUD_USERNAME",
            password_file="/tmp/password.txt",
            password_env="PESETECH_CLOUD_PASSWORD",
            user_origin=1,
            timeout=5,
            user_agent="test-agent",
            accept_language="en",
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.fetch_cloud_mesh(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("scripts/pesetech_fetch_cloud_mesh.py", command)
        self.assertIn("--output /tmp/pesetech_mesh.json", command)
        self.assertIn("--candidate 2", command)
        self.assertIn("--raw-output /tmp/pesetech-cloud.json", command)
        self.assertIn("--report-output /tmp/pesetech-cloud-report.json", command)
        self.assertIn("--region asia", command)
        self.assertIn("--base-url https://service.lepuiot.com", command)
        self.assertIn("--endpoint home-list --endpoint sync-data", command)
        self.assertIn("--home-id home-1", command)
        self.assertIn("--token-file /tmp/token.txt", command)
        self.assertIn("--token-env PESETECH_CLOUD_TOKEN", command)
        self.assertIn("--username-file /tmp/user.txt", command)
        self.assertIn("--username-env PESETECH_CLOUD_USERNAME", command)
        self.assertIn("--password-file /tmp/password.txt", command)
        self.assertIn("--password-env PESETECH_CLOUD_PASSWORD", command)
        self.assertIn("--user-origin 1", command)
        self.assertNotIn("--token ", command)
        self.assertNotIn("--password secret", command)

    def test_verify_command_reads_topic_defaults_from_config(self):
        args = types.SimpleNamespace(
            config="docker/config/config.yaml",
            discovery_prefix=None,
            mesh_topic=None,
            device_id=None,
            proof_log="docker/config/pesetech-proof.jsonl",
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.verify(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("scripts/pesetech_verify_proof.py docker/config/pesetech-proof.jsonl", command)
        self.assertIn("--config docker/config/config.yaml", command)
        self.assertNotIn("--discovery-prefix", command)

    def test_verify_command_can_override_config_topic_values(self):
        args = types.SimpleNamespace(
            config="docker/config/config.yaml",
            discovery_prefix="homeassistant",
            mesh_topic="mqtt_mesh",
            device_id="skylight",
            proof_log="docker/config/pesetech-proof.jsonl",
            proof_run_id="proof-123",
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.verify(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("--discovery-prefix homeassistant", command)
        self.assertIn("--mesh-topic mqtt_mesh", command)
        self.assertIn("--device-id skylight", command)
        self.assertIn("--run-id proof-123", command)

    def test_ha_service_command_runs_home_assistant_service_proof_defaults(self):
        args = types.SimpleNamespace(
            ha_url="http://homeassistant.local:8123",
            ha_entity_id="light.skylight",
            ha_token_file=None,
            ha_proof_log="docker/config/pesetech-ha-service-proof.jsonl",
            proof_run_id="proof-123",
            ha_precondition_visible_start=False,
            ha_wait_attributes=False,
            ha_wait_mqtt_state=False,
            ha_wait_mqtt_attributes=False,
            ha_no_wait_state=False,
            ha_no_observe=False,
            ha_list_candidates=False,
            ha_candidate_search="skylight",
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.ha_service(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("scripts/pesetech_ha_service_smoke.py", command)
        self.assertIn("--url http://homeassistant.local:8123", command)
        self.assertIn("--entity-id light.skylight", command)
        self.assertIn("--proof-log docker/config/pesetech-ha-service-proof.jsonl", command)
        self.assertIn("--run-id proof-123", command)
        self.assertIn("--wait-state --observe", command)
        self.assertIn("--candidate-search skylight", command)
        self.assertNotIn("--token-file", command)
        self.assertNotIn("--precondition-visible-start", command)

    def test_ha_service_command_can_precondition_visible_start(self):
        args = types.SimpleNamespace(
            ha_url="http://homeassistant.local:8123",
            ha_entity_id="light.skylight",
            ha_token_file=None,
            ha_proof_log="docker/config/pesetech-ha-service-proof.jsonl",
            proof_run_id="proof-123",
            ha_precondition_visible_start=True,
            ha_wait_attributes=False,
            ha_wait_mqtt_state=False,
            ha_wait_mqtt_attributes=False,
            ha_no_wait_state=False,
            ha_no_observe=False,
            ha_list_candidates=False,
            ha_candidate_search=None,
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.ha_service(args)

        self.assertEqual(exit_code, 0)
        self.assertIn("--precondition-visible-start", output.getvalue())

    def test_ha_service_command_can_wait_for_mqtt_bridge_state(self):
        args = types.SimpleNamespace(
            config="docker/config/config.yaml",
            ha_url="http://homeassistant.local:8123",
            ha_entity_id="light.skylight",
            ha_token_file=None,
            ha_proof_log="docker/config/pesetech-ha-service-proof.jsonl",
            ha_wait_attributes=False,
            ha_wait_mqtt_state=True,
            ha_wait_mqtt_attributes=True,
            ha_no_wait_state=False,
            ha_no_observe=False,
            ha_list_candidates=False,
            ha_candidate_search="skylight",
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.ha_service(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("--wait-mqtt-state --mqtt-config docker/config/config.yaml", command)
        self.assertIn("--wait-mqtt-attributes", command)

    def test_ha_service_command_passes_mqtt_overrides_to_state_watcher(self):
        args = types.SimpleNamespace(
            config="docker/config/config.yaml",
            ha_url="http://homeassistant.local:8123",
            ha_entity_id="light.skylight",
            ha_token_file=None,
            ha_proof_log="docker/config/pesetech-ha-service-proof.jsonl",
            ha_wait_attributes=False,
            ha_wait_mqtt_state=True,
            ha_wait_mqtt_attributes=False,
            ha_mqtt_broker="mqtt.local",
            ha_mqtt_port=1884,
            ha_mqtt_username="hidden-user",
            ha_mqtt_password="mqtt-secret",
            ha_mqtt_discovery_prefix="ha",
            ha_mqtt_mesh_topic="mesh_bridge",
            ha_mqtt_device_id="kitchen_sky",
            ha_no_wait_state=False,
            ha_no_observe=False,
            ha_list_candidates=False,
            ha_candidate_search="skylight",
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.ha_service(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("--wait-mqtt-state --mqtt-config docker/config/config.yaml", command)
        self.assertIn("--mqtt-broker mqtt.local", command)
        self.assertIn("--mqtt-port 1884", command)
        self.assertIn("--mqtt-username '<redacted>'", command)
        self.assertIn("--mqtt-password '<redacted>'", command)
        self.assertNotIn("hidden-user", command)
        self.assertNotIn("mqtt-secret", command)
        self.assertIn("--mqtt-discovery-prefix ha", command)
        self.assertIn("--mqtt-mesh-topic mesh_bridge", command)
        self.assertIn("--mqtt-device-id kitchen_sky", command)

    def test_ha_service_command_can_list_candidates_with_token_file(self):
        args = types.SimpleNamespace(
            config="docker/config/config.yaml",
            ha_url="http://ha.local:8123",
            ha_entity_id="light.skylight",
            ha_token_file="/run/secrets/ha-token",
            ha_proof_log="docker/config/pesetech-ha-service-proof.jsonl",
            ha_wait_attributes=True,
            ha_wait_mqtt_state=False,
            ha_wait_mqtt_attributes=False,
            ha_no_wait_state=False,
            ha_no_observe=False,
            ha_list_candidates=True,
            ha_candidate_search="pesetech",
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.ha_service(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("--token-file /run/secrets/ha-token", command)
        self.assertIn("--list-candidates", command)
        self.assertIn("--candidate-search pesetech", command)
        self.assertNotIn("--wait-state", command)
        self.assertNotIn("--observe", command)

    def test_ha_api_check_command_verifies_token_and_api_before_proof(self):
        args = types.SimpleNamespace(
            ha_url="http://ha.local:8123",
            ha_entity_id="light.pesetech_sky",
            ha_token_file="/run/secrets/ha-token",
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.ha_api_check(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("scripts/pesetech_ha_service_smoke.py", command)
        self.assertIn("--url http://ha.local:8123", command)
        self.assertIn("--check-api", command)
        self.assertNotIn("--entity-id", command)
        self.assertNotIn("--check-entity", command)
        self.assertIn("--token-file /run/secrets/ha-token", command)

    def test_ha_entity_check_command_verifies_target_entity_after_discovery(self):
        args = types.SimpleNamespace(
            ha_url="http://ha.local:8123",
            ha_entity_id="light.pesetech_sky",
            ha_entity_timeout=30.0,
            ha_token_file="/run/secrets/ha-token",
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.ha_entity_check(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("scripts/pesetech_ha_service_smoke.py", command)
        self.assertIn("--url http://ha.local:8123", command)
        self.assertIn("--entity-id light.pesetech_sky", command)
        self.assertIn("--check-entity", command)
        self.assertIn("--candidate-search pesetech_sky", command)
        self.assertIn("--entity-timeout 30.0", command)
        self.assertIn("--token-file /run/secrets/ha-token", command)

    def test_ha_entity_check_uses_explicit_candidate_search_hint(self):
        args = types.SimpleNamespace(
            ha_url="http://ha.local:8123",
            ha_entity_id="light.pesetech_sky",
            ha_entity_timeout=30.0,
            ha_token_file="/run/secrets/ha-token",
            ha_candidate_search="kitchen",
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.ha_entity_check(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("--candidate-search kitchen", command)
        self.assertNotIn("--candidate-search pesetech_sky", command)

    def test_ha_verify_command_checks_home_assistant_service_proof_log(self):
        args = types.SimpleNamespace(
            ha_url="http://homeassistant.local:8123",
            ha_entity_id="light.skylight",
            ha_proof_log="docker/config/pesetech-ha-service-proof.jsonl",
            proof_run_id="proof-123",
            ha_wait_attributes=True,
            ha_wait_mqtt_state=False,
            ha_require_mqtt_state=True,
            ha_require_mqtt_attributes=True,
            ha_allow_missing_state=False,
            ha_allow_service_error=False,
            ha_allow_unobserved=False,
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.ha_verify(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("scripts/pesetech_verify_ha_service_proof.py docker/config/pesetech-ha-service-proof.jsonl", command)
        self.assertIn("--url http://homeassistant.local:8123", command)
        self.assertIn("--entity-id light.skylight", command)
        self.assertIn("--run-id proof-123", command)
        self.assertIn("--require-attributes", command)
        self.assertIn("--require-mqtt-state", command)
        self.assertIn("--require-mqtt-attributes", command)

    def test_final_audit_command_checks_both_real_device_proof_logs(self):
        args = types.SimpleNamespace(
            config="docker/config/config.yaml",
            proof_log="docker/config/pesetech-proof.jsonl",
            ha_proof_log="docker/config/pesetech-ha-service-proof.jsonl",
            final_audit_report="docker/config/pesetech-final-audit.json",
            ha_url="http://homeassistant.local:8123",
            ha_entity_id="light.skylight",
            discovery_prefix="homeassistant",
            mesh_topic="mqtt_mesh",
            device_id="skylight",
            proof_run_id="proof-123",
            allow_unobserved=False,
            allow_different_run_ids=False,
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.final_audit(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("scripts/pesetech_real_device_audit.py", command)
        self.assertIn("--proof-log docker/config/pesetech-proof.jsonl", command)
        self.assertIn("--ha-proof-log docker/config/pesetech-ha-service-proof.jsonl", command)
        self.assertIn("--ha-url http://homeassistant.local:8123", command)
        self.assertIn("--ha-entity-id light.skylight", command)
        self.assertIn("--proof-run-id proof-123", command)
        self.assertIn("--discovery-prefix homeassistant", command)
        self.assertIn("--mesh-topic mqtt_mesh", command)
        self.assertIn("--device-id skylight", command)
        self.assertIn("--output-json docker/config/pesetech-final-audit.json", command)

    def test_diagnostics_command_preserves_topic_overrides(self):
        args = types.SimpleNamespace(
            config="docker/config/config.yaml",
            store="docker/config/store.yaml",
            proof_log="docker/config/pesetech-proof.jsonl",
            ha_proof_log="docker/config/pesetech-ha-service-proof.jsonl",
            final_audit_report="docker/config/pesetech-final-audit.json",
            import_check_report="docker/config/pesetech-import-check.json",
            readiness_report="docker/config/pesetech-readiness.json",
            status_report="docker/config/pesetech-status.json",
            ha_url="http://ha.local:8123",
            ha_entity_id="light.pesetech_sky",
            ha_token_file="/tmp/ha-token.txt",
            ha_candidate_search="pesetech",
            ha_require_attributes=False,
            ha_wait_attributes=True,
            ha_require_mqtt_state=True,
            ha_require_mqtt_attributes=True,
            ha_wait_mqtt_state=False,
            ha_wait_mqtt_attributes=False,
            ha_mqtt_brightness_scale=65280,
            ha_mqtt_brightness_tolerance=2,
            ha_mqtt_mired_tolerance=2,
            broker="mqtt.local",
            port=1884,
            username="hidden-user",
            password="mqtt-secret",
            compose_dir="docker",
            discovery_prefix="ha",
            mesh_topic="mesh_bridge",
            device_id="kitchen_sky",
            candidate_timeout=7.0,
            cloud_output="/share/pesetech_mesh.json",
            cloud_raw_output="/share/raw-cloud.json",
            cloud_report="/share/pesetech_cloud_fetch_report.json",
            cloud_token_file="/share/pesetech_cloud_token.txt",
            cloud_username_file="/share/pesetech_cloud_username.txt",
            cloud_password_file="/share/pesetech_cloud_password.txt",
            cloud_region="asia",
            cloud_candidate="2",
            cloud_home_id="home-1",
            import_mesh_candidate="2",
            proof_run_id="proof-123",
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.diagnostics(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("scripts/pesetech_diagnostics.py", command)
        self.assertIn("--ha-proof-log docker/config/pesetech-ha-service-proof.jsonl", command)
        self.assertIn("--final-audit-report docker/config/pesetech-final-audit.json", command)
        self.assertIn("--import-check-report docker/config/pesetech-import-check.json", command)
        self.assertIn("--readiness-report docker/config/pesetech-readiness.json", command)
        self.assertIn("--status-report docker/config/pesetech-status.json", command)
        self.assertIn("--live-discovery", command)
        self.assertIn("--ha-url http://ha.local:8123", command)
        self.assertIn("--ha-entity-id light.pesetech_sky", command)
        self.assertIn("--ha-token-file /tmp/ha-token.txt", command)
        self.assertIn("--ha-api-context", command)
        self.assertIn("--ha-candidate-search pesetech", command)
        self.assertIn("--ha-require-attributes", command)
        self.assertIn("--ha-require-mqtt-state", command)
        self.assertIn("--ha-require-mqtt-attributes", command)
        self.assertIn("--ha-mqtt-brightness-scale 65280", command)
        self.assertIn("--ha-mqtt-brightness-tolerance 2", command)
        self.assertIn("--ha-mqtt-mired-tolerance 2", command)
        self.assertIn("--broker mqtt.local", command)
        self.assertIn("--port 1884", command)
        self.assertIn("--username '<redacted>'", command)
        self.assertIn("--password '<redacted>'", command)
        self.assertNotIn("hidden-user", command)
        self.assertNotIn("mqtt-secret", command)
        self.assertIn("--discovery-prefix ha", command)
        self.assertIn("--mesh-topic mesh_bridge", command)
        self.assertIn("--device-id kitchen_sky", command)
        self.assertIn("--candidate-timeout 7.0", command)
        self.assertIn("--cloud-output /share/pesetech_mesh.json", command)
        self.assertIn("--cloud-raw-output /share/raw-cloud.json", command)
        self.assertIn("--cloud-report /share/pesetech_cloud_fetch_report.json", command)
        self.assertIn("--cloud-token-file /share/pesetech_cloud_token.txt", command)
        self.assertIn("--cloud-username-file /share/pesetech_cloud_username.txt", command)
        self.assertIn("--cloud-password-file /share/pesetech_cloud_password.txt", command)
        self.assertIn("--cloud-region asia", command)
        self.assertIn("--cloud-candidate 2", command)
        self.assertIn("--cloud-home-id home-1", command)
        self.assertIn("--import-mesh-candidate 2", command)
        self.assertIn("--proof-run-id proof-123", command)

    def test_diagnostics_command_can_skip_docker_checks(self):
        args = types.SimpleNamespace(
            config="docker/config/config.yaml",
            store="docker/config/store.yaml",
            proof_log="docker/config/pesetech-proof.jsonl",
            ha_proof_log="docker/config/pesetech-ha-service-proof.jsonl",
            final_audit_report="docker/config/pesetech-final-audit.json",
            ha_url="http://ha.local:8123",
            ha_entity_id="light.pesetech_sky",
            ha_token_file=None,
            ha_candidate_search="pesetech",
            compose_dir="docker",
            discovery_prefix=None,
            mesh_topic=None,
            device_id=None,
            proof_run_id=None,
            skip_docker=True,
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.diagnostics(args)

        self.assertEqual(exit_code, 0)
        self.assertIn("--skip-docker", output.getvalue())

    def test_prove_dry_run_prints_end_to_end_proof_sequence(self):
        args = types.SimpleNamespace(
            config="docker/config/config.yaml",
            compose_dir="docker",
            broker="mqtt.local",
            port=1884,
            username="hidden-user",
            password="mqtt-secret",
            discovery_prefix="ha",
            mesh_topic="mesh_bridge",
            device_id="kitchen_sky",
            discovery_timeout=30.0,
            candidate_timeout=4.0,
            dump_json=False,
            proof_log="docker/config/pesetech-proof.jsonl",
            proof_run_id="proof-123",
            precondition_visible_start=True,
            store="docker/config/store.yaml",
            host=True,
            start_service=False,
            no_diagnostics=False,
            diagnostics_on_success=False,
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.prove(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("== prepare-proof-logs ==", command)
        self.assertIn("rm -f", command)
        self.assertIn("docker/config/pesetech-proof.jsonl", command)
        self.assertIn("== preflight ==", command)
        self.assertIn("scripts/pesetech_preflight.py", command)
        self.assertIn("--store docker/config/store.yaml", command)
        self.assertIn("== runtime-check ==", command)
        self.assertIn("scripts/pesetech_runtime_check.py", command)
        self.assertIn("== discovery ==", command)
        self.assertIn("scripts/pesetech_mqtt_discovery.py", command)
        self.assertIn("--discovery-timeout 30.0", command)
        self.assertIn("== smoke ==", command)
        self.assertIn("scripts/pesetech_mqtt_smoke.py", command)
        self.assertIn("--run-id proof-123", command)
        self.assertIn("--precondition-visible-start", command)
        self.assertIn("== verify ==", command)
        self.assertIn("scripts/pesetech_verify_proof.py", command)
        self.assertNotIn("== diagnostics ==", command)

    def test_prove_can_start_service_after_preflight(self):
        args = types.SimpleNamespace(
            config="docker/config/config.yaml",
            compose_dir="docker",
            broker=None,
            username=None,
            password=None,
            discovery_prefix=None,
            mesh_topic=None,
            device_id=None,
            discovery_timeout=30.0,
            dump_json=False,
            proof_log="docker/config/pesetech-proof.jsonl",
            store="docker/config/store.yaml",
            host=True,
            start_service=True,
            service_ready_timeout=30.0,
            no_diagnostics=False,
            diagnostics_on_success=False,
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.prove(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("== prepare-proof-logs ==", command)
        self.assertIn("== preflight ==", command)
        self.assertIn("== service ==", command)
        self.assertIn("GATEWAY_MODE=service docker compose up -d --force-recreate", command)
        self.assertIn("== wait-service ==", command)
        self.assertIn("wait up to 30s for docker compose exec -T app true", command)
        self.assertIn("== runtime-check ==", command)
        self.assertLess(command.index("== preflight =="), command.index("== service =="))
        self.assertLess(command.index("== service =="), command.index("== wait-service =="))
        self.assertLess(command.index("== wait-service =="), command.index("== runtime-check =="))

    def test_prepare_proof_logs_removes_stale_logs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            proof_log = Path(temp_dir) / "pesetech-proof.jsonl"
            ha_proof_log = Path(temp_dir) / "pesetech-ha-service-proof.jsonl"
            proof_log.write_text("stale mqtt proof\n", encoding="utf-8")
            ha_proof_log.write_text("stale ha proof\n", encoding="utf-8")
            report = Path(temp_dir) / "pesetech-final-audit.json"
            report.write_text('{"passed": true}\n', encoding="utf-8")
            args = types.SimpleNamespace(
                proof_log=str(proof_log),
                ha_proof_log=str(ha_proof_log),
                final_audit_report=str(report),
                keep_proof_logs=False,
                dry_run=False,
            )

            exit_code = session.prepare_proof_logs(args)

            self.assertEqual(exit_code, 0)
            self.assertFalse(proof_log.exists())
            self.assertFalse(ha_proof_log.exists())
            self.assertFalse(report.exists())

    def test_prepare_proof_logs_can_keep_stale_logs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            proof_log = Path(temp_dir) / "pesetech-proof.jsonl"
            proof_log.write_text("stale mqtt proof\n", encoding="utf-8")
            args = types.SimpleNamespace(
                proof_log=str(proof_log),
                ha_proof_log=None,
                keep_proof_logs=True,
                dry_run=False,
            )

            exit_code = session.prepare_proof_logs(args)

            self.assertEqual(exit_code, 0)
            self.assertTrue(proof_log.exists())

    def test_prove_collects_diagnostics_on_failure(self):
        calls = []

        def ok(name):
            def handler(args):
                calls.append(name)
                return 0
            return handler

        def fail(args):
            calls.append("smoke")
            return 7

        def diagnostic(args):
            calls.append("diagnostics")
            return 0

        original = (
            session.preflight,
            session.runtime_check,
            session.discovery,
            session.smoke,
            session.verify,
            session.ha_service,
            session.ha_verify,
            session.diagnostics,
        )
        try:
            session.preflight = ok("preflight")
            session.runtime_check = ok("runtime-check")
            session.discovery = ok("discovery")
            session.smoke = fail
            session.verify = ok("verify")
            session.ha_service = ok("ha-service")
            session.ha_verify = ok("ha-verify")
            session.diagnostics = diagnostic
            args = types.SimpleNamespace(no_diagnostics=False, diagnostics_on_success=False)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = session.prove(args)
        finally:
            (
                session.preflight,
                session.runtime_check,
                session.discovery,
                session.smoke,
                session.verify,
                session.ha_service,
                session.ha_verify,
                session.diagnostics,
            ) = original

        self.assertEqual(exit_code, 7)
        self.assertEqual(calls, ["preflight", "runtime-check", "discovery", "smoke", "diagnostics"])
        self.assertIn("smoke failed with exit code 7", stderr.getvalue())

    def test_prove_can_append_home_assistant_service_proof(self):
        calls = []

        def ok(name):
            def handler(args):
                calls.append(name)
                return 0
            return handler

        original = (
            session.preflight,
            session.runtime_check,
            session.discovery,
            session.smoke,
            session.verify,
            session.ha_api_check,
            session.ha_entity_check,
            session.ha_service,
            session.ha_verify,
            session.diagnostics,
        )
        try:
            session.preflight = ok("preflight")
            session.runtime_check = ok("runtime-check")
            session.discovery = ok("discovery")
            session.smoke = ok("smoke")
            session.verify = ok("verify")
            session.ha_api_check = ok("ha-api-check")
            session.ha_entity_check = ok("ha-entity-check")
            session.ha_service = ok("ha-service")
            session.ha_verify = ok("ha-verify")
            session.diagnostics = ok("diagnostics")
            args = types.SimpleNamespace(
                ha_service=True,
                ha_wait_mqtt_state=True,
                ha_wait_mqtt_attributes=True,
                no_diagnostics=False,
                diagnostics_on_success=False,
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = session.prove(args)
        finally:
            (
                session.preflight,
                session.runtime_check,
                session.discovery,
                session.smoke,
                session.verify,
                session.ha_api_check,
                session.ha_entity_check,
                session.ha_service,
                session.ha_verify,
                session.diagnostics,
            ) = original

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, ["ha-api-check", "preflight", "runtime-check", "discovery", "ha-entity-check", "smoke", "verify", "ha-service", "ha-verify"])
        self.assertIn("== ha-api-check ==", output.getvalue())
        self.assertIn("== ha-entity-check ==", output.getvalue())
        self.assertIn("== ha-service ==", output.getvalue())
        self.assertIn("== ha-verify ==", output.getvalue())

    def test_prove_can_append_final_real_device_audit_after_home_assistant_service_proof(self):
        calls = []

        def ok(name):
            def handler(args):
                calls.append(name)
                return 0
            return handler

        original = (
            session.preflight,
            session.runtime_check,
            session.discovery,
            session.smoke,
            session.verify,
            session.ha_api_check,
            session.ha_entity_check,
            session.ha_service,
            session.ha_verify,
            session.final_audit,
            session.diagnostics,
        )
        try:
            session.preflight = ok("preflight")
            session.runtime_check = ok("runtime-check")
            session.discovery = ok("discovery")
            session.smoke = ok("smoke")
            session.verify = ok("verify")
            session.ha_api_check = ok("ha-api-check")
            session.ha_entity_check = ok("ha-entity-check")
            session.ha_service = ok("ha-service")
            session.ha_verify = ok("ha-verify")
            session.final_audit = ok("final-audit")
            session.diagnostics = ok("diagnostics")
            args = types.SimpleNamespace(
                ha_service=True,
                final_audit=True,
                ha_wait_mqtt_state=True,
                ha_wait_mqtt_attributes=True,
                no_diagnostics=False,
                diagnostics_on_success=False,
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = session.prove(args)
        finally:
            (
                session.preflight,
                session.runtime_check,
                session.discovery,
                session.smoke,
                session.verify,
                session.ha_api_check,
                session.ha_entity_check,
                session.ha_service,
                session.ha_verify,
                session.final_audit,
                session.diagnostics,
            ) = original

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, ["ha-api-check", "preflight", "runtime-check", "discovery", "ha-entity-check", "smoke", "verify", "ha-service", "ha-verify", "final-audit"])
        self.assertIn("== ha-api-check ==", output.getvalue())
        self.assertIn("== ha-entity-check ==", output.getvalue())
        self.assertIn("== final-audit ==", output.getvalue())

    def test_prove_ha_addon_uses_existing_gateway_without_local_runtime_steps(self):
        calls = []

        def ok(name):
            def handler(args):
                calls.append(name)
                return 0
            return handler

        original = (
            session.prepare_proof_logs,
            session.ha_api_check,
            session.discovery,
            session.ha_entity_check,
            session.smoke,
            session.verify,
            session.ha_service,
            session.ha_verify,
            session.final_audit,
            session.diagnostics,
        )
        try:
            session.prepare_proof_logs = ok("prepare-proof-logs")
            session.ha_api_check = ok("ha-api-check")
            session.discovery = ok("discovery")
            session.ha_entity_check = ok("ha-entity-check")
            session.smoke = ok("smoke")
            session.verify = ok("verify")
            session.ha_service = ok("ha-service")
            session.ha_verify = ok("ha-verify")
            session.final_audit = ok("final-audit")
            session.diagnostics = ok("diagnostics")
            args = types.SimpleNamespace(
                proof_log="docker/config/pesetech-proof.jsonl",
                ha_proof_log="docker/config/pesetech-ha-service-proof.jsonl",
                final_audit_report="docker/config/pesetech-final-audit.json",
                keep_proof_logs=False,
                ha_relaxed_state_proof=False,
                ha_no_wait_state=False,
                ha_wait_attributes=False,
                ha_wait_mqtt_state=False,
                ha_wait_mqtt_attributes=False,
                no_final_audit=False,
                no_diagnostics=False,
                diagnostics_on_success=False,
                proof_run_id="addon-proof-1",
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = session.prove_ha_addon(args)
        finally:
            (
                session.prepare_proof_logs,
                session.ha_api_check,
                session.discovery,
                session.ha_entity_check,
                session.smoke,
                session.verify,
                session.ha_service,
                session.ha_verify,
                session.final_audit,
                session.diagnostics,
            ) = original

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            calls,
            [
                "prepare-proof-logs",
                "ha-api-check",
                "discovery",
                "ha-entity-check",
                "smoke",
                "verify",
                "ha-service",
                "ha-verify",
                "final-audit",
            ],
        )
        self.assertTrue(args.ha_service)
        self.assertTrue(args.ha_wait_attributes)
        self.assertTrue(args.ha_wait_mqtt_state)
        self.assertTrue(args.ha_wait_mqtt_attributes)
        self.assertNotIn("== preflight ==", output.getvalue())
        self.assertNotIn("== runtime-check ==", output.getvalue())

    def test_prove_ha_addon_readiness_only_does_not_move_light(self):
        calls = []

        def ok(name):
            def handler(args):
                calls.append(name)
                return 0
            return handler

        original = (
            session.prepare_proof_logs,
            session.ha_api_check,
            session.discovery,
            session.ha_entity_check,
            session.smoke,
            session.verify,
            session.ha_service,
            session.ha_verify,
            session.final_audit,
            session.diagnostics,
        )
        try:
            session.prepare_proof_logs = ok("prepare-proof-logs")
            session.ha_api_check = ok("ha-api-check")
            session.discovery = ok("discovery")
            session.ha_entity_check = ok("ha-entity-check")
            session.smoke = ok("smoke")
            session.verify = ok("verify")
            session.ha_service = ok("ha-service")
            session.ha_verify = ok("ha-verify")
            session.final_audit = ok("final-audit")
            session.diagnostics = ok("diagnostics")
            args = types.SimpleNamespace(
                proof_log="docker/config/pesetech-proof.jsonl",
                ha_proof_log="docker/config/pesetech-ha-service-proof.jsonl",
                final_audit_report="docker/config/pesetech-final-audit.json",
                keep_proof_logs=False,
                ha_relaxed_state_proof=False,
                ha_no_wait_state=False,
                ha_wait_attributes=False,
                ha_wait_mqtt_state=False,
                ha_wait_mqtt_attributes=False,
                ha_entity_id="light.kitchen_sky",
                default_entity_id=None,
                ha_candidate_search=None,
                readiness_only=True,
                no_final_audit=False,
                no_diagnostics=False,
                diagnostics_on_success=False,
                proof_run_id=None,
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = session.prove_ha_addon(args)
        finally:
            (
                session.prepare_proof_logs,
                session.ha_api_check,
                session.discovery,
                session.ha_entity_check,
                session.smoke,
                session.verify,
                session.ha_service,
                session.ha_verify,
                session.final_audit,
                session.diagnostics,
            ) = original

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, ["ha-api-check", "discovery", "ha-entity-check"])
        self.assertIn("== ha-api-check ==", output.getvalue())
        self.assertIn("== discovery ==", output.getvalue())
        self.assertIn("== ha-entity-check ==", output.getvalue())
        self.assertIn("Readiness check passed without sending light-control commands.", output.getvalue())
        self.assertIn("without --readiness-only while watching light.kitchen_sky", output.getvalue())
        self.assertNotIn("== smoke ==", output.getvalue())
        self.assertNotIn("== ha-service ==", output.getvalue())
        self.assertNotIn("== final-audit ==", output.getvalue())
        self.assertEqual(args.default_entity_id, "light.kitchen_sky")
        self.assertEqual(args.ha_candidate_search, "kitchen_sky")

    def test_prove_ha_addon_cli_defaults_to_configless_proof_path(self):
        original_argv = sys.argv
        output = io.StringIO()
        try:
            sys.argv = [
                "pesetech_hardware_session.py",
                "prove-ha-addon",
                "--dry-run",
                "--broker",
                "mqtt.local",
                "--username",
                "mqtt",
                "--password",
                "secret",
                "--ha-entity-id",
                "light.skylight",
                "--proof-run-id",
                "cli-addon-proof",
            ]
            with redirect_stdout(output):
                with self.assertRaises(SystemExit) as exit_context:
                    session.main()
        finally:
            sys.argv = original_argv

        self.assertEqual(exit_context.exception.code, 0)
        command = output.getvalue()
        self.assertIn(f"--config {session.HA_ADDON_PROOF_CONFIG}", command)
        self.assertNotIn("--config docker/config/config.yaml", command)
        self.assertIn("--broker mqtt.local", command)
        self.assertIn("--username '<redacted>'", command)
        self.assertIn("--password '<redacted>'", command)
        self.assertNotIn("secret", command)

    def test_prove_ha_addon_dry_run_prints_strict_observed_final_audit_path(self):
        args = types.SimpleNamespace(
            config="docker/config/config.yaml",
            compose_dir="docker",
            broker="mqtt.local",
            port=1884,
            username="hidden-user",
            password="mqtt-secret",
            discovery_prefix="ha",
            mesh_topic="mesh_bridge",
            device_id="kitchen_sky",
            discovery_timeout=30.0,
            candidate_timeout=4.0,
            dump_json=False,
            proof_log="docker/config/pesetech-proof.jsonl",
            store="docker/config/store.yaml",
            precondition_visible_start=True,
            ha_url="http://homeassistant.local:8123",
            ha_entity_id="light.kitchen_sky",
            ha_entity_timeout=30.0,
            ha_token_file=None,
            ha_proof_log="docker/config/pesetech-ha-service-proof.jsonl",
            final_audit_report="docker/config/pesetech-final-audit.json",
            ha_precondition_visible_start=True,
            ha_wait_attributes=False,
            ha_wait_mqtt_state=False,
            ha_wait_mqtt_attributes=False,
            ha_no_wait_state=False,
            ha_no_observe=False,
            ha_candidate_search=None,
            ha_allow_missing_state=False,
            ha_allow_service_error=False,
            ha_allow_unobserved=False,
            ha_relaxed_state_proof=False,
            ha_mqtt_broker=None,
            ha_mqtt_port=None,
            ha_mqtt_username=None,
            ha_mqtt_password=None,
            ha_mqtt_discovery_prefix=None,
            ha_mqtt_mesh_topic=None,
            ha_mqtt_device_id=None,
            ha_mqtt_brightness_scale=None,
            ha_mqtt_brightness_tolerance=None,
            ha_mqtt_mired_tolerance=None,
            default_entity_id=None,
            allow_unobserved=False,
            allow_different_run_ids=False,
            keep_proof_logs=False,
            no_final_audit=False,
            no_diagnostics=False,
            diagnostics_on_success=False,
            skip_docker=True,
            proof_run_id="addon-proof-1",
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.prove_ha_addon(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("== ha-api-check ==", command)
        self.assertIn("== discovery ==", command)
        self.assertIn("== smoke ==", command)
        self.assertIn("== ha-service ==", command)
        self.assertIn("== final-audit ==", command)
        self.assertNotIn("== preflight ==", command)
        self.assertNotIn("== runtime-check ==", command)
        self.assertIn("--port 1884", command)
        self.assertIn("--mqtt-port 1884", command)
        self.assertIn("--default-entity-id light.kitchen_sky", command)
        self.assertIn("--candidate-timeout 4.0", command)
        self.assertIn("--entity-id light.kitchen_sky", command)
        self.assertIn("--candidate-search kitchen_sky", command)
        self.assertIn("--wait-state --observe", command)
        self.assertIn("--wait-state --wait-attributes --observe", command)
        self.assertIn("--wait-mqtt-state --mqtt-config docker/config/config.yaml", command)
        self.assertIn("--wait-mqtt-attributes", command)
        self.assertIn("--require-attributes", command)
        self.assertIn("--require-mqtt-state", command)
        self.assertIn("--require-mqtt-attributes", command)
        self.assertIn("--proof-run-id addon-proof-1", command)
        self.assertNotIn("--allow-unobserved", command)
        self.assertEqual(args.default_entity_id, "light.kitchen_sky")
        self.assertEqual(args.ha_candidate_search, "kitchen_sky")

    def test_addon_runbook_prints_install_gates_and_strict_proof_commands(self):
        args = types.SimpleNamespace(
            addon_archive="/tmp/pesetech-ha-local-addon.tar.gz",
            ha_host="ha.local",
            ssh_user="root",
            ha_url="http://ha.local:8123",
            ha_entity_id="light.kitchen_sky",
            broker="mqtt.local",
            port=1884,
            mqtt_auth=True,
            discovery_prefix="ha",
            mesh_topic="mesh_bridge",
            device_id="kitchen_sky",
            candidate_timeout=10.0,
            cloud_region="asia",
            cloud_home_id="home-1",
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.addon_runbook(args)

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        self.assertIn("addon-host-check", text)
        self.assertIn("addon-install", text)
        self.assertIn("scp /tmp/pesetech-ha-local-addon.tar.gz root@ha.local:/addons/pesetech-ha-local-addon.tar.gz", text)
        self.assertIn("operation: runtime-check (no motion)", text)
        self.assertIn("operation: mesh-daemon-check (no motion)", text)
        self.assertIn("operation: cloud-fetch (no motion)", text)
        self.assertIn("operation: import-check (no motion)", text)
        self.assertIn("operation: import (no motion)", text)
        self.assertIn("operation: readiness-test (no motion)", text)
        self.assertIn("operation: move-test (moves real skylight)", text)
        self.assertIn("operation: ha-service-test (moves real skylight)", text)
        self.assertIn("addon-set-operation", text)
        flat_text = " ".join(token for token in text.split() if token != "\\")
        self.assertIn("addon-ha-api-install-local-repo --repository-dir /tmp/pesetech-ha-addon --replace --ha-url http://ha.local:8123 --operation runtime-check", flat_text)
        self.assertIn("addon-set-operation --ha-host ha.local runtime-check --run start", flat_text)
        self.assertIn("addon-set-operation --ha-host ha.local mesh-daemon-check --run restart", flat_text)
        self.assertIn("addon-set-operation --ha-host ha.local status --run restart", flat_text)
        self.assertIn("addon-set-operation --ha-host ha.local cloud-fetch --run restart --cloud-region asia --cloud-home-id home-1", flat_text)
        self.assertIn("addon-set-operation --ha-host ha.local import-check --run restart", flat_text)
        self.assertIn("addon-set-operation --ha-host ha.local import --run restart", flat_text)
        self.assertIn("addon-set-operation --ha-host ha.local preflight --run restart", flat_text)
        self.assertIn("addon-set-operation --ha-host ha.local readiness-test --run restart --ha-url http://ha.local:8123 --ha-entity-id light.kitchen_sky", flat_text)
        self.assertIn("addon-set-operation --ha-host ha.local move-test --run restart", flat_text)
        self.assertIn("addon-set-operation --ha-host ha.local service --run restart", flat_text)
        self.assertIn("addon-set-operation --ha-host ha.local ha-api-check --run restart --ha-url http://ha.local:8123 --ha-entity-id light.kitchen_sky", flat_text)
        self.assertIn("addon-set-operation --ha-host ha.local ha-service-test --run restart --ha-url http://ha.local:8123 --ha-entity-id light.kitchen_sky", flat_text)
        self.assertIn("/share/pesetech_next_operation.json", text)
        self.assertIn("Cloud region: asia", text)
        self.assertIn("Cloud home ID: set add-on cloud_home_id to home-1", text)
        self.assertIn("addon-fetch-status", text)
        self.assertIn("addon-upload-cloud", text)
        self.assertIn("addon-fetch-cloud-report", text)
        self.assertIn("--token-file <local_token_file>", text)
        self.assertIn("--username-file <local_username_file>", text)
        self.assertIn("--password-file <local_password_file>", text)
        self.assertIn("addon-upload-mesh", text)
        self.assertIn("--mesh-json /tmp/pesetech_mesh.json", text)
        self.assertIn("export HOME_ASSISTANT_TOKEN=<long-lived Home Assistant token>", text)
        self.assertIn("Token API operation shortcuts after the add-on is installed", text)
        self.assertIn("addon-ha-api-operation runtime-check --run start --logs", flat_text)
        self.assertIn("addon-ha-api-operation mesh-daemon-check --run restart --logs", flat_text)
        self.assertIn("addon-ha-api-operation cloud-fetch --run restart --logs --cloud-region asia --cloud-home-id home-1", flat_text)
        self.assertIn("addon-ha-api-operation readiness-test --run restart --logs --addon-ha-url http://ha.local:8123 --ha-entity-id light.kitchen_sky", flat_text)
        self.assertIn("verifies expected pass markers in the latest add-on log block", text)
        self.assertIn("addon-ha-api-sequence --through readiness-test --cloud-region asia --addon-ha-url http://ha.local:8123 --ha-entity-id light.kitchen_sky --cloud-home-id home-1", flat_text)
        self.assertIn("export MQTT_USERNAME=<mqtt username>", text)
        self.assertIn("prove-ha-addon", text)
        self.assertIn("--readiness-only", text)
        self.assertIn("--ha-url http://ha.local:8123", text)
        self.assertIn("--ha-entity-id light.kitchen_sky", text)
        self.assertIn("--broker mqtt.local", text)
        self.assertIn("--port 1884", text)
        self.assertIn("--username \"$MQTT_USERNAME\"", text)
        self.assertIn("--password \"$MQTT_PASSWORD\"", text)
        self.assertIn("--discovery-prefix ha", text)
        self.assertIn("--mesh-topic mesh_bridge", text)
        self.assertIn("--device-id kitchen_sky", text)
        self.assertIn("addon-fetch-diagnostics", text)

    def test_addon_runbook_can_omit_mqtt_auth_exports(self):
        args = types.SimpleNamespace(
            addon_archive="/tmp/pesetech-ha-local-addon.tar.gz",
            ha_host="ha.local",
            ssh_user="",
            ha_url="http://ha.local:8123",
            ha_entity_id="light.skylight",
            broker="mqtt.local",
            port=1883,
            mqtt_auth=False,
            discovery_prefix=None,
            mesh_topic=None,
            device_id=None,
            candidate_timeout=None,
            cloud_region="europe",
            cloud_home_id="",
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.addon_runbook(args)

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        self.assertIn("ssh ha.local", text)
        self.assertIn("Cloud home ID: leave cloud_home_id blank to auto-discover home IDs from homeList", text)
        self.assertIn("/share/pesetech_cloud_fetch_report.json homes", text)
        self.assertIn("cloud_home_id set", text)
        self.assertNotIn("MQTT_USERNAME", text)
        self.assertNotIn('--username "$MQTT_USERNAME"', text)
        self.assertNotIn('--password "$MQTT_PASSWORD"', text)

    def test_addon_runbook_can_write_output_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "runbook.txt"
            args = types.SimpleNamespace(
                addon_archive="/tmp/pesetech-ha-local-addon.tar.gz",
                ha_host="ha.local",
                ssh_user="root",
                ha_url="http://ha.local:8123",
                ha_entity_id="light.skylight",
                broker="mqtt.local",
                port=1883,
                mqtt_auth=False,
                discovery_prefix=None,
                mesh_topic=None,
                device_id=None,
                candidate_timeout=10.0,
                cloud_region="europe",
                cloud_home_id="",
                output=str(output_path),
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = session.addon_runbook(args)

            self.assertEqual(exit_code, 0)
            self.assertIn(f"Wrote {output_path}", output.getvalue())
            text = output_path.read_text(encoding="utf-8")

        self.assertIn("Pesetech Home Assistant add-on real-device runbook", text)
        self.assertIn("addon-install", text)
        self.assertIn("prove-ha-addon", text)

    def test_addon_host_check_dry_run_prints_non_destructive_checks(self):
        args = types.SimpleNamespace(
            ha_host="ha.local",
            ha_url="http://ha.local:8123",
            ha_connect_timeout=3.0,
            ssh_user="root",
            ssh_connect_timeout=7,
            ssh_batch_mode=True,
            remote_addons_dir="/addons",
            remote_share_dir="/share",
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.addon_host_check(args)

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        self.assertIn("Pesetech Home Assistant host check", text)
        self.assertIn("PASS: Home Assistant web UI (http://ha.local:8123; dry run)", text)
        self.assertIn("ssh -o ConnectTimeout=7 -o BatchMode=yes root@ha.local true", text)
        self.assertIn("ha apps --help", text)
        self.assertIn("ha addons --help", text)
        self.assertIn("mkdir -p /addons", text)
        self.assertIn("mkdir -p /share", text)
        self.assertIn("PASS: Home Assistant CLI add-on namespace", text)
        self.assertIn("Host check passed", text)

    def test_addon_host_check_passes_with_bluetooth_warning(self):
        calls = []
        responses = [
            (0, ""),
            (0, "addons\n"),
            (0, "/addons\n"),
            (0, "/share\n"),
            (0, "tar\n"),
            (2, ""),
        ]

        def capture(command, cwd=None, env=None, dry_run=False, placeholder=""):
            calls.append(command)
            return responses.pop(0)

        original = session.run_capture_command
        try:
            session.run_capture_command = capture
            args = types.SimpleNamespace(
                ha_host="ha.local",
                ssh_user="",
                remote_addons_dir="/addons",
                remote_share_dir="/share",
                dry_run=False,
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = session.addon_host_check(args)
        finally:
            session.run_capture_command = original

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls[0], ["ssh", "ha.local", "true"])
        self.assertIn("ha addons --help", calls[1][2])
        self.assertIn("mkdir -p /addons", calls[2][2])
        self.assertIn("mkdir -p /share", calls[3][2])
        self.assertIn("command -v tar", calls[4][2])
        self.assertIn("/sys/class/bluetooth/hci", calls[5][2])
        text = output.getvalue()
        self.assertIn("PASS: Home Assistant CLI add-on namespace (addons)", text)
        self.assertIn("WARN: host Bluetooth adapter hint", text)
        self.assertIn("Host check passed with warnings", text)

    def test_addon_host_check_warns_when_ha_url_is_unreachable(self):
        responses = [
            (0, ""),
            (0, "apps\n"),
            (0, "/addons\n"),
            (0, "/share\n"),
            (0, "tar\n"),
            (0, "/sys/class/bluetooth/hci0\n"),
        ]

        def capture(command, cwd=None, env=None, dry_run=False, placeholder=""):
            return responses.pop(0)

        def http_check(url, timeout):
            return False, "connection refused"

        original = (session.run_capture_command, session.check_http_reachable)
        try:
            session.run_capture_command = capture
            session.check_http_reachable = http_check
            args = types.SimpleNamespace(
                ha_host="ha.local",
                ha_url="http://ha.local:8123",
                ha_connect_timeout=1.0,
                ssh_user="",
                remote_addons_dir="/addons",
                remote_share_dir="/share",
                dry_run=False,
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = session.addon_host_check(args)
        finally:
            session.run_capture_command, session.check_http_reachable = original

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        self.assertIn("WARN: Home Assistant web UI", text)
        self.assertIn("connection refused", text)
        self.assertIn("Host check passed with warnings", text)

    def test_addon_host_check_fails_when_required_check_fails(self):
        calls = []
        responses = [
            (0, ""),
            (127, "missing\n"),
            (0, "/addons\n"),
            (0, "/share\n"),
            (0, "tar\n"),
            (0, "/sys/class/bluetooth/hci0\n"),
        ]

        def capture(command, cwd=None, env=None, dry_run=False, placeholder=""):
            calls.append(command)
            return responses.pop(0)

        original = session.run_capture_command
        try:
            session.run_capture_command = capture
            args = types.SimpleNamespace(
                ha_host="ha.local",
                ssh_user="root",
                remote_addons_dir="/addons",
                remote_share_dir="/share",
                dry_run=False,
            )
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                exit_code = session.addon_host_check(args)
        finally:
            session.run_capture_command = original

        self.assertEqual(exit_code, 1)
        self.assertEqual(len(calls), 6)
        self.assertIn("Host check failed", stderr.getvalue())

    def test_addon_host_check_stops_after_ssh_login_failure(self):
        calls = []

        def capture(command, cwd=None, env=None, dry_run=False, placeholder=""):
            calls.append(command)
            return 255, ""

        def http_check(url, timeout):
            return True, "HTTP 200"

        original = (session.run_capture_command, session.check_http_reachable)
        try:
            session.run_capture_command = capture
            session.check_http_reachable = http_check
            args = types.SimpleNamespace(
                ha_host="ha.local",
                ha_url="http://ha.local:8123",
                ha_connect_timeout=1.0,
                ssh_user="root",
                remote_addons_dir="/addons",
                remote_share_dir="/share",
                dry_run=False,
            )
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                exit_code = session.addon_host_check(args)
        finally:
            session.run_capture_command, session.check_http_reachable = original

        self.assertEqual(exit_code, 1)
        self.assertEqual(calls, [["ssh", "root@ha.local", "true"]])
        self.assertIn("SSH is unavailable", stderr.getvalue())
        self.assertIn("Host check failed", stderr.getvalue())

    def test_addon_prepare_git_repo_dry_run_prints_server_and_repository_url(self):
        args = types.SimpleNamespace(
            repository_dir="/tmp/pesetech-ha-addon",
            output_dir="/tmp/pesetech-ha-addon-git",
            repo_name="pesetech-ha-addon.git",
            replace=True,
            port=8766,
            bind="0.0.0.0",
            ha_host="ha.local",
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.addon_prepare_git_repo(args)

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        flat_text = " ".join(token for token in text.split() if token != "\\")
        self.assertIn("Would prepare bare Git repository", text)
        self.assertIn("python3 scripts/pesetech_hardware_session.py addon-serve-git-repo", flat_text)
        self.assertIn("--port 8766", flat_text)
        self.assertIn("pesetech-ha-addon.git", text)
        self.assertIn("Home Assistant Settings -> Add-ons", text)

    def test_addon_serve_git_repo_dry_run_prints_repository_url(self):
        args = types.SimpleNamespace(
            repository_dir="/tmp/pesetech-ha-addon",
            output_dir="/tmp/pesetech-ha-addon-git",
            repo_name="pesetech-ha-addon.git",
            prepare=False,
            replace=False,
            port=8766,
            bind="0.0.0.0",
            ha_host="ha.local",
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.addon_serve_git_repo(args)

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        self.assertIn("Pesetech Home Assistant add-on repository server", text)
        self.assertIn("pesetech-ha-addon.git", text)
        self.assertIn("Home Assistant Settings -> Add-ons", text)
        self.assertIn("Keep this command running", text)
        self.assertIn("Dry run only; not starting HTTP server.", text)

    def test_addon_serve_git_repo_reports_missing_prepared_repository(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            args = types.SimpleNamespace(
                repository_dir="/tmp/pesetech-ha-addon",
                output_dir=str(Path(temp_dir) / "git-root"),
                repo_name="pesetech-test.git",
                prepare=False,
                replace=False,
                port=8766,
                bind="127.0.0.1",
                ha_host="ha.local",
                dry_run=False,
            )
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                exit_code = session.addon_serve_git_repo(args)

            self.assertEqual(exit_code, 1)
            self.assertIn("Prepared Git repository is missing", stderr.getvalue())
            self.assertIn("addon-prepare-git-repo --replace", stderr.getvalue())

    def test_find_hassio_addon_slug_accepts_prefixed_store_slug(self):
        payload = {
            "data": {
                "addons": [
                    {"slug": "core_mosquitto", "name": "Mosquitto broker"},
                    {"slug": "local_pesetech_ble_mesh", "name": "Pesetech BLE Mesh Gateway"},
                ]
            }
        }

        slug = session.find_hassio_addon_slug(payload, "pesetech_ble_mesh", "Pesetech BLE Mesh Gateway")

        self.assertEqual(slug, "local_pesetech_ble_mesh")

    def test_addon_ha_api_install_dry_run_redacts_secret_options(self):
        args = types.SimpleNamespace(
            ha_url="http://ha.local:8123",
            hassio_url="",
            token="",
            token_file="",
            token_env="HOME_ASSISTANT_TOKEN",
            auth_header="authorization",
            timeout=30.0,
            repository_url="http://mac.local:8766/pesetech-ha-addon.git",
            repo_name="pesetech-ha-addon.git",
            port=8766,
            ha_host="ha.local",
            slug="pesetech_ble_mesh",
            name="Pesetech BLE Mesh Gateway",
            store_slug="local_pesetech_ble_mesh",
            installed_slug="local_pesetech_ble_mesh",
            operation="cloud-fetch",
            option=["cloud_password=secret", "cloud_username=user@example.com"],
            start=False,
            skip_repository=False,
            skip_install=False,
            skip_options=False,
            repository_exists_ok=True,
            install_exists_ok=True,
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.addon_ha_api_install(args)

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        self.assertIn("POST http://ha.local:8123/api/hassio/store/repositories", text)
        self.assertIn("POST http://ha.local:8123/api/hassio/store/addons/local_pesetech_ble_mesh/install", text)
        self.assertIn("POST http://ha.local:8123/api/hassio/addons/local_pesetech_ble_mesh/options", text)
        self.assertIn('"operation": "cloud-fetch"', text)
        self.assertIn('"cloud_password": "<redacted>"', text)
        self.assertNotIn("secret", text)

    def test_addon_ha_api_install_builds_supervisor_requests(self):
        calls = []

        class FakeResponse:
            def __init__(self, payload):
                self.status = 200
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        def fake_urlopen(request, timeout):
            body = None
            if request.data:
                body = json.loads(request.data.decode("utf-8"))
            calls.append((request.get_method(), request.full_url, request.get_header("Authorization"), body))
            if request.full_url.endswith("/store/addons"):
                return FakeResponse(
                    {
                        "data": {
                            "addons": [
                                {
                                    "slug": "local_pesetech_ble_mesh",
                                    "name": "Pesetech BLE Mesh Gateway",
                                }
                            ]
                        }
                    }
                )
            return FakeResponse({"result": "ok"})

        args = types.SimpleNamespace(
            ha_url="http://ha.local:8123",
            hassio_url="",
            token="ha-token",
            token_file="",
            token_env="HOME_ASSISTANT_TOKEN",
            auth_header="authorization",
            timeout=30.0,
            repository_url="http://mac.local:8766/pesetech-ha-addon.git",
            repo_name="pesetech-ha-addon.git",
            port=8766,
            ha_host="ha.local",
            slug="pesetech_ble_mesh",
            name="Pesetech BLE Mesh Gateway",
            store_slug="",
            installed_slug="",
            operation="runtime-check",
            option=[],
            start=True,
            skip_repository=False,
            skip_install=False,
            skip_options=False,
            repository_exists_ok=True,
            install_exists_ok=True,
            dry_run=False,
        )
        original_urlopen = session.urllib.request.urlopen
        output = io.StringIO()
        try:
            session.urllib.request.urlopen = fake_urlopen
            with redirect_stdout(output):
                exit_code = session.addon_ha_api_install(args)
        finally:
            session.urllib.request.urlopen = original_urlopen

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            [call[1] for call in calls],
            [
                "http://ha.local:8123/api/hassio/store/repositories",
                "http://ha.local:8123/api/hassio/store/addons",
                "http://ha.local:8123/api/hassio/store/addons/local_pesetech_ble_mesh/install",
                "http://ha.local:8123/api/hassio/addons/local_pesetech_ble_mesh/info",
                "http://ha.local:8123/api/hassio/addons/local_pesetech_ble_mesh/options",
                "http://ha.local:8123/api/hassio/addons/local_pesetech_ble_mesh/start",
            ],
        )
        self.assertTrue(all(call[2] == "Bearer ha-token" for call in calls))
        self.assertEqual(calls[0][3], {"repository": "http://mac.local:8766/pesetech-ha-addon.git"})
        self.assertEqual(calls[4][3]["options"]["operation"], "runtime-check")
        self.assertNotIn("ha-token", output.getvalue())

    def test_addon_ha_api_install_can_verify_first_runtime_log_gate(self):
        calls = []

        class FakeResponse:
            def __init__(self, payload):
                self.status = 200
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                if isinstance(self.payload, (dict, list)):
                    return json.dumps(self.payload).encode("utf-8")
                return str(self.payload).encode("utf-8")

        def fake_urlopen(request, timeout):
            body = None
            if request.data:
                body = json.loads(request.data.decode("utf-8"))
            calls.append((request.get_method(), request.full_url, request.get_header("Authorization"), body))
            if request.full_url.endswith("/store/addons"):
                return FakeResponse(
                    {
                        "data": {
                            "addons": [
                                {
                                    "slug": "local_pesetech_ble_mesh",
                                    "name": "Pesetech BLE Mesh Gateway",
                                }
                            ]
                        }
                    }
                )
            if request.full_url.endswith("/logs"):
                return FakeResponse("Pesetech operation gate: runtime-check\nRuntime check passed.\n")
            return FakeResponse({"result": "ok"})

        args = types.SimpleNamespace(
            ha_url="http://ha.local:8123",
            hassio_url="",
            token="ha-token",
            token_file="",
            token_env="HOME_ASSISTANT_TOKEN",
            auth_header="authorization",
            timeout=30.0,
            repository_url="http://mac.local:8766/pesetech-ha-addon.git",
            repo_name="pesetech-ha-addon.git",
            port=8766,
            ha_host="ha.local",
            slug="pesetech_ble_mesh",
            name="Pesetech BLE Mesh Gateway",
            store_slug="",
            installed_slug="",
            operation="runtime-check",
            option=[],
            start=True,
            logs=True,
            logs_delay=0.0,
            logs_tail=0,
            require_log_gate=True,
            gate_timeout=1.0,
            gate_poll_interval=0.01,
            skip_repository=False,
            skip_install=False,
            skip_options=False,
            repository_exists_ok=True,
            install_exists_ok=True,
            dry_run=False,
        )
        original_urlopen = session.urllib.request.urlopen
        output = io.StringIO()
        try:
            session.urllib.request.urlopen = fake_urlopen
            with redirect_stdout(output):
                exit_code = session.addon_ha_api_install(args)
        finally:
            session.urllib.request.urlopen = original_urlopen

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            [call[1] for call in calls],
            [
                "http://ha.local:8123/api/hassio/store/repositories",
                "http://ha.local:8123/api/hassio/store/addons",
                "http://ha.local:8123/api/hassio/store/addons/local_pesetech_ble_mesh/install",
                "http://ha.local:8123/api/hassio/addons/local_pesetech_ble_mesh/info",
                "http://ha.local:8123/api/hassio/addons/local_pesetech_ble_mesh/options",
                "http://ha.local:8123/api/hassio/addons/local_pesetech_ble_mesh/start",
                "http://ha.local:8123/api/hassio/addons/local_pesetech_ble_mesh/logs",
            ],
        )
        text = output.getvalue()
        self.assertIn("Verified operation=runtime-check log gate", text)
        self.assertIn("Runtime check passed.", text)

    def test_addon_ha_api_install_rejects_log_gate_without_start(self):
        args = types.SimpleNamespace(
            ha_url="http://ha.local:8123",
            hassio_url="",
            token="",
            token_file="",
            token_env="HOME_ASSISTANT_TOKEN",
            auth_header="authorization",
            timeout=30.0,
            repository_url="http://mac.local:8766/pesetech-ha-addon.git",
            repo_name="pesetech-ha-addon.git",
            port=8766,
            ha_host="ha.local",
            slug="pesetech_ble_mesh",
            name="Pesetech BLE Mesh Gateway",
            store_slug="local_pesetech_ble_mesh",
            installed_slug="local_pesetech_ble_mesh",
            operation="runtime-check",
            option=[],
            start=False,
            logs=False,
            logs_delay=0.0,
            logs_tail=0,
            require_log_gate=True,
            gate_timeout=1.0,
            gate_poll_interval=0.01,
            skip_repository=True,
            skip_install=True,
            skip_options=True,
            repository_exists_ok=True,
            install_exists_ok=True,
            dry_run=True,
        )
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            exit_code = session.addon_ha_api_install(args)

        self.assertEqual(exit_code, 2)
        self.assertIn("--require-log-gate needs --start", stderr.getvalue())

    def test_addon_ha_api_install_local_repo_dry_run_serves_then_installs(self):
        args = types.SimpleNamespace(
            dry_run=True,
            repository_dir="/tmp/pesetech-ha-addon",
            output_dir="/tmp/pesetech-ha-addon-git",
            repo_name="pesetech-ha-addon.git",
            replace=True,
            prepare=True,
            port=8766,
            bind="0.0.0.0",
            ha_host="ha.local",
            ha_url="http://ha.local:8123",
            hassio_url="",
            token="",
            token_file="",
            token_env="HOME_ASSISTANT_TOKEN",
            auth_header="authorization",
            timeout=30.0,
            repository_url="http://mac.local:8766/pesetech-ha-addon.git",
            slug="pesetech_ble_mesh",
            name="Pesetech BLE Mesh Gateway",
            store_slug="local_pesetech_ble_mesh",
            installed_slug="local_pesetech_ble_mesh",
            operation="runtime-check",
            option=[],
            start=True,
            logs=True,
            logs_delay=0.0,
            logs_tail=120,
            require_log_gate=True,
            gate_timeout=120.0,
            gate_poll_interval=5.0,
            skip_repository=False,
            skip_install=False,
            skip_options=False,
            repository_exists_ok=True,
            install_exists_ok=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.addon_ha_api_install_local_repo(args)

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        self.assertIn("Would prepare bare Git repository", text)
        self.assertIn("Home Assistant API local-repository install", text)
        self.assertIn("Dry run only; not starting the temporary repository server.", text)
        self.assertIn("POST http://ha.local:8123/api/hassio/store/repositories", text)
        self.assertIn("Dry run log gate for operation=runtime-check", text)

    def test_addon_ha_api_operation_dry_run_redacts_secret_options(self):
        args = types.SimpleNamespace(
            ha_url="http://ha.local:8123",
            hassio_url="",
            token="",
            token_file="",
            token_env="HOME_ASSISTANT_TOKEN",
            auth_header="authorization",
            timeout=30.0,
            slug="pesetech_ble_mesh",
            name="Pesetech BLE Mesh Gateway",
            installed_slug="local_pesetech_ble_mesh",
            no_discover_slug=False,
            operation="cloud-fetch",
            option=[],
            run="restart",
            skip_options=False,
            logs=True,
            logs_delay=0.0,
            logs_tail=0,
            cloud_token="token-1",
            cloud_token_file="",
            cloud_token_env="PESETECH_CLOUD_TOKEN",
            cloud_username="",
            cloud_username_file="",
            cloud_username_env="PESETECH_CLOUD_USERNAME",
            cloud_password="",
            cloud_password_file="",
            cloud_password_env="PESETECH_CLOUD_PASSWORD",
            cloud_region="asia",
            cloud_home_id="home-1",
            cloud_candidate=None,
            import_mesh_candidate=None,
            import_node_uuid=None,
            import_node_unicast=None,
            import_local_address=None,
            import_force=False,
            addon_ha_url=None,
            ha_entity_id=None,
            discovery_prefix=None,
            node_id=None,
            device_id=None,
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.addon_ha_api_operation(args)

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        self.assertIn("POST http://ha.local:8123/api/hassio/addons/local_pesetech_ble_mesh/options", text)
        self.assertIn('"cloud_token": "<redacted>"', text)
        self.assertIn('"cloud_region": "asia"', text)
        self.assertIn("POST http://ha.local:8123/api/hassio/addons/local_pesetech_ble_mesh/restart", text)
        self.assertIn("GET http://ha.local:8123/api/hassio/addons/local_pesetech_ble_mesh/logs", text)
        self.assertNotIn("token-1", text)

    def test_addon_ha_api_operation_discovers_slug_runs_and_fetches_logs(self):
        calls = []

        class FakeResponse:
            def __init__(self, body, status=200):
                self.status = status
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                if isinstance(self.body, (dict, list)):
                    return json.dumps(self.body).encode("utf-8")
                return str(self.body).encode("utf-8")

        def fake_urlopen(request, timeout):
            body = None
            if request.data:
                body = json.loads(request.data.decode("utf-8"))
            calls.append((request.get_method(), request.full_url, request.get_header("Authorization"), body))
            if request.full_url.endswith("/addons") and request.get_method() == "GET":
                return FakeResponse(
                    {
                        "data": {
                            "addons": [
                                {
                                    "slug": "local_pesetech_ble_mesh",
                                    "name": "Pesetech BLE Mesh Gateway",
                                }
                            ]
                        }
                    }
                )
            if request.full_url.endswith("/logs"):
                return FakeResponse("first\nsecond\nthird\n")
            return FakeResponse({"result": "ok"})

        args = types.SimpleNamespace(
            ha_url="http://ha.local:8123",
            hassio_url="",
            token="ha-token",
            token_file="",
            token_env="HOME_ASSISTANT_TOKEN",
            auth_header="authorization",
            timeout=30.0,
            slug="pesetech_ble_mesh",
            name="Pesetech BLE Mesh Gateway",
            installed_slug="",
            no_discover_slug=False,
            operation="readiness-test",
            option=[],
            run="restart",
            skip_options=False,
            logs=True,
            logs_delay=0.0,
            logs_tail=2,
            cloud_token="",
            cloud_token_file="",
            cloud_token_env="PESETECH_CLOUD_TOKEN",
            cloud_username="",
            cloud_username_file="",
            cloud_username_env="PESETECH_CLOUD_USERNAME",
            cloud_password="",
            cloud_password_file="",
            cloud_password_env="PESETECH_CLOUD_PASSWORD",
            cloud_region=None,
            cloud_home_id=None,
            cloud_candidate=None,
            import_mesh_candidate=None,
            import_node_uuid=None,
            import_node_unicast=None,
            import_local_address=None,
            import_force=False,
            addon_ha_url="http://supervisor/core",
            ha_entity_id="light.skylight",
            discovery_prefix=None,
            node_id=None,
            device_id=None,
            dry_run=False,
        )
        original_urlopen = session.urllib.request.urlopen
        output = io.StringIO()
        try:
            session.urllib.request.urlopen = fake_urlopen
            with redirect_stdout(output):
                exit_code = session.addon_ha_api_operation(args)
        finally:
            session.urllib.request.urlopen = original_urlopen

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            [call[1] for call in calls],
            [
                "http://ha.local:8123/api/hassio/addons",
                "http://ha.local:8123/api/hassio/addons/local_pesetech_ble_mesh/info",
                "http://ha.local:8123/api/hassio/addons/local_pesetech_ble_mesh/options",
                "http://ha.local:8123/api/hassio/addons/local_pesetech_ble_mesh/restart",
                "http://ha.local:8123/api/hassio/addons/local_pesetech_ble_mesh/logs",
            ],
        )
        self.assertTrue(all(call[2] == "Bearer ha-token" for call in calls))
        self.assertEqual(calls[2][3]["options"]["operation"], "readiness-test")
        self.assertEqual(calls[2][3]["options"]["ha_url"], "http://supervisor/core")
        self.assertEqual(calls[2][3]["options"]["ha_entity_id"], "light.skylight")
        self.assertIn("second\nthird", output.getvalue())
        self.assertNotIn("first", output.getvalue())

    def test_addon_log_gate_match_scopes_to_latest_operation_block(self):
        raw = "\n".join(
            [
                "Pesetech operation gate: readiness-test",
                "Readiness-test passed without publishing light-control commands.",
                "Pesetech operation gate: readiness-test",
                "Starting gateway for readiness-test.",
            ]
        )

        matched, detail = session.addon_log_gate_match(raw, "readiness-test")

        self.assertFalse(matched)
        self.assertIn("missing success marker", detail)

    def test_addon_ha_api_operation_waits_for_required_log_gate(self):
        calls = []
        log_responses = [
            "Pesetech operation gate: readiness-test\nStarting gateway for readiness-test.\n",
            "Pesetech operation gate: readiness-test\nReadiness-test passed without publishing light-control commands.\n",
        ]

        class FakeResponse:
            def __init__(self, body, status=200):
                self.status = status
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                if isinstance(self.body, (dict, list)):
                    return json.dumps(self.body).encode("utf-8")
                return str(self.body).encode("utf-8")

        def fake_urlopen(request, timeout):
            body = None
            if request.data:
                body = json.loads(request.data.decode("utf-8"))
            calls.append((request.get_method(), request.full_url, body))
            if request.full_url.endswith("/logs"):
                return FakeResponse(log_responses.pop(0))
            return FakeResponse({"result": "ok"})

        args = types.SimpleNamespace(
            ha_url="http://ha.local:8123",
            hassio_url="",
            token="ha-token",
            token_file="",
            token_env="HOME_ASSISTANT_TOKEN",
            auth_header="authorization",
            timeout=30.0,
            slug="pesetech_ble_mesh",
            name="Pesetech BLE Mesh Gateway",
            installed_slug="local_pesetech_ble_mesh",
            no_discover_slug=True,
            operation="readiness-test",
            option=[],
            run="restart",
            skip_options=False,
            logs=True,
            logs_delay=0.0,
            logs_tail=0,
            require_log_gate=True,
            gate_timeout=1.0,
            gate_poll_interval=0.01,
            cloud_token="",
            cloud_token_file="",
            cloud_token_env="PESETECH_CLOUD_TOKEN",
            cloud_username="",
            cloud_username_file="",
            cloud_username_env="PESETECH_CLOUD_USERNAME",
            cloud_password="",
            cloud_password_file="",
            cloud_password_env="PESETECH_CLOUD_PASSWORD",
            cloud_region=None,
            cloud_home_id=None,
            cloud_candidate=None,
            import_mesh_candidate=None,
            import_node_uuid=None,
            import_node_unicast=None,
            import_local_address=None,
            import_force=False,
            addon_ha_url="http://supervisor/core",
            ha_entity_id="light.skylight",
            discovery_prefix=None,
            node_id=None,
            device_id=None,
            dry_run=False,
        )
        original_urlopen = session.urllib.request.urlopen
        output = io.StringIO()
        try:
            session.urllib.request.urlopen = fake_urlopen
            with redirect_stdout(output):
                exit_code = session.addon_ha_api_operation(args)
        finally:
            session.urllib.request.urlopen = original_urlopen

        self.assertEqual(exit_code, 0)
        self.assertEqual(len([call for call in calls if call[1].endswith("/logs")]), 2)
        self.assertIn("Verified operation=readiness-test log gate", output.getvalue())
        self.assertIn("Readiness-test passed without publishing light-control commands.", output.getvalue())

    def test_addon_ha_api_operation_fails_when_required_log_gate_is_missing(self):
        calls = []

        class FakeResponse:
            status = 200

            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                if isinstance(self.body, (dict, list)):
                    return json.dumps(self.body).encode("utf-8")
                return str(self.body).encode("utf-8")

        def fake_urlopen(request, timeout):
            calls.append((request.get_method(), request.full_url))
            if request.full_url.endswith("/logs"):
                return FakeResponse("Pesetech operation gate: runtime-check\nRuntime check starting.\n")
            return FakeResponse({"result": "ok"})

        args = types.SimpleNamespace(
            ha_url="http://ha.local:8123",
            hassio_url="",
            token="ha-token",
            token_file="",
            token_env="HOME_ASSISTANT_TOKEN",
            auth_header="authorization",
            timeout=30.0,
            slug="pesetech_ble_mesh",
            name="Pesetech BLE Mesh Gateway",
            installed_slug="local_pesetech_ble_mesh",
            no_discover_slug=True,
            operation="runtime-check",
            option=[],
            run="start",
            skip_options=False,
            logs=True,
            logs_delay=0.0,
            logs_tail=0,
            require_log_gate=True,
            gate_timeout=0.0,
            gate_poll_interval=0.01,
            cloud_token="",
            cloud_token_file="",
            cloud_token_env="PESETECH_CLOUD_TOKEN",
            cloud_username="",
            cloud_username_file="",
            cloud_username_env="PESETECH_CLOUD_USERNAME",
            cloud_password="",
            cloud_password_file="",
            cloud_password_env="PESETECH_CLOUD_PASSWORD",
            cloud_region=None,
            cloud_home_id=None,
            cloud_candidate=None,
            import_mesh_candidate=None,
            import_node_uuid=None,
            import_node_unicast=None,
            import_local_address=None,
            import_force=False,
            addon_ha_url=None,
            ha_entity_id=None,
            discovery_prefix=None,
            node_id=None,
            device_id=None,
            dry_run=False,
        )
        original_urlopen = session.urllib.request.urlopen
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            session.urllib.request.urlopen = fake_urlopen
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = session.addon_ha_api_operation(args)
        finally:
            session.urllib.request.urlopen = original_urlopen

        self.assertEqual(exit_code, 1)
        self.assertEqual(len([call for call in calls if call[1].endswith("/logs")]), 1)
        self.assertIn("did not pass its log gate", stderr.getvalue())
        self.assertIn("Runtime check starting.", stdout.getvalue())

    def test_addon_ha_api_sequence_dry_run_stops_at_readiness_without_movement(self):
        args = types.SimpleNamespace(
            ha_url="http://ha.local:8123",
            hassio_url="",
            token="",
            token_file="",
            token_env="HOME_ASSISTANT_TOKEN",
            auth_header="authorization",
            timeout=30.0,
            slug="pesetech_ble_mesh",
            name="Pesetech BLE Mesh Gateway",
            installed_slug="local_pesetech_ble_mesh",
            no_discover_slug=False,
            through="readiness-test",
            allow_movement=False,
            skip_cloud_fetch=False,
            option=[],
            logs=True,
            logs_delay=0.0,
            logs_tail=0,
            cloud_token="token-1",
            cloud_token_file="",
            cloud_token_env="PESETECH_CLOUD_TOKEN",
            cloud_username="",
            cloud_username_file="",
            cloud_username_env="PESETECH_CLOUD_USERNAME",
            cloud_password="",
            cloud_password_file="",
            cloud_password_env="PESETECH_CLOUD_PASSWORD",
            cloud_region="europe",
            cloud_home_id="home-1",
            cloud_candidate=None,
            import_mesh_candidate=1,
            import_node_uuid=None,
            import_node_unicast=None,
            import_local_address=None,
            import_force=False,
            addon_ha_url="http://supervisor/core",
            ha_entity_id="light.skylight",
            discovery_prefix=None,
            node_id=None,
            device_id=None,
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.addon_ha_api_sequence(args)

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        for operation in (
            "runtime-check",
            "mesh-daemon-check",
            "status",
            "cloud-fetch",
            "import-check",
            "import",
            "preflight",
            "readiness-test",
        ):
            self.assertIn(f"== {operation} ==", text)
        self.assertNotIn("== move-test ==", text)
        self.assertIn("Movement:    disabled", text)
        self.assertIn("Log gates:   required", text)
        self.assertIn('"cloud_token": "<redacted>"', text)
        self.assertNotIn("token-1", text)
        self.assertIn('"ha_entity_id": "light.skylight"', text)

    def test_addon_ha_api_sequence_requires_explicit_movement_opt_in(self):
        args = types.SimpleNamespace(
            ha_url="http://ha.local:8123",
            hassio_url="",
            token="",
            token_file="",
            token_env="HOME_ASSISTANT_TOKEN",
            auth_header="authorization",
            timeout=30.0,
            slug="pesetech_ble_mesh",
            name="Pesetech BLE Mesh Gateway",
            installed_slug="local_pesetech_ble_mesh",
            no_discover_slug=False,
            through="move-test",
            allow_movement=False,
            skip_cloud_fetch=True,
            option=[],
            logs=True,
            logs_delay=0.0,
            logs_tail=0,
            cloud_token="",
            cloud_token_file="",
            cloud_token_env="PESETECH_CLOUD_TOKEN",
            cloud_username="",
            cloud_username_file="",
            cloud_username_env="PESETECH_CLOUD_USERNAME",
            cloud_password="",
            cloud_password_file="",
            cloud_password_env="PESETECH_CLOUD_PASSWORD",
            cloud_region=None,
            cloud_home_id=None,
            cloud_candidate=None,
            import_mesh_candidate=None,
            import_node_uuid=None,
            import_node_unicast=None,
            import_local_address=None,
            import_force=False,
            addon_ha_url=None,
            ha_entity_id=None,
            discovery_prefix=None,
            node_id=None,
            device_id=None,
            dry_run=True,
        )
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            exit_code = session.addon_ha_api_sequence(args)

        self.assertEqual(exit_code, 2)
        self.assertIn("Refusing to include movement operations", stderr.getvalue())
        self.assertIn("move-test", stderr.getvalue())

    @unittest.skipUnless(shutil.which("git"), "git is required")
    def test_addon_prepare_git_repo_creates_cloneable_bare_repository(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source = temp_path / "ha-addon"
            source.mkdir()
            (source / "repository.yaml").write_text("name: Test Pesetech\n", encoding="utf-8")
            addon = source / "pesetech_ble_mesh"
            addon.mkdir()
            (addon / "config.yaml").write_text('name: "Pesetech BLE Mesh Gateway"\n', encoding="utf-8")
            output_dir = temp_path / "git-root"
            args = types.SimpleNamespace(
                repository_dir=str(source),
                output_dir=str(output_dir),
                repo_name="pesetech-test.git",
                replace=False,
                port=8766,
                bind="127.0.0.1",
                ha_host="ha.local",
                dry_run=False,
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = session.addon_prepare_git_repo(args)

            self.assertEqual(exit_code, 0)
            bare_repo = output_dir / "pesetech-test.git"
            self.assertTrue((bare_repo / "HEAD").exists())
            clone_dir = temp_path / "clone"
            subprocess.run(["git", "clone", str(bare_repo), str(clone_dir)], check=True, capture_output=True, text=True)
            self.assertTrue((clone_dir / "repository.yaml").is_file())
            self.assertTrue((clone_dir / "pesetech_ble_mesh" / "config.yaml").is_file())

    def test_addon_install_dry_run_copies_extracts_and_checks_layout(self):
        args = types.SimpleNamespace(
            addon_archive="/tmp/pesetech-ha-local-addon.tar.gz",
            ha_host="ha.local",
            ssh_user="root",
            remote_addons_dir="/addons",
            slug="pesetech_ble_mesh",
            replace=False,
            dry_run=True,
            skip_verify=False,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.addon_install(args)

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        self.assertIn("python3 scripts/pesetech_verify_addon_package.py --local-app /tmp/pesetech-ha-local-addon.tar.gz", text)
        self.assertIn("ssh root@ha.local 'mkdir -p /addons'", text)
        self.assertIn("scp /tmp/pesetech-ha-local-addon.tar.gz root@ha.local:/addons/pesetech-ha-local-addon.tar.gz", text)
        self.assertIn("ssh root@ha.local 'tar -xzf /addons/pesetech-ha-local-addon.tar.gz -C /addons'", text)
        self.assertIn("test -f /addons/pesetech_ble_mesh/config.yaml", text)
        self.assertIn("test -f /addons/pesetech_ble_mesh/run.sh", text)
        self.assertIn("test -d /addons/pesetech_ble_mesh/source", text)
        self.assertIn("operation=runtime-check", text)
        self.assertNotIn("rm -rf", text)

    def test_addon_install_replace_removes_remote_folder_before_extract(self):
        calls = []

        def run(command, cwd=None, env=None, dry_run=False):
            calls.append(command)
            return 0

        original = session.run_command
        try:
            session.run_command = run
            with tempfile.NamedTemporaryFile(suffix=".tar.gz") as archive:
                args = types.SimpleNamespace(
                    addon_archive=archive.name,
                    ha_host="ha.local",
                    ssh_user="",
                    remote_addons_dir="/addons",
                    slug="pesetech_ble_mesh",
                    replace=True,
                    dry_run=False,
                    skip_verify=False,
                )

                exit_code = session.addon_install(args)
        finally:
            session.run_command = original

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls[0][0:3], ["python3", "scripts/pesetech_verify_addon_package.py", "--local-app"])
        self.assertEqual(calls[0][3], archive.name)
        self.assertEqual(calls[1], ["ssh", "ha.local", "mkdir -p /addons"])
        self.assertEqual(calls[2][0], "scp")
        self.assertEqual(calls[3], ["ssh", "ha.local", "rm -rf /addons/pesetech_ble_mesh"])
        self.assertEqual(calls[4][0:2], ["ssh", "ha.local"])
        self.assertIn("tar -xzf", calls[4][2])
        self.assertEqual(calls[5][0:2], ["ssh", "ha.local"])
        self.assertIn("test -f /addons/pesetech_ble_mesh/config.yaml", calls[5][2])

    def test_addon_install_stops_when_archive_verification_fails(self):
        calls = []

        def run(command, cwd=None, env=None, dry_run=False):
            calls.append(command)
            if any("pesetech_verify_addon_package.py" in part for part in command):
                return 7
            return 0

        original = session.run_command
        try:
            session.run_command = run
            with tempfile.NamedTemporaryFile(suffix=".tar.gz") as archive:
                args = types.SimpleNamespace(
                    addon_archive=archive.name,
                    ha_host="ha.local",
                    ssh_user="root",
                    remote_addons_dir="/addons",
                    slug="pesetech_ble_mesh",
                    replace=False,
                    dry_run=False,
                    skip_verify=False,
                )

                exit_code = session.addon_install(args)
        finally:
            session.run_command = original

        self.assertEqual(exit_code, 7)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0:3], ["python3", "scripts/pesetech_verify_addon_package.py", "--local-app"])
        self.assertEqual(calls[0][3], archive.name)

    def test_addon_install_can_skip_local_archive_verification(self):
        calls = []

        def run(command, cwd=None, env=None, dry_run=False):
            calls.append(command)
            return 0

        original = session.run_command
        try:
            session.run_command = run
            with tempfile.NamedTemporaryFile(suffix=".tar.gz") as archive:
                args = types.SimpleNamespace(
                    addon_archive=archive.name,
                    ha_host="ha.local",
                    ssh_user="root",
                    remote_addons_dir="/addons",
                    slug="pesetech_ble_mesh",
                    replace=False,
                    dry_run=False,
                    skip_verify=True,
                )

                exit_code = session.addon_install(args)
        finally:
            session.run_command = original

        self.assertEqual(exit_code, 0)
        self.assertTrue(calls)
        self.assertEqual(calls[0], ["ssh", "root@ha.local", "mkdir -p /addons"])
        self.assertFalse(any("pesetech_verify_addon_package.py" in part for command in calls for part in command))

    def test_addon_install_errors_when_archive_is_missing(self):
        args = types.SimpleNamespace(
            addon_archive="/tmp/does-not-exist-pesetech.tar.gz",
            ha_host="ha.local",
            ssh_user="root",
            remote_addons_dir="/addons",
            slug="pesetech_ble_mesh",
            replace=False,
            dry_run=False,
            skip_verify=False,
        )
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            exit_code = session.addon_install(args)

        self.assertEqual(exit_code, 1)
        self.assertIn("Add-on archive not found", stderr.getvalue())

    def test_addon_upload_cloud_token_dry_run_copies_token_without_printing_secret(self):
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as token_file:
            token_file.write("secret-token-value\n")
            token_file.flush()
            args = types.SimpleNamespace(
                ha_host="ha.local",
                ssh_user="root",
                remote_share_dir="/share",
                token_file=token_file.name,
                username_file=None,
                password_file=None,
                remote_token_name="pesetech_cloud_token.txt",
                remote_username_name="pesetech_cloud_username.txt",
                remote_password_name="pesetech_cloud_password.txt",
                dry_run=True,
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = session.addon_upload_cloud_credentials(args)

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        self.assertIn("ssh root@ha.local 'mkdir -p /share'", text)
        self.assertIn("scp", text)
        self.assertIn("root@ha.local:/share/pesetech_cloud_token.txt", text)
        self.assertIn("test -s /share/pesetech_cloud_token.txt", text)
        self.assertIn("operation=cloud-fetch", text)
        self.assertNotIn("secret-token-value", text)

    def test_addon_upload_cloud_credentials_copies_username_and_password(self):
        calls = []

        def run(command, cwd=None, env=None, dry_run=False):
            calls.append(command)
            return 0

        original = session.run_command
        try:
            session.run_command = run
            with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as username_file, tempfile.NamedTemporaryFile("w+", encoding="utf-8") as password_file:
                username_file.write("user@example.com\n")
                username_file.flush()
                password_file.write("hidden-password\n")
                password_file.flush()
                args = types.SimpleNamespace(
                    ha_host="ha.local",
                    ssh_user="",
                    remote_share_dir="/share",
                    token_file=None,
                    username_file=username_file.name,
                    password_file=password_file.name,
                    remote_token_name="pesetech_cloud_token.txt",
                    remote_username_name="pesetech_cloud_username.txt",
                    remote_password_name="pesetech_cloud_password.txt",
                    dry_run=False,
                )

                exit_code = session.addon_upload_cloud_credentials(args)
        finally:
            session.run_command = original

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls[0], ["ssh", "ha.local", "mkdir -p /share"])
        self.assertEqual(calls[1][0], "scp")
        self.assertTrue(calls[1][1].endswith(username_file.name.split("/")[-1]))
        self.assertEqual(calls[1][2], "ha.local:/share/pesetech_cloud_username.txt")
        self.assertEqual(calls[2][0], "scp")
        self.assertEqual(calls[2][2], "ha.local:/share/pesetech_cloud_password.txt")
        self.assertEqual(calls[3][0:2], ["ssh", "ha.local"])
        self.assertIn("test -s /share/pesetech_cloud_username.txt", calls[3][2])
        self.assertIn("test -s /share/pesetech_cloud_password.txt", calls[3][2])

    def test_addon_upload_cloud_credentials_requires_complete_secret_source(self):
        args = types.SimpleNamespace(
            ha_host="ha.local",
            ssh_user="root",
            remote_share_dir="/share",
            token_file=None,
            username_file="/tmp/user.txt",
            password_file=None,
            remote_token_name="pesetech_cloud_token.txt",
            remote_username_name="pesetech_cloud_username.txt",
            remote_password_name="pesetech_cloud_password.txt",
            dry_run=True,
        )
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            exit_code = session.addon_upload_cloud_credentials(args)

        self.assertEqual(exit_code, 2)
        self.assertIn("--username-file and --password-file", stderr.getvalue())

    def test_addon_upload_cloud_credentials_errors_when_local_file_missing(self):
        args = types.SimpleNamespace(
            ha_host="ha.local",
            ssh_user="root",
            remote_share_dir="/share",
            token_file="/tmp/does-not-exist-token.txt",
            username_file=None,
            password_file=None,
            remote_token_name="pesetech_cloud_token.txt",
            remote_username_name="pesetech_cloud_username.txt",
            remote_password_name="pesetech_cloud_password.txt",
            dry_run=False,
        )
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            exit_code = session.addon_upload_cloud_credentials(args)

        self.assertEqual(exit_code, 1)
        self.assertIn("Cloud token file not found", stderr.getvalue())

    def test_addon_upload_mesh_dry_run_validates_copies_and_checks_file(self):
        args = types.SimpleNamespace(
            ha_host="ha.local",
            ssh_user="root",
            remote_share_dir="/share",
            mesh_json="/tmp/pesetech_mesh.json",
            remote_name="pesetech_mesh.json",
            skip_validate=False,
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.addon_upload_mesh(args)

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        self.assertIn("python3 scripts/pesetech_extract_mesh_json.py --list /tmp/pesetech_mesh.json", text)
        self.assertIn("ssh root@ha.local 'mkdir -p /share'", text)
        self.assertIn("scp /tmp/pesetech_mesh.json root@ha.local:/share/pesetech_mesh.json", text)
        self.assertIn("test -s /share/pesetech_mesh.json", text)
        self.assertIn("operation=import-check", text)

    def test_addon_upload_mesh_validates_before_copying(self):
        calls = []

        def run(command, cwd=None, env=None, dry_run=False):
            calls.append(command)
            return 0

        original = session.run_command
        try:
            session.run_command = run
            with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".json") as mesh_file:
                mesh_file.write("{}\n")
                mesh_file.flush()
                args = types.SimpleNamespace(
                    ha_host="ha.local",
                    ssh_user="",
                    remote_share_dir="/share",
                    mesh_json=mesh_file.name,
                    remote_name="pesetech_mesh.json",
                    skip_validate=False,
                    dry_run=False,
                )

                exit_code = session.addon_upload_mesh(args)
        finally:
            session.run_command = original

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls[0][0:3], ["python3", "scripts/pesetech_extract_mesh_json.py", "--list"])
        self.assertEqual(calls[0][3], mesh_file.name)
        self.assertEqual(calls[1], ["ssh", "ha.local", "mkdir -p /share"])
        self.assertEqual(calls[2], ["scp", mesh_file.name, "ha.local:/share/pesetech_mesh.json"])
        self.assertEqual(calls[3], ["ssh", "ha.local", "test -s /share/pesetech_mesh.json"])

    def test_addon_upload_mesh_stops_when_validation_fails(self):
        calls = []

        def run(command, cwd=None, env=None, dry_run=False):
            calls.append(command)
            if any("pesetech_extract_mesh_json.py" in part for part in command):
                return 6
            return 0

        original = session.run_command
        try:
            session.run_command = run
            with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".json") as mesh_file:
                mesh_file.write("{}\n")
                mesh_file.flush()
                args = types.SimpleNamespace(
                    ha_host="ha.local",
                    ssh_user="root",
                    remote_share_dir="/share",
                    mesh_json=mesh_file.name,
                    remote_name="pesetech_mesh.json",
                    skip_validate=False,
                    dry_run=False,
                )

                exit_code = session.addon_upload_mesh(args)
        finally:
            session.run_command = original

        self.assertEqual(exit_code, 6)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0:3], ["python3", "scripts/pesetech_extract_mesh_json.py", "--list"])

    def test_addon_upload_mesh_can_skip_validation(self):
        calls = []

        def run(command, cwd=None, env=None, dry_run=False):
            calls.append(command)
            return 0

        original = session.run_command
        try:
            session.run_command = run
            with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".json") as mesh_file:
                mesh_file.write("{}\n")
                mesh_file.flush()
                args = types.SimpleNamespace(
                    ha_host="ha.local",
                    ssh_user="root",
                    remote_share_dir="/share",
                    mesh_json=mesh_file.name,
                    remote_name="pesetech_mesh.json",
                    skip_validate=True,
                    dry_run=False,
                )

                exit_code = session.addon_upload_mesh(args)
        finally:
            session.run_command = original

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls[0], ["ssh", "root@ha.local", "mkdir -p /share"])
        self.assertFalse(any("pesetech_extract_mesh_json.py" in part for command in calls for part in command))

    def test_addon_upload_mesh_errors_when_file_is_missing(self):
        args = types.SimpleNamespace(
            ha_host="ha.local",
            ssh_user="root",
            remote_share_dir="/share",
            mesh_json="/tmp/does-not-exist-pesetech-mesh.json",
            remote_name="pesetech_mesh.json",
            skip_validate=False,
            dry_run=False,
        )
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            exit_code = session.addon_upload_mesh(args)

        self.assertEqual(exit_code, 1)
        self.assertIn("Mesh JSON file not found", stderr.getvalue())

    def test_addon_set_operation_dry_run_uploads_override_and_can_restart(self):
        args = types.SimpleNamespace(
            ha_host="ha.local",
            ssh_user="root",
            remote_share_dir="/share",
            remote_name="pesetech_next_operation.json",
            slug="pesetech_ble_mesh",
            operation="import-check",
            cloud_home_id="home-1",
            import_mesh_candidate=2,
            ha_entity_id="light.kitchen_sky",
            import_force=False,
            relay=False,
            run="restart",
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.addon_set_operation(args)

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        self.assertIn('"operation": "import-check"', text)
        self.assertIn('"cloud_home_id": "home-1"', text)
        self.assertIn('"ha_entity_id": "light.kitchen_sky"', text)
        self.assertIn("scp", text)
        self.assertIn("root@ha.local:/share/pesetech_next_operation.json", text)
        self.assertIn("test -s /share/pesetech_next_operation.json", text)
        self.assertIn("ha apps restart pesetech_ble_mesh", text)
        self.assertIn("ha addons restart pesetech_ble_mesh", text)

    def test_addon_set_operation_writes_non_secret_override_file(self):
        calls = []
        copied_payloads = []

        def run(command, cwd=None, env=None, dry_run=False):
            calls.append(command)
            if command and command[0] == "scp":
                copied_payloads.append(Path(command[1]).read_text(encoding="utf-8"))
            return 0

        original = session.run_command
        try:
            session.run_command = run
            args = types.SimpleNamespace(
                ha_host="ha.local",
                ssh_user="",
                remote_share_dir="/share",
                remote_name="pesetech_next_operation.json",
                slug="pesetech_ble_mesh",
                operation="move-test",
                ha_entity_id="light.skylight",
                import_force=False,
                relay=False,
                run="none",
                dry_run=False,
            )

            exit_code = session.addon_set_operation(args)
        finally:
            session.run_command = original

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls[0], ["ssh", "ha.local", "mkdir -p /share"])
        self.assertEqual(calls[1][0], "scp")
        self.assertEqual(calls[1][2], "ha.local:/share/pesetech_next_operation.json")
        self.assertEqual(calls[2], ["ssh", "ha.local", "test -s /share/pesetech_next_operation.json"])
        self.assertEqual(len(calls), 3)
        payload = copied_payloads[0]
        self.assertIn('"operation": "move-test"', payload)
        self.assertIn('"ha_entity_id": "light.skylight"', payload)
        self.assertNotIn("password", payload.lower())

    def test_addon_fetch_cloud_report_dry_run_copies_and_summarizes_report(self):
        args = types.SimpleNamespace(
            ha_host="ha.local",
            ssh_user="root",
            remote_report="/share/pesetech_cloud_fetch_report.json",
            output_dir="/tmp/pesetech-cloud-reports",
            no_summary=False,
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.addon_fetch_cloud_report(args)

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        self.assertIn("ssh root@ha.local 'test -s /share/pesetech_cloud_fetch_report.json'", text)
        self.assertIn("scp root@ha.local:/share/pesetech_cloud_fetch_report.json /tmp/pesetech-cloud-reports/pesetech_cloud_fetch_report.json", text)
        self.assertIn("scripts/pesetech_cloud_report_summary.py /tmp/pesetech-cloud-reports/pesetech_cloud_fetch_report.json", text)

    def test_addon_fetch_cloud_report_checks_copies_then_summarizes(self):
        calls = []

        def run(command, cwd=None, env=None, dry_run=False):
            calls.append(command)
            return 0

        original = session.run_command
        try:
            session.run_command = run
            with tempfile.TemporaryDirectory() as temp_dir:
                args = types.SimpleNamespace(
                    ha_host="ha.local",
                    ssh_user="",
                    remote_report="/share/pesetech_cloud_fetch_report.json",
                    output_dir=temp_dir,
                    no_summary=False,
                    dry_run=False,
                )

                exit_code = session.addon_fetch_cloud_report(args)
        finally:
            session.run_command = original

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls[0], ["ssh", "ha.local", "test -s /share/pesetech_cloud_fetch_report.json"])
        self.assertEqual(calls[1][0], "scp")
        self.assertEqual(calls[1][1], "ha.local:/share/pesetech_cloud_fetch_report.json")
        self.assertTrue(calls[1][2].endswith("pesetech_cloud_fetch_report.json"))
        self.assertEqual(calls[2][0:2], ["python3", "scripts/pesetech_cloud_report_summary.py"])
        self.assertTrue(calls[2][2].endswith("pesetech_cloud_fetch_report.json"))

    def test_addon_fetch_status_dry_run_copies_and_summarizes_report(self):
        args = types.SimpleNamespace(
            ha_host="ha.local",
            ssh_user="root",
            remote_report="/share/pesetech-status.json",
            output_dir="/tmp/pesetech-status-reports",
            no_summary=False,
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.addon_fetch_status_report(args)

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        self.assertIn("ssh root@ha.local 'test -s /share/pesetech-status.json'", text)
        self.assertIn("scp root@ha.local:/share/pesetech-status.json /tmp/pesetech-status-reports/pesetech-status.json", text)
        self.assertIn("scripts/pesetech_status_report_summary.py /tmp/pesetech-status-reports/pesetech-status.json", text)

    def test_addon_fetch_status_checks_copies_then_summarizes(self):
        calls = []

        def run(command, cwd=None, env=None, dry_run=False):
            calls.append(command)
            return 0

        original = session.run_command
        try:
            session.run_command = run
            with tempfile.TemporaryDirectory() as temp_dir:
                args = types.SimpleNamespace(
                    ha_host="ha.local",
                    ssh_user="",
                    remote_report="/share/pesetech-status.json",
                    output_dir=temp_dir,
                    no_summary=False,
                    dry_run=False,
                )

                exit_code = session.addon_fetch_status_report(args)
        finally:
            session.run_command = original

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls[0], ["ssh", "ha.local", "test -s /share/pesetech-status.json"])
        self.assertEqual(calls[1][0], "scp")
        self.assertEqual(calls[1][1], "ha.local:/share/pesetech-status.json")
        self.assertTrue(calls[1][2].endswith("pesetech-status.json"))
        self.assertEqual(calls[2][0:2], ["python3", "scripts/pesetech_status_report_summary.py"])
        self.assertTrue(calls[2][2].endswith("pesetech-status.json"))

    def test_addon_fetch_diagnostics_dry_run_copies_latest_bundle_and_reviews_it(self):
        args = types.SimpleNamespace(
            ha_host="ha.local",
            ssh_user="root",
            remote_glob="/share/pesetech-diagnostics-*.tar.gz",
            output_dir="/tmp/pesetech-diagnostics",
            no_review=False,
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.addon_fetch_diagnostics(args)

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        self.assertIn("ssh root@ha.local 'ls -t /share/pesetech-diagnostics-*.tar.gz", text)
        self.assertIn("scp root@ha.local:/share/pesetech-diagnostics-latest.tar.gz /tmp/pesetech-diagnostics/pesetech-diagnostics-latest.tar.gz", text)
        self.assertIn("scripts/pesetech_review_diagnostics.py /tmp/pesetech-diagnostics/pesetech-diagnostics-latest.tar.gz", text)

    def test_addon_fetch_diagnostics_copies_latest_bundle_before_review(self):
        calls = []

        def capture(command, cwd=None, env=None, dry_run=False, placeholder=""):
            calls.append(("capture", command))
            return 0, "/share/pesetech-diagnostics-20260628-120000.tar.gz\n"

        def run(command, cwd=None, env=None, dry_run=False):
            calls.append(("run", command))
            return 0

        original = (session.run_capture_command, session.run_command)
        try:
            session.run_capture_command = capture
            session.run_command = run
            with tempfile.TemporaryDirectory() as temp_dir:
                args = types.SimpleNamespace(
                    ha_host="ha.local",
                    ssh_user="root",
                    remote_glob="/share/pesetech-diagnostics-*.tar.gz",
                    output_dir=temp_dir,
                    no_review=False,
                    dry_run=False,
                )

                exit_code = session.addon_fetch_diagnostics(args)
        finally:
            session.run_capture_command, session.run_command = original

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls[0], ("capture", ["ssh", "root@ha.local", "ls -t /share/pesetech-diagnostics-*.tar.gz 2>/dev/null | head -n 1"]))
        self.assertEqual(calls[1][0], "run")
        self.assertEqual(calls[1][1][0], "scp")
        self.assertEqual(calls[1][1][1], "root@ha.local:/share/pesetech-diagnostics-20260628-120000.tar.gz")
        self.assertTrue(calls[1][1][2].endswith("pesetech-diagnostics-20260628-120000.tar.gz"))
        self.assertEqual(calls[2][0], "run")
        self.assertEqual(calls[2][1][0:2], ["python3", "scripts/pesetech_review_diagnostics.py"])
        self.assertTrue(calls[2][1][2].endswith("pesetech-diagnostics-20260628-120000.tar.gz"))

    def test_prove_final_audit_requires_home_assistant_service_proof(self):
        args = types.SimpleNamespace(
            ha_service=False,
            final_audit=True,
        )
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            exit_code = session.prove(args)

        self.assertEqual(exit_code, 2)
        self.assertIn("--final-audit requires --ha-service", stderr.getvalue())

    def test_prove_ha_service_defaults_to_strict_state_and_mqtt_attribute_checks(self):
        args = types.SimpleNamespace(
            config="docker/config/config.yaml",
            compose_dir="docker",
            broker="mqtt.local",
            port=1884,
            username="hidden-user",
            password="mqtt-secret",
            discovery_prefix="ha",
            mesh_topic="mesh_bridge",
            device_id="kitchen_sky",
            dump_json=False,
            proof_log="docker/config/pesetech-proof.jsonl",
            store="docker/config/store.yaml",
            host=True,
            precondition_visible_start=True,
            ha_service=True,
            ha_url="http://homeassistant.local:8123",
            ha_entity_id="light.skylight",
            ha_token_file=None,
            ha_proof_log="docker/config/pesetech-ha-service-proof.jsonl",
            ha_precondition_visible_start=True,
            ha_wait_attributes=False,
            ha_wait_mqtt_state=False,
            ha_wait_mqtt_attributes=False,
            ha_no_wait_state=False,
            ha_no_observe=False,
            ha_candidate_search="skylight",
            ha_allow_missing_state=False,
            ha_allow_service_error=False,
            ha_allow_unobserved=False,
            ha_relaxed_state_proof=False,
            no_diagnostics=False,
            diagnostics_on_success=False,
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.prove(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("== ha-service ==", command)
        self.assertGreaterEqual(command.count("--precondition-visible-start"), 2)
        self.assertIn("--wait-state --wait-attributes --observe", command)
        self.assertIn("--wait-mqtt-state --mqtt-config docker/config/config.yaml", command)
        self.assertIn("--mqtt-broker mqtt.local", command)
        self.assertIn("--mqtt-port 1884", command)
        self.assertIn("--mqtt-username '<redacted>'", command)
        self.assertIn("--mqtt-password '<redacted>'", command)
        self.assertNotIn("hidden-user", command)
        self.assertNotIn("mqtt-secret", command)
        self.assertIn("--mqtt-discovery-prefix ha", command)
        self.assertIn("--mqtt-mesh-topic mesh_bridge", command)
        self.assertIn("--mqtt-device-id kitchen_sky", command)
        self.assertIn("--wait-mqtt-attributes", command)
        self.assertIn("== ha-verify ==", command)
        self.assertIn("--require-attributes", command)
        self.assertIn("--require-mqtt-state", command)
        self.assertIn("--require-mqtt-attributes", command)

    def test_prove_ha_service_can_relax_strict_attribute_defaults(self):
        args = types.SimpleNamespace(
            config="docker/config/config.yaml",
            compose_dir="docker",
            broker=None,
            username=None,
            password=None,
            discovery_prefix=None,
            mesh_topic=None,
            device_id=None,
            dump_json=False,
            proof_log="docker/config/pesetech-proof.jsonl",
            store="docker/config/store.yaml",
            host=True,
            ha_service=True,
            ha_url="http://homeassistant.local:8123",
            ha_entity_id="light.skylight",
            ha_token_file=None,
            ha_proof_log="docker/config/pesetech-ha-service-proof.jsonl",
            ha_wait_attributes=False,
            ha_wait_mqtt_state=False,
            ha_wait_mqtt_attributes=False,
            ha_no_wait_state=False,
            ha_no_observe=False,
            ha_candidate_search="skylight",
            ha_allow_missing_state=False,
            ha_allow_service_error=False,
            ha_allow_unobserved=False,
            ha_relaxed_state_proof=True,
            no_diagnostics=False,
            diagnostics_on_success=False,
            dry_run=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = session.prove(args)

        self.assertEqual(exit_code, 0)
        command = output.getvalue()
        self.assertIn("== ha-service ==", command)
        self.assertIn("--wait-state --observe", command)
        self.assertNotIn("--wait-attributes", command)
        self.assertNotIn("--wait-mqtt-state", command)
        self.assertNotIn("--require-attributes", command)
        self.assertNotIn("--require-mqtt-state", command)

    def test_prove_can_collect_diagnostics_on_success(self):
        calls = []

        def ok(name):
            def handler(args):
                calls.append(name)
                return 0
            return handler

        original = (
            session.preflight,
            session.runtime_check,
            session.discovery,
            session.smoke,
            session.verify,
            session.ha_service,
            session.ha_verify,
            session.diagnostics,
        )
        try:
            session.preflight = ok("preflight")
            session.runtime_check = ok("runtime-check")
            session.discovery = ok("discovery")
            session.smoke = ok("smoke")
            session.verify = ok("verify")
            session.ha_service = ok("ha-service")
            session.ha_verify = ok("ha-verify")
            session.diagnostics = ok("diagnostics")
            args = types.SimpleNamespace(no_diagnostics=False, diagnostics_on_success=True)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = session.prove(args)
        finally:
            (
                session.preflight,
                session.runtime_check,
                session.discovery,
                session.smoke,
                session.verify,
                session.ha_service,
                session.ha_verify,
                session.diagnostics,
            ) = original

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, ["preflight", "runtime-check", "discovery", "smoke", "verify", "diagnostics"])
        self.assertIn("== diagnostics ==", output.getvalue())


if __name__ == "__main__":
    unittest.main()
