import importlib.util
import io
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "pesetech_preflight.py"
)
spec = importlib.util.spec_from_file_location("pesetech_preflight", SCRIPT_PATH)
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class PesetechPreflightTest(unittest.TestCase):
    def test_placeholder_config_fails_fast(self):
        config = {
            "mqtt": {"broker": "<mqtt_broker>", "username": "<username>"},
            "mesh": {
                "<hass_device_id>": {
                    "uuid": "<bluetooth_mesh_device_uuid>",
                    "type": "light",
                }
            },
        }

        errors, warnings, mesh_topic = preflight.validate_config(config)

        self.assertEqual(mesh_topic, "mqtt_mesh")
        self.assertIn("mqtt.broker must be set to the Home Assistant MQTT broker host.", errors)
        self.assertIn("mqtt.username is still a placeholder; remove it or set a real value.", errors)
        self.assertIn(
            "mesh contains the sample placeholder device id; replace it with skylight or another id.",
            errors,
        )
        self.assertEqual(warnings, [])

    def test_real_config_reports_topics_and_passes(self):
        config = {
            "mqtt": {"broker": "homeassistant.local", "node_id": "pesetech_mesh"},
            "mesh": {
                "skylight": {
                    "uuid": "00112233-4455-6677-8899-aabbccddeeff",
                    "type": "pesetech_skylight",
                }
            },
        }
        args = types.SimpleNamespace(discovery_prefix="homeassistant", host=False)
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = preflight.print_report(config, {}, args)

        self.assertEqual(exit_code, 0)
        report = output.getvalue()
        self.assertIn("homeassistant/light/pesetech_mesh/skylight/config", report)
        self.assertIn("homeassistant/light/pesetech_mesh/skylight/set", report)
        self.assertIn("Config preflight passed.", report)
        self.assertIn("not provisioned yet", report)

    def test_real_config_uses_configured_discovery_prefix(self):
        config = {
            "mqtt": {
                "broker": "homeassistant.local",
                "discovery_prefix": "ha_discovery",
                "node_id": "pesetech_mesh",
            },
            "mesh": {
                "skylight": {
                    "uuid": "00112233-4455-6677-8899-aabbccddeeff",
                    "type": "pesetech_skylight",
                }
            },
        }
        args = types.SimpleNamespace(discovery_prefix=None, host=False)
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = preflight.print_report(config, {}, args)

        self.assertEqual(exit_code, 0)
        report = output.getvalue()
        self.assertIn("ha_discovery/light/pesetech_mesh/skylight/config", report)
        self.assertIn("ha_discovery/light/pesetech_mesh/skylight/set", report)

    def test_print_report_fails_for_fatal_imported_store_findings(self):
        config = {
            "mqtt": {"broker": "homeassistant.local"},
            "mesh": {
                "skylight": {
                    "uuid": "00112233-4455-6677-8899-aabbccddeeff",
                    "type": "pesetech_skylight",
                }
            },
        }
        store = {
            "remote_nodes": {
                "00112233-4455-6677-8899-aabbccddeeff": {
                    "device_key": "ffeeddccbbaa99887766554433221100",
                    "unicast": 2,
                    "count": 3,
                }
            }
        }
        args = types.SimpleNamespace(discovery_prefix=None, host=False)
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = preflight.print_report(config, store, args)

        self.assertEqual(exit_code, 1)
        report = output.getvalue()
        self.assertIn("Errors:", report)
        self.assertIn(
            "remote_nodes are present but keychain.network_key is missing; an imported skylight will not respond to generated replacement keys.",
            report,
        )

    def test_invalid_uuid_is_error(self):
        config = {
            "mqtt": {"broker": "homeassistant.local"},
            "mesh": {"skylight": {"uuid": "not-a-uuid", "type": "pesetech_skylight"}},
        }

        errors, _, _ = preflight.validate_config(config)

        self.assertEqual(errors, ["mesh.skylight.uuid is not a valid UUID: not-a-uuid"])

    def test_invalid_topic_segments_are_errors(self):
        config = {
            "mqtt": {"broker": "homeassistant.local", "node_id": "bad/topic"},
            "mesh": {
                "sky/light": {
                    "uuid": "00112233-4455-6677-8899-aabbccddeeff",
                    "type": "pesetech_skylight",
                }
            },
        }

        errors, _, _ = preflight.validate_config(config)

        self.assertIn("mqtt.topic/node_id must be a single MQTT topic segment and must not contain '/'.", errors)
        self.assertIn("mesh device id 'sky/light' must be a single MQTT topic segment and must not contain '/'.", errors)

    def test_invalid_mqtt_ports_are_errors(self):
        valid_mesh = {
            "skylight": {
                "uuid": "00112233-4455-6677-8899-aabbccddeeff",
                "type": "pesetech_skylight",
            }
        }

        for port in ("not-a-port", 0, 65536):
            errors, _, _ = preflight.validate_config({"mqtt": {"broker": "homeassistant.local", "port": port}, "mesh": valid_mesh})
            self.assertIn("mqtt.port must be an integer between 1 and 65535.", errors)

        errors, _, _ = preflight.validate_config({"mqtt": {"broker": "homeassistant.local", "port": "<port>"}, "mesh": valid_mesh})
        self.assertIn("mqtt.port is still a placeholder; remove it or set a real port.", errors)

    def test_invalid_default_entity_id_is_error(self):
        config = {
            "mqtt": {"broker": "homeassistant.local"},
            "mesh": {
                "skylight": {
                    "uuid": "00112233-4455-6677-8899-aabbccddeeff",
                    "type": "pesetech_skylight",
                    "default_entity_id": "switch.skylight",
                }
            },
        }

        errors, _, _ = preflight.validate_config(config)

        self.assertIn(
            "mesh.skylight.default_entity_id must be a Home Assistant light entity id, for example light.skylight.",
            errors,
        )

    def test_pesetech_scale_overrides_warn_for_first_test(self):
        config = {
            "mqtt": {"broker": "homeassistant.local"},
            "mesh": {
                "skylight": {
                    "uuid": "00112233-4455-6677-8899-aabbccddeeff",
                    "type": "pesetech_skylight",
                    "brightness_scale": 255,
                    "min_mireds": 153,
                    "max_mireds": 500,
                }
            },
        }

        _, warnings, _ = preflight.validate_config(config)

        self.assertIn("mesh.skylight.brightness_scale is 255; first Pesetech test expects 65280.", warnings)
        self.assertIn("mesh.skylight.min_mireds is 153; first Pesetech test expects 100.", warnings)
        self.assertIn("mesh.skylight.max_mireds is 500; first Pesetech test expects 556.", warnings)

    def test_bluetooth_adapter_detection_accepts_nonzero_hci_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bluetooth_dir = Path(temp_dir) / "bluetooth"
            bluetooth_dir.mkdir()
            (bluetooth_dir / "hci1").mkdir()

            self.assertEqual(preflight.bluetooth_adapters(str(bluetooth_dir)), ["hci1"])
            warnings = preflight.host_checks(skip_docker=True, bluetooth_sys_class=str(bluetooth_dir))

        self.assertNotIn(
            f"No Linux Bluetooth hci* adapter was found under {bluetooth_dir}.",
            warnings,
        )

    def test_bluetooth_adapter_detection_warns_when_no_hci_adapter_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bluetooth_dir = Path(temp_dir) / "bluetooth"
            bluetooth_dir.mkdir()

            warnings = preflight.host_checks(skip_docker=True, bluetooth_sys_class=str(bluetooth_dir))

        self.assertIn(
            f"No Linux Bluetooth hci* adapter was found under {bluetooth_dir}.",
            warnings,
        )

    def test_validate_store_normalizes_configured_uuid_case(self):
        config = {
            "mesh": {
                "skylight": {
                    "uuid": "00112233-4455-6677-8899-AABBCCDDEEFF",
                    "type": "pesetech_skylight",
                }
            }
        }
        store = {
            "nodes": {
                "00112233-4455-6677-8899-aabbccddeeff": {
                    "type": "pesetech_skylight",
                    "configured": True,
                    "unicast": 2,
                    "count": 3,
                }
            }
        }

        self.assertEqual(preflight.validate_store(config, store), [])

    def test_validate_store_warns_for_bad_imported_remote_node(self):
        config = {
            "mesh": {
                "skylight": {
                    "uuid": "00112233-4455-6677-8899-aabbccddeeff",
                    "type": "pesetech_skylight",
                }
            }
        }
        store = {
            "nodes": {
                "00112233-4455-6677-8899-aabbccddeeff": {
                    "type": "pesetech_skylight",
                    "configured": True,
                    "unicast": 2,
                    "count": 3,
                }
            },
            "remote_nodes": {
                "00112233-4455-6677-8899-aabbccddeeff": {
                    "device_key": "not-a-key",
                    "unicast": 4,
                    "count": 1,
                }
            },
        }

        warnings = preflight.validate_store(config, store)

        self.assertIn(
            "remote node 00112233-4455-6677-8899-aabbccddeeff has an invalid device_key; imported-mesh config operations may fail.",
            warnings,
        )
        self.assertIn("remote node 00112233-4455-6677-8899-aabbccddeeff unicast does not match store node unicast.", warnings)
        self.assertIn("remote node 00112233-4455-6677-8899-aabbccddeeff count does not match store node count.", warnings)

    def test_validate_store_accepts_imported_keychain_indexes(self):
        config = {"mesh": {}}
        store = {
            "keychain": {
                "network_key": "00112233445566778899aabbccddeeff",
                "network_key_index": "0x007",
                "app_key": "112233445566778899aabbccddeeff00",
                "app_key_index": "5",
                "app_key_bound_net_key_index": 7,
                "device_key": "ffeeddccbbaa99887766554433221100",
            }
        }

        self.assertEqual(preflight.validate_store(config, store), [])

    def test_validate_store_warns_for_bad_imported_keychain(self):
        config = {"mesh": {}}
        store = {
            "keychain": {
                "network_key": "not-a-key",
                "network_key_index": 7,
                "app_key": "112233445566778899aabbccddeeff00",
                "app_key_index": 4096,
                "app_key_bound_net_key_index": 8,
                "device_key": "also-bad",
            }
        }

        warnings = preflight.validate_store(config, store)

        self.assertIn("keychain.network_key must be a 16-byte hex key.", warnings)
        self.assertIn("keychain.device_key must be a 16-byte hex key.", warnings)
        self.assertIn("keychain.app_key_index must be an integer key index from 0 to 4095.", warnings)
        self.assertIn(
            "keychain.app_key_bound_net_key_index must match keychain.network_key_index for this single-network gateway profile.",
            warnings,
        )

    def test_validate_store_warns_when_imported_remote_nodes_have_no_mesh_keys(self):
        config = {"mesh": {}}
        store = {
            "remote_nodes": {
                "00112233-4455-6677-8899-aabbccddeeff": {
                    "device_key": "ffeeddccbbaa99887766554433221100",
                    "unicast": 2,
                    "count": 3,
                }
            }
        }

        warnings = preflight.validate_store(config, store)

        self.assertIn(
            "remote_nodes are present but keychain.network_key is missing; an imported skylight will not respond to generated replacement keys.",
            warnings,
        )
        self.assertIn(
            "remote_nodes are present but keychain.app_key is missing; an imported skylight will not respond to generated replacement keys.",
            warnings,
        )

    def test_validate_store_warns_for_bad_local_and_node_addresses(self):
        config = {"mesh": {}}
        store = {
            "local": {"address": 0, "iv_index": 0x100000000},
            "nodes": {
                "not-a-uuid": {"unicast": "0x8000", "count": 1},
                "00112233-4455-6677-8899-aabbccddeeff": {"unicast": "0x7fff", "count": 2},
                "11111111-2222-3333-4444-555555555555": {"unicast": "0x0005", "count": 0},
            },
        }

        warnings = preflight.validate_store(config, store)

        self.assertIn("local.address must be a unicast address from 0001 to 7FFF.", warnings)
        self.assertIn("local.iv_index must be an integer from 0 to FFFFFFFF.", warnings)
        self.assertIn("nodes.not-a-uuid is not a valid UUID key.", warnings)
        self.assertIn("nodes.not-a-uuid.unicast must be a unicast address from 0001 to 7FFF.", warnings)
        self.assertIn("nodes.00112233-4455-6677-8899-aabbccddeeff address range must stay within 0001..7FFF.", warnings)
        self.assertIn("nodes.11111111-2222-3333-4444-555555555555.count must be a positive integer.", warnings)

    def test_simple_yaml_fallback_parses_gateway_config_shape(self):
        config = preflight.load_simple_yaml(
            """
            ---
            mqtt:
              broker: homeassistant.local
              node_id: pesetech_mesh
            mesh:
              skylight:
                uuid: 00112233-4455-6677-8899-aabbccddeeff
                type: pesetech_skylight
                relay: false
            nodes: {}
            """
        )

        self.assertEqual(config["mqtt"]["broker"], "homeassistant.local")
        self.assertEqual(config["mqtt"]["node_id"], "pesetech_mesh")
        self.assertEqual(config["mesh"]["skylight"]["type"], "pesetech_skylight")
        self.assertFalse(config["mesh"]["skylight"]["relay"])
        self.assertEqual(config["nodes"], {})

    def test_host_checks_can_skip_docker_for_home_assistant_addon(self):
        original_command_exists = preflight.command_exists
        original_bluetooth_adapters = preflight.bluetooth_adapters
        try:
            preflight.command_exists = lambda name: False
            preflight.bluetooth_adapters = lambda sys_class="/sys/class/bluetooth": ["hci1"]

            with_docker = preflight.host_checks(skip_docker=False)
            without_docker = preflight.host_checks(skip_docker=True)

            self.assertIn("docker command not found.", with_docker)
            self.assertNotIn("docker command not found.", without_docker)
        finally:
            preflight.command_exists = original_command_exists
            preflight.bluetooth_adapters = original_bluetooth_adapters

    def test_mqtt_connect_check_passes_when_tcp_connection_opens(self):
        class FakeConnection:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

        calls = []
        original_create_connection = preflight.socket.create_connection
        try:
            def fake_create_connection(address, timeout):
                calls.append((address, timeout))
                return FakeConnection()

            preflight.socket.create_connection = fake_create_connection

            warnings = preflight.mqtt_connect_warnings(
                {"mqtt": {"broker": "homeassistant.local", "port": 1884}},
                timeout=1.25,
            )
        finally:
            preflight.socket.create_connection = original_create_connection

        self.assertEqual(warnings, [])
        self.assertEqual(calls, [(("homeassistant.local", 1884), 1.25)])

    def test_mqtt_connect_check_warns_when_tcp_connection_fails(self):
        original_create_connection = preflight.socket.create_connection
        try:
            def fake_create_connection(address, timeout):
                raise OSError("network unreachable")

            preflight.socket.create_connection = fake_create_connection

            warnings = preflight.mqtt_connect_warnings({"mqtt": {"broker": "homeassistant.local"}}, timeout=0.5)
        finally:
            preflight.socket.create_connection = original_create_connection

        self.assertEqual(
            warnings,
            ["MQTT broker homeassistant.local:1883 was not reachable from this host: network unreachable"],
        )

    def test_main_reports_missing_config_cleanly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing.yaml"
            stderr = io.StringIO()

            with redirect_stderr(stderr), self.assertRaises(SystemExit) as context:
                run_main(["--config", str(missing)])

            self.assertEqual(context.exception.code, 1)
            self.assertIn(f"Config file not found: {missing}", stderr.getvalue())


def run_main(argv):
    original_argv = preflight.sys.argv
    try:
        preflight.sys.argv = [str(SCRIPT_PATH), *argv]
        return preflight.main()
    finally:
        preflight.sys.argv = original_argv


if __name__ == "__main__":
    unittest.main()
