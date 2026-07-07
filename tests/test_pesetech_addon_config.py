import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "pesetech_addon_config.py"
)
spec = importlib.util.spec_from_file_location("pesetech_addon_config", SCRIPT_PATH)
addon_config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(addon_config)


class PesetechAddonConfigTest(unittest.TestCase):
    def test_defaults_start_with_read_only_runtime_check(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_options = Path(temp_dir) / "options.json"

            self.assertEqual(addon_config.DEFAULTS["operation"], "runtime-check")
            self.assertTrue(addon_config.DEFAULTS["mqtt_from_supervisor"])
            self.assertEqual(addon_config.load_options(missing_options)["operation"], "runtime-check")
            self.assertIn("PESETECH_OPERATION=runtime-check", addon_config.shell_exports(addon_config.DEFAULTS))
            self.assertIn("PESETECH_MQTT_SOURCE=none", addon_config.shell_exports(addon_config.DEFAULTS))

    def test_operation_override_applies_only_safe_fields(self):
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as override_file:
            json.dump(
                {
                    "operation": "import-check",
                    "cloud_home_id": "home-1",
                    "ha_entity_id": "light.kitchen_sky",
                    "import_mesh_candidate": 2,
                },
                override_file,
            )
            override_file.flush()
            options = addon_config.DEFAULTS.copy()

            resolved = addon_config.apply_operation_override(options, override_file.name)

        self.assertEqual(resolved["operation"], "import-check")
        self.assertEqual(resolved["cloud_home_id"], "home-1")
        self.assertEqual(resolved["ha_entity_id"], "light.kitchen_sky")
        self.assertEqual(resolved["import_mesh_candidate"], 2)
        self.assertEqual(options["operation"], "runtime-check")

    def test_missing_operation_override_keeps_options(self):
        options = addon_config.DEFAULTS.copy()

        self.assertEqual(
            addon_config.apply_operation_override(options, "/tmp/does-not-exist-pesetech-next-operation.json"),
            options,
        )

    def test_operation_override_rejects_secret_or_connection_fields(self):
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as override_file:
            json.dump({"operation": "move-test", "mqtt_password": "secret"}, override_file)
            override_file.flush()

            with self.assertRaises(ValueError) as error:
                addon_config.apply_operation_override(addon_config.DEFAULTS.copy(), override_file.name)

        self.assertIn("may not contain secret or connection keys", str(error.exception))

    def test_gateway_config_uses_pesetech_profile_and_normalized_uuid(self):
        options = {
            "operation": "service",
            "mqtt_broker": "homeassistant.local",
            "mqtt_port": 1884,
            "mqtt_username": "mqtt",
            "mqtt_password": "secret",
            "discovery_prefix": "ha",
            "node_id": "pesetech_mesh",
            "device_id": "kitchen_sky",
            "skylight_name": "Kitchen Sky",
            "skylight_uuid": "00112233445566778899aabbccddeeff",
            "relay": False,
        }

        config = addon_config.gateway_config(options)

        self.assertEqual(
            config["mqtt"],
            {
                "broker": "homeassistant.local",
                "port": 1884,
                "discovery_prefix": "ha",
                "node_id": "pesetech_mesh",
                "username": "mqtt",
                "password": "secret",
            },
        )
        self.assertEqual(
            config["mesh"],
            {
                "kitchen_sky": {
                    "uuid": "00112233-4455-6677-8899-aabbccddeeff",
                    "name": "Kitchen Sky",
                    "default_entity_id": "light.skylight",
                    "type": "pesetech_skylight",
                    "relay": False,
                }
            },
        )
        self.assertFalse(config["skylight_programs_enabled"])
        self.assertFalse(config["diagnostic_monitor"]["enabled"])
        self.assertFalse(config["diagnostic_export"]["enabled"])
        self.assertFalse(config["btmon_monitor"]["enabled"])

    def test_dump_simple_yaml_preserves_scalar_types(self):
        rendered = addon_config.dump_simple_yaml(
            {
                "mqtt": {
                    "broker": "homeassistant.local",
                    "port": 1884,
                },
                "mesh": {
                    "skylight": {
                        "relay": False,
                    }
                },
            }
        )

        self.assertIn('broker: "homeassistant.local"', rendered)
        self.assertIn("port: 1884", rendered)
        self.assertIn("relay: false", rendered)

    def test_shell_exports_include_mqtt_topic_context(self):
        options = addon_config.DEFAULTS.copy()
        options.update(
            {
                "mqtt_broker": "homeassistant.local",
                "mqtt_port": 1884,
                "discovery_prefix": "ha",
                "node_id": "pesetech_mesh",
            }
        )

        exports = addon_config.shell_exports(options)

        self.assertIn("PESETECH_MQTT_BROKER=homeassistant.local", exports)
        self.assertIn("PESETECH_MQTT_PORT=1884", exports)
        self.assertIn("PESETECH_DISCOVERY_PREFIX=ha", exports)
        self.assertIn("PESETECH_NODE_ID=pesetech_mesh", exports)

    def test_scan_and_service_do_not_require_uuid_but_provision_does(self):
        scan = addon_config.DEFAULTS.copy()
        scan.update({"operation": "scan", "mqtt_broker": ""})
        service = addon_config.DEFAULTS.copy()
        service.update({"operation": "service", "mqtt_broker": "", "mqtt_port": 0, "node_id": "", "discovery_prefix": ""})
        provision = addon_config.DEFAULTS.copy()
        provision.update({"operation": "provision", "mqtt_broker": "homeassistant.local"})

        self.assertEqual(addon_config.validate_options(scan), [])
        self.assertEqual(addon_config.validate_options(service), [])
        self.assertIn("skylight_uuid is required for provision.", addon_config.validate_options(provision))

    def test_service_with_uuid_validates_rendered_config_options(self):
        options = addon_config.DEFAULTS.copy()
        options.update(
            {
                "operation": "service",
                "mqtt_from_supervisor": False,
                "mqtt_broker": "",
                "mqtt_port": 0,
                "skylight_uuid": "not-a-uuid",
            }
        )

        errors = addon_config.validate_options(options)

        self.assertIn("mqtt_broker must be set to your Home Assistant MQTT broker host.", errors)
        self.assertIn("mqtt_port must be an integer between 1 and 65535.", errors)
        self.assertIn("skylight_uuid must be a valid UUID from the scan result.", errors)

    def test_supervisor_mqtt_service_fills_blank_mqtt_options(self):
        options = addon_config.DEFAULTS.copy()
        options.update({"operation": "scan", "mqtt_broker": ""})
        service = {
            "host": "172.30.33.0",
            "port": "1883",
            "ssl": False,
            "username": "addon",
            "password": "secret",
        }

        resolved = addon_config.apply_supervisor_mqtt_service(options, service)

        self.assertTrue(addon_config.should_resolve_supervisor_mqtt(options))
        self.assertEqual(resolved["mqtt_broker"], "172.30.33.0")
        self.assertEqual(resolved["mqtt_port"], 1883)
        self.assertEqual(resolved["mqtt_username"], "addon")
        self.assertEqual(resolved["mqtt_password"], "secret")
        self.assertEqual(addon_config.mqtt_config_source(resolved), "supervisor")
        self.assertEqual(addon_config.validate_options(resolved), [])

    def test_manual_mqtt_broker_skips_supervisor_resolution(self):
        options = addon_config.DEFAULTS.copy()
        options.update({"operation": "scan", "mqtt_broker": "mqtt.local"})

        self.assertFalse(addon_config.should_resolve_supervisor_mqtt(options))
        self.assertEqual(addon_config.mqtt_config_source(options), "manual")

    def test_supervisor_mqtt_tls_service_is_rejected(self):
        options = addon_config.DEFAULTS.copy()
        options.update({"operation": "scan"})

        with self.assertRaises(ValueError) as error:
            addon_config.apply_supervisor_mqtt_service(options, {"host": "mqtt", "port": "8883", "ssl": True})

        self.assertIn("supports plain MQTT only", str(error.exception))

    def test_service_with_uuid_validates_light_entity_domain(self):
        options = addon_config.DEFAULTS.copy()
        options.update(
            {
                "operation": "service",
                "mqtt_broker": "homeassistant.local",
                "skylight_uuid": "00112233-4455-6677-8899-aabbccddeeff",
                "ha_entity_id": "switch.skylight",
            }
        )

        self.assertIn(
            "ha_entity_id must be a Home Assistant light entity id, for example light.skylight.",
            addon_config.validate_options(options),
        )

    def test_import_requires_mesh_json_but_not_skylight_uuid(self):
        options = addon_config.DEFAULTS.copy()
        options.update(
            {
                "operation": "import",
                "mqtt_broker": "homeassistant.local",
                "skylight_uuid": "",
                "mesh_json_path": "/share/pesetech_mesh.json",
                "import_node_uuid": "",
            }
        )

        self.assertEqual(addon_config.validate_options(options), [])

        options["mesh_json_path"] = ""
        self.assertIn("mesh_json_path is required for import.", addon_config.validate_options(options))

    def test_import_shell_exports_include_import_options(self):
        options = addon_config.DEFAULTS.copy()
        options.update(
            {
                "operation": "import",
                "device_id": "sky",
                "skylight_name": "Imported Sky",
                "mesh_json_path": "/share/mesh.json",
                "import_mesh_candidate": 2,
                "import_node_uuid": "00112233445566778899aabbccddeeff",
                "import_node_unicast": "0002",
                "import_local_address": "0005",
                "import_force": True,
            }
        )

        exports = addon_config.shell_exports(options)

        self.assertIn("PESETECH_OPERATION=import", exports)
        self.assertIn("PESETECH_MQTT_BROKER=''", exports)
        self.assertIn("PESETECH_MQTT_PORT=1883", exports)
        self.assertIn("PESETECH_DISCOVERY_PREFIX=homeassistant", exports)
        self.assertIn("PESETECH_NODE_ID=mqtt_mesh", exports)
        self.assertIn("PESETECH_DEVICE_ID=sky", exports)
        self.assertIn("PESETECH_DEVICE_NAME='Imported Sky'", exports)
        self.assertIn("PESETECH_MESH_JSON=/share/mesh.json", exports)
        self.assertIn("PESETECH_IMPORT_MESH_CANDIDATE=2", exports)
        self.assertIn("PESETECH_IMPORT_NODE_UUID=00112233-4455-6677-8899-aabbccddeeff", exports)
        self.assertIn("PESETECH_IMPORT_NODE_UNICAST=0002", exports)
        self.assertIn("PESETECH_IMPORT_LOCAL_ADDRESS=0005", exports)
        self.assertIn("PESETECH_IMPORT_FORCE=true", exports)

    def test_import_check_uses_import_options_without_requiring_mqtt(self):
        options = addon_config.DEFAULTS.copy()
        options.update(
            {
                "operation": "import-check",
                "mqtt_broker": "",
                "mqtt_port": 0,
                "mesh_json_path": "/share/mesh.json",
                "import_mesh_candidate": 1,
                "import_node_uuid": "00112233445566778899aabbccddeeff",
                "import_node_unicast": "0002",
                "import_local_address": "0005",
                "skylight_uuid": "not-a-uuid",
            }
        )

        self.assertEqual(addon_config.validate_options(options), [])
        self.assertFalse(addon_config.should_write_gateway_config(options))
        exports = addon_config.shell_exports(options)
        self.assertIn("PESETECH_OPERATION=import-check", exports)
        self.assertIn("PESETECH_IMPORT_MESH_CANDIDATE=1", exports)
        self.assertIn("PESETECH_IMPORT_NODE_UUID=00112233-4455-6677-8899-aabbccddeeff", exports)
        self.assertIn("PESETECH_IMPORT_NODE_UNICAST=0002", exports)
        self.assertIn("PESETECH_IMPORT_LOCAL_ADDRESS=0005", exports)

    def test_cloud_fetch_uses_cloud_file_options_without_requiring_mqtt(self):
        options = addon_config.DEFAULTS.copy()
        options.update(
            {
                "operation": "cloud-fetch",
                "mqtt_broker": "",
                "mqtt_port": 0,
                "device_id": "",
                "node_id": "",
                "discovery_prefix": "",
                "skylight_uuid": "not-a-uuid",
                "cloud_region": "asia",
                "cloud_base_url": "https://capture.local",
                "cloud_token_path": "/share/token.txt",
                "cloud_username_path": "/share/user.txt",
                "cloud_password_path": "/share/password.txt",
                "cloud_output_path": "/share/mesh.json",
                "cloud_raw_output_path": "/share/raw.json",
                "cloud_report_path": "/share/report.json",
                "cloud_candidate": 2,
                "cloud_home_id": "home-1",
            }
        )

        self.assertEqual(addon_config.validate_options(options), [])
        self.assertFalse(addon_config.should_write_gateway_config(options))
        exports = addon_config.shell_exports(options)
        self.assertIn("PESETECH_OPERATION=cloud-fetch", exports)
        self.assertIn("PESETECH_CLOUD_REGION=asia", exports)
        self.assertIn("PESETECH_CLOUD_BASE_URL=https://capture.local", exports)
        self.assertIn("PESETECH_CLOUD_TOKEN_FILE=/share/token.txt", exports)
        self.assertIn("PESETECH_CLOUD_USERNAME_FILE=/share/user.txt", exports)
        self.assertIn("PESETECH_CLOUD_PASSWORD_FILE=/share/password.txt", exports)
        self.assertIn("PESETECH_CLOUD_OUTPUT=/share/mesh.json", exports)
        self.assertIn("PESETECH_CLOUD_RAW_OUTPUT=/share/raw.json", exports)
        self.assertIn("PESETECH_CLOUD_REPORT=/share/report.json", exports)
        self.assertIn("PESETECH_CLOUD_CANDIDATE=2", exports)
        self.assertIn("PESETECH_CLOUD_HOME_ID=home-1", exports)

    def test_cloud_fetch_exports_direct_cloud_credentials(self):
        options = addon_config.DEFAULTS.copy()
        options.update(
            {
                "operation": "cloud-fetch",
                "cloud_token": "token-1",
                "cloud_username": "user@example.com",
                "cloud_password": "secret",
            }
        )

        self.assertEqual(addon_config.validate_options(options), [])
        exports = addon_config.shell_exports(options)

        self.assertIn("PESETECH_CLOUD_TOKEN=token-1", exports)
        self.assertIn("PESETECH_CLOUD_USERNAME=user@example.com", exports)
        self.assertIn("PESETECH_CLOUD_PASSWORD=secret", exports)

    def test_cloud_fetch_validates_region_candidate_and_output(self):
        options = addon_config.DEFAULTS.copy()
        options.update(
            {
                "operation": "cloud-fetch",
                "cloud_region": "mars",
                "cloud_output_path": "",
                "cloud_username": "user@example.com",
                "cloud_password": "",
                "cloud_candidate": -1,
                "cloud_home_id": "home-1\nhome-2",
            }
        )

        errors = addon_config.validate_options(options)

        self.assertIn("cloud_region must be one of asia, europe.", errors)
        self.assertIn("cloud_output_path is required for cloud-fetch.", errors)
        self.assertIn("cloud_username and cloud_password must be set together.", errors)
        self.assertIn("cloud_candidate must be 0 or a positive integer.", errors)
        self.assertIn("cloud_home_id must be blank or a single Pesetech homeId value.", errors)

    def test_import_check_validates_light_entity_domain(self):
        options = addon_config.DEFAULTS.copy()
        options.update({"operation": "import-check", "ha_entity_id": "switch.skylight"})

        self.assertIn(
            "ha_entity_id must be a Home Assistant light entity id, for example light.skylight.",
            addon_config.validate_options(options),
        )

    def test_import_check_validates_mesh_candidate(self):
        options = addon_config.DEFAULTS.copy()
        options.update({"operation": "import-check", "import_mesh_candidate": -1})

        self.assertIn(
            "import_mesh_candidate must be 0 or a positive integer.",
            addon_config.validate_options(options),
        )

    def test_runtime_check_does_not_require_config_options(self):
        options = addon_config.DEFAULTS.copy()
        options.update(
            {
                "operation": "runtime-check",
                "mqtt_broker": "",
                "mqtt_port": 0,
                "device_id": "",
                "node_id": "",
                "discovery_prefix": "",
                "skylight_uuid": "not-a-uuid",
                "import_node_uuid": "not-a-uuid",
            }
        )

        self.assertEqual(addon_config.validate_options(options), [])
        exports = addon_config.shell_exports(options)
        self.assertIn("PESETECH_OPERATION=runtime-check", exports)
        self.assertIn("PESETECH_UUID=''", exports)
        self.assertIn("PESETECH_IMPORT_NODE_UUID=''", exports)

    def test_mesh_daemon_check_does_not_require_config_or_mqtt_options(self):
        options = addon_config.DEFAULTS.copy()
        options.update(
            {
                "operation": "mesh-daemon-check",
                "mqtt_broker": "",
                "mqtt_port": 0,
                "device_id": "",
                "node_id": "",
                "discovery_prefix": "",
                "skylight_uuid": "not-a-uuid",
                "import_node_uuid": "not-a-uuid",
            }
        )

        self.assertEqual(addon_config.validate_options(options), [])
        self.assertFalse(addon_config.should_write_gateway_config(options))
        self.assertFalse(addon_config.should_resolve_supervisor_mqtt(options))
        exports = addon_config.shell_exports(options)
        self.assertIn("PESETECH_OPERATION=mesh-daemon-check", exports)
        self.assertIn("PESETECH_MQTT_SOURCE=none", exports)

    def test_status_does_not_require_config_or_mqtt_options(self):
        options = addon_config.DEFAULTS.copy()
        options.update(
            {
                "operation": "status",
                "mqtt_broker": "",
                "mqtt_port": 0,
                "device_id": "",
                "node_id": "",
                "discovery_prefix": "",
                "skylight_uuid": "not-a-uuid",
                "import_node_uuid": "not-a-uuid",
            }
        )

        self.assertEqual(addon_config.validate_options(options), [])
        self.assertFalse(addon_config.should_write_gateway_config(options))
        self.assertFalse(addon_config.should_resolve_supervisor_mqtt(options))
        exports = addon_config.shell_exports(options)
        self.assertIn("PESETECH_OPERATION=status", exports)
        self.assertIn("PESETECH_MQTT_SOURCE=none", exports)

    def test_move_test_uses_existing_config_without_requiring_options(self):
        options = addon_config.DEFAULTS.copy()
        options.update(
            {
                "operation": "move-test",
                "mqtt_broker": "",
                "mqtt_port": 0,
                "device_id": "",
                "node_id": "",
                "discovery_prefix": "",
                "skylight_uuid": "not-a-uuid",
            }
        )

        self.assertEqual(addon_config.validate_options(options), [])
        self.assertFalse(addon_config.should_write_gateway_config(options))
        self.assertIn("PESETECH_OPERATION=move-test", addon_config.shell_exports(options))

    def test_list_uses_existing_config_without_requiring_options(self):
        options = addon_config.DEFAULTS.copy()
        options.update(
            {
                "operation": "list",
                "mqtt_broker": "",
                "mqtt_port": 0,
                "device_id": "",
                "node_id": "",
                "discovery_prefix": "",
                "skylight_uuid": "not-a-uuid",
            }
        )

        self.assertEqual(addon_config.validate_options(options), [])
        self.assertFalse(addon_config.should_write_gateway_config(options))
        self.assertIn("PESETECH_OPERATION=list", addon_config.shell_exports(options))

    def test_ha_service_test_uses_existing_config_and_exports_home_assistant_defaults(self):
        options = addon_config.DEFAULTS.copy()
        options.update(
            {
                "operation": "ha-service-test",
                "mqtt_broker": "",
                "mqtt_port": 0,
                "device_id": "",
                "node_id": "",
                "discovery_prefix": "",
                "skylight_uuid": "not-a-uuid",
            }
        )

        self.assertEqual(addon_config.validate_options(options), [])
        self.assertFalse(addon_config.should_write_gateway_config(options))
        exports = addon_config.shell_exports(options)
        self.assertIn("PESETECH_OPERATION=ha-service-test", exports)
        self.assertIn("PESETECH_HA_URL=http://supervisor/core", exports)
        self.assertIn("PESETECH_HA_ENTITY_ID=light.skylight", exports)

    def test_readiness_test_uses_existing_config_and_exports_home_assistant_defaults(self):
        options = addon_config.DEFAULTS.copy()
        options.update(
            {
                "operation": "readiness-test",
                "mqtt_broker": "",
                "mqtt_port": 0,
                "device_id": "",
                "node_id": "",
                "discovery_prefix": "",
                "skylight_uuid": "not-a-uuid",
            }
        )

        self.assertEqual(addon_config.validate_options(options), [])
        self.assertFalse(addon_config.should_write_gateway_config(options))
        exports = addon_config.shell_exports(options)
        self.assertIn("PESETECH_OPERATION=readiness-test", exports)
        self.assertIn("PESETECH_HA_URL=http://supervisor/core", exports)
        self.assertIn("PESETECH_HA_ENTITY_ID=light.skylight", exports)

    def test_proof_test_uses_existing_config_and_exports_home_assistant_defaults(self):
        options = addon_config.DEFAULTS.copy()
        options.update(
            {
                "operation": "proof-test",
                "mqtt_broker": "",
                "mqtt_port": 0,
                "device_id": "",
                "node_id": "",
                "discovery_prefix": "",
                "skylight_uuid": "not-a-uuid",
            }
        )

        self.assertEqual(addon_config.validate_options(options), [])
        self.assertFalse(addon_config.should_write_gateway_config(options))
        exports = addon_config.shell_exports(options)
        self.assertIn("PESETECH_OPERATION=proof-test", exports)
        self.assertIn("PESETECH_HA_URL=http://supervisor/core", exports)
        self.assertIn("PESETECH_HA_ENTITY_ID=light.skylight", exports)

    def test_ha_api_check_uses_home_assistant_api_without_requiring_mesh_options(self):
        options = addon_config.DEFAULTS.copy()
        options.update(
            {
                "operation": "ha-api-check",
                "mqtt_broker": "",
                "mqtt_port": 0,
                "device_id": "",
                "node_id": "",
                "discovery_prefix": "",
                "skylight_uuid": "not-a-uuid",
            }
        )

        self.assertEqual(addon_config.validate_options(options), [])
        self.assertFalse(addon_config.should_write_gateway_config(options))
        exports = addon_config.shell_exports(options)
        self.assertIn("PESETECH_OPERATION=ha-api-check", exports)
        self.assertIn("PESETECH_HA_URL=http://supervisor/core", exports)
        self.assertIn("PESETECH_HA_ENTITY_ID=light.skylight", exports)

    def test_ha_service_test_requires_home_assistant_target(self):
        options = addon_config.DEFAULTS.copy()
        options.update({"operation": "ha-service-test", "ha_url": "", "ha_entity_id": ""})

        errors = addon_config.validate_options(options)

        self.assertIn("ha_url must be set for ha-service-test.", errors)
        self.assertIn("ha_entity_id must be set for ha-service-test.", errors)

    def test_readiness_test_requires_home_assistant_target(self):
        options = addon_config.DEFAULTS.copy()
        options.update({"operation": "readiness-test", "ha_url": "", "ha_entity_id": ""})

        errors = addon_config.validate_options(options)

        self.assertIn("ha_url must be set for readiness-test.", errors)
        self.assertIn("ha_entity_id must be set for readiness-test.", errors)

    def test_proof_test_requires_home_assistant_target(self):
        options = addon_config.DEFAULTS.copy()
        options.update({"operation": "proof-test", "ha_url": "", "ha_entity_id": ""})

        errors = addon_config.validate_options(options)

        self.assertIn("ha_url must be set for proof-test.", errors)
        self.assertIn("ha_entity_id must be set for proof-test.", errors)

    def test_ha_api_check_requires_home_assistant_target(self):
        options = addon_config.DEFAULTS.copy()
        options.update({"operation": "ha-api-check", "ha_url": "", "ha_entity_id": ""})

        errors = addon_config.validate_options(options)

        self.assertIn("ha_url must be set for ha-api-check.", errors)
        self.assertIn("ha_entity_id must be set for ha-api-check.", errors)

    def test_preflight_uses_existing_config_without_requiring_options(self):
        options = addon_config.DEFAULTS.copy()
        options.update(
            {
                "operation": "preflight",
                "mqtt_broker": "",
                "mqtt_port": 0,
                "device_id": "",
                "node_id": "",
                "discovery_prefix": "",
                "skylight_uuid": "not-a-uuid",
            }
        )

        self.assertEqual(addon_config.validate_options(options), [])
        self.assertFalse(addon_config.should_write_gateway_config(options))
        self.assertIn("PESETECH_OPERATION=preflight", addon_config.shell_exports(options))

    def test_diagnostics_uses_existing_state_without_requiring_options(self):
        options = addon_config.DEFAULTS.copy()
        options.update(
            {
                "operation": "diagnostics",
                "mqtt_broker": "",
                "mqtt_port": 0,
                "device_id": "",
                "node_id": "",
                "discovery_prefix": "",
                "skylight_uuid": "not-a-uuid",
            }
        )

        self.assertEqual(addon_config.validate_options(options), [])
        self.assertFalse(addon_config.should_write_gateway_config(options))
        self.assertIn("PESETECH_OPERATION=diagnostics", addon_config.shell_exports(options))

    def test_import_main_does_not_overwrite_existing_imported_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            options_path = temp_path / "options.json"
            output_path = temp_path / "config.yaml"
            output_path.write_text("imported: true\n", encoding="utf-8")
            options_path.write_text(
                json.dumps(
                    {
                        "operation": "import",
                        "mqtt_broker": "homeassistant.local",
                        "mesh_json_path": "/share/pesetech_mesh.json",
                    }
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = run_main(["--options", str(options_path), "--output", str(output_path), "--shell"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "imported: true\n")
            self.assertIn("PESETECH_OPERATION=import", stdout.getvalue())

    def test_import_main_writes_base_config_when_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            options_path = temp_path / "options.json"
            output_path = temp_path / "config.yaml"
            options_path.write_text(
                json.dumps(
                    {
                        "operation": "import",
                        "mqtt_broker": "homeassistant.local",
                        "mqtt_port": 1884,
                        "mqtt_username": "mqtt",
                        "mqtt_password": "secret",
                        "discovery_prefix": "ha",
                        "node_id": "pesetech_mesh",
                        "mesh_json_path": "/share/pesetech_mesh.json",
                    }
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = run_main(["--options", str(options_path), "--output", str(output_path), "--shell"])

            rendered = output_path.read_text(encoding="utf-8")
            self.assertEqual(exit_code, 0)
            self.assertIn('broker: "homeassistant.local"', rendered)
            self.assertIn("port: 1884", rendered)
            self.assertIn('username: "mqtt"', rendered)
            self.assertIn('password: "secret"', rendered)
            self.assertIn('discovery_prefix: "ha"', rendered)
            self.assertIn('node_id: "pesetech_mesh"', rendered)
            self.assertIn("mesh: {}", rendered)
            self.assertIn("PESETECH_OPERATION=import", stdout.getvalue())

    def test_import_force_main_refreshes_base_config_from_options(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            options_path = temp_path / "options.json"
            output_path = temp_path / "config.yaml"
            output_path.write_text("mqtt:\n  broker: old.local\nmesh: {}\n", encoding="utf-8")
            options_path.write_text(
                json.dumps(
                    {
                        "operation": "import",
                        "mqtt_broker": "new.local",
                        "mesh_json_path": "/share/pesetech_mesh.json",
                        "import_force": True,
                    }
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = run_main(["--options", str(options_path), "--output", str(output_path), "--shell"])

            self.assertEqual(exit_code, 0)
            self.assertIn('broker: "new.local"', output_path.read_text(encoding="utf-8"))

    def test_import_main_refreshes_placeholder_seed_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            options_path = temp_path / "options.json"
            output_path = temp_path / "config.yaml"
            output_path.write_text(
                "mqtt:\n  broker: <home_assistant_mqtt_host>\nmesh:\n",
                encoding="utf-8",
            )
            options_path.write_text(
                json.dumps(
                    {
                        "operation": "import",
                        "mqtt_broker": "homeassistant.local",
                        "mesh_json_path": "/share/pesetech_mesh.json",
                    }
                ),
                encoding="utf-8",
            )

            exit_code = run_main(["--options", str(options_path), "--output", str(output_path)])

            rendered = output_path.read_text(encoding="utf-8")
            self.assertEqual(exit_code, 0)
            self.assertIn('broker: "homeassistant.local"', rendered)
            self.assertIn("mesh: {}", rendered)
            self.assertNotIn("<home_assistant_mqtt_host>", rendered)

    def test_import_check_main_does_not_overwrite_existing_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            options_path = temp_path / "options.json"
            output_path = temp_path / "config.yaml"
            output_path.write_text("persisted: true\n", encoding="utf-8")
            options_path.write_text(
                json.dumps(
                    {
                        "operation": "import-check",
                        "mesh_json_path": "/share/pesetech_mesh.json",
                    }
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = run_main(["--options", str(options_path), "--output", str(output_path), "--shell"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "persisted: true\n")
            self.assertIn("PESETECH_OPERATION=import-check", stdout.getvalue())

    def test_cloud_fetch_main_does_not_overwrite_existing_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            options_path = temp_path / "options.json"
            output_path = temp_path / "config.yaml"
            output_path.write_text("persisted: true\n", encoding="utf-8")
            options_path.write_text(json.dumps({"operation": "cloud-fetch"}), encoding="utf-8")
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = run_main(["--options", str(options_path), "--output", str(output_path), "--shell"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "persisted: true\n")
            self.assertIn("PESETECH_OPERATION=cloud-fetch", stdout.getvalue())
            self.assertIn("PESETECH_CLOUD_OUTPUT=/share/pesetech_mesh.json", stdout.getvalue())
            self.assertIn("PESETECH_CLOUD_REPORT=/share/pesetech_cloud_fetch_report.json", stdout.getvalue())

    def test_service_without_uuid_does_not_overwrite_existing_imported_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            options_path = temp_path / "options.json"
            output_path = temp_path / "config.yaml"
            output_path.write_text("imported: true\n", encoding="utf-8")
            options_path.write_text(
                json.dumps(
                    {
                        "operation": "service",
                        "mqtt_broker": "",
                        "mqtt_port": 0,
                        "node_id": "",
                        "discovery_prefix": "",
                        "skylight_uuid": "",
                    }
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = run_main(["--options", str(options_path), "--output", str(output_path), "--shell"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "imported: true\n")
            self.assertIn("PESETECH_OPERATION=service", stdout.getvalue())
            self.assertIn("PESETECH_UUID=''", stdout.getvalue())
            self.assertIn("PESETECH_MQTT_SOURCE=persisted", stdout.getvalue())

    def test_runtime_check_main_does_not_overwrite_existing_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            options_path = temp_path / "options.json"
            output_path = temp_path / "config.yaml"
            output_path.write_text("persisted: true\n", encoding="utf-8")
            options_path.write_text(json.dumps({"operation": "runtime-check"}), encoding="utf-8")
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = run_main(["--options", str(options_path), "--output", str(output_path), "--shell"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "persisted: true\n")
            self.assertIn("PESETECH_OPERATION=runtime-check", stdout.getvalue())

    def test_move_test_main_does_not_overwrite_existing_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            options_path = temp_path / "options.json"
            output_path = temp_path / "config.yaml"
            output_path.write_text("persisted: true\n", encoding="utf-8")
            options_path.write_text(json.dumps({"operation": "move-test"}), encoding="utf-8")
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = run_main(["--options", str(options_path), "--output", str(output_path), "--shell"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "persisted: true\n")
            self.assertIn("PESETECH_OPERATION=move-test", stdout.getvalue())
            self.assertIn("PESETECH_MQTT_SOURCE=persisted", stdout.getvalue())

    def test_ha_service_test_main_does_not_overwrite_existing_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            options_path = temp_path / "options.json"
            output_path = temp_path / "config.yaml"
            output_path.write_text("persisted: true\n", encoding="utf-8")
            options_path.write_text(json.dumps({"operation": "ha-service-test"}), encoding="utf-8")
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = run_main(["--options", str(options_path), "--output", str(output_path), "--shell"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "persisted: true\n")
            self.assertIn("PESETECH_OPERATION=ha-service-test", stdout.getvalue())
            self.assertIn("PESETECH_HA_URL=http://supervisor/core", stdout.getvalue())
            self.assertIn("PESETECH_MQTT_SOURCE=persisted", stdout.getvalue())

    def test_ha_api_check_main_does_not_overwrite_existing_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            options_path = temp_path / "options.json"
            output_path = temp_path / "config.yaml"
            output_path.write_text("persisted: true\n", encoding="utf-8")
            options_path.write_text(json.dumps({"operation": "ha-api-check"}), encoding="utf-8")
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = run_main(["--options", str(options_path), "--output", str(output_path), "--shell"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "persisted: true\n")
            self.assertIn("PESETECH_OPERATION=ha-api-check", stdout.getvalue())
            self.assertIn("PESETECH_HA_URL=http://supervisor/core", stdout.getvalue())
            self.assertIn("PESETECH_MQTT_SOURCE=none", stdout.getvalue())

    def test_preflight_main_does_not_overwrite_existing_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            options_path = temp_path / "options.json"
            output_path = temp_path / "config.yaml"
            output_path.write_text("persisted: true\n", encoding="utf-8")
            options_path.write_text(json.dumps({"operation": "preflight"}), encoding="utf-8")
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = run_main(["--options", str(options_path), "--output", str(output_path), "--shell"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "persisted: true\n")
            self.assertIn("PESETECH_OPERATION=preflight", stdout.getvalue())
            self.assertIn("PESETECH_MQTT_SOURCE=persisted", stdout.getvalue())

    def test_diagnostics_main_does_not_overwrite_existing_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            options_path = temp_path / "options.json"
            output_path = temp_path / "config.yaml"
            output_path.write_text("persisted: true\n", encoding="utf-8")
            options_path.write_text(json.dumps({"operation": "diagnostics"}), encoding="utf-8")
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = run_main(["--options", str(options_path), "--output", str(output_path), "--shell"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "persisted: true\n")
            self.assertIn("PESETECH_OPERATION=diagnostics", stdout.getvalue())
            self.assertIn("PESETECH_MQTT_SOURCE=persisted", stdout.getvalue())

    def test_invalid_mqtt_port_fails_validation(self):
        options = addon_config.DEFAULTS.copy()
        options.update({"operation": "scan", "mqtt_broker": "homeassistant.local", "mqtt_port": 0})

        self.assertIn("mqtt_port must be an integer between 1 and 65535.", addon_config.validate_options(options))

    def test_main_writes_config_and_shell_exports(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            options_path = temp_path / "options.json"
            output_path = temp_path / "config.yaml"
            options_path.write_text(
                json.dumps(
                    {
                        "operation": "provision",
                        "mqtt_broker": "homeassistant.local",
                        "skylight_uuid": "00112233-4455-6677-8899-aabbccddeeff",
                    }
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = run_main(["--options", str(options_path), "--output", str(output_path), "--shell"])

            rendered = output_path.read_text(encoding="utf-8")
            self.assertEqual(exit_code, 0)
            self.assertIn('type: "pesetech_skylight"', rendered)
            self.assertIn('default_entity_id: "light.skylight"', rendered)
            self.assertIn("PESETECH_OPERATION=provision", stdout.getvalue())
            self.assertIn("PESETECH_UUID=00112233-4455-6677-8899-aabbccddeeff", stdout.getvalue())

    def test_main_fetches_supervisor_mqtt_service_for_blank_broker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            options_path = temp_path / "options.json"
            output_path = temp_path / "config.yaml"
            options_path.write_text(
                json.dumps(
                    {
                        "operation": "provision",
                        "mqtt_broker": "",
                        "skylight_uuid": "00112233-4455-6677-8899-aabbccddeeff",
                    }
                ),
                encoding="utf-8",
            )
            original_fetch = addon_config.fetch_supervisor_mqtt_service
            try:
                addon_config.fetch_supervisor_mqtt_service = lambda base_url: {
                    "host": "172.30.33.0",
                    "port": "1883",
                    "ssl": False,
                    "username": "addon",
                    "password": "secret",
                }
                stderr = io.StringIO()
                stdout = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = run_main(["--options", str(options_path), "--output", str(output_path), "--shell"])
            finally:
                addon_config.fetch_supervisor_mqtt_service = original_fetch

            rendered = output_path.read_text(encoding="utf-8")
            self.assertEqual(exit_code, 0)
            self.assertIn('broker: "172.30.33.0"', rendered)
            self.assertIn("port: 1883", rendered)
            self.assertIn('username: "addon"', rendered)
            self.assertIn('password: "secret"', rendered)
            self.assertIn("Using Home Assistant Supervisor MQTT service credentials.", stderr.getvalue())
            self.assertIn("PESETECH_MQTT_SOURCE=supervisor", stdout.getvalue())

    def test_main_reports_missing_broker_when_supervisor_mqtt_is_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            options_path = Path(temp_dir) / "options.json"
            options_path.write_text(json.dumps({"operation": "scan", "mqtt_from_supervisor": False}), encoding="utf-8")
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                exit_code = run_main(["--options", str(options_path)])

            self.assertEqual(exit_code, 2)
            self.assertIn("mqtt_broker must be set", stderr.getvalue())


def run_main(argv):
    original_argv = addon_config.sys.argv
    try:
        addon_config.sys.argv = [str(SCRIPT_PATH), *argv]
        return addon_config.main()
    finally:
        addon_config.sys.argv = original_argv


if __name__ == "__main__":
    unittest.main()
