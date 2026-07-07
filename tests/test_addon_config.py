import importlib.util
import tempfile
import unittest

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_addon_config_module():
    spec = importlib.util.spec_from_file_location(
        "addon_config_under_test",
        REPO_ROOT / "scripts/pesetech_addon_config.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ADDON_CONFIG = load_addon_config_module()


class AddonConfigTest(unittest.TestCase):
    def test_raw_payload_marker_enables_diagnostic_monitor_exports(self):
        exports = ADDON_CONFIG.shell_exports(
            {
                "operation": "service",
                "raw_payload": "diagnostic-monitor-enabled diagnostic-export-enabled btmon-monitor-enabled",
            }
        )

        self.assertIn("PESETECH_DIAGNOSTIC_MONITOR_ENABLED=true", exports)
        self.assertIn("PESETECH_DIAGNOSTIC_MONITOR_PATH=/share/pesetech-command-monitor.jsonl", exports)
        self.assertIn("PESETECH_DIAGNOSTIC_EXPORT_ENABLED=true", exports)
        self.assertIn("PESETECH_DIAGNOSTIC_EXPORT_PORT=8766", exports)
        self.assertIn("PESETECH_BTMON_MONITOR_ENABLED=true", exports)
        self.assertIn("PESETECH_BTMON_MONITOR_EVENTS_PATH=/share/pesetech-btmon-events.jsonl", exports)

    def test_service_runtime_patch_only_updates_monitor_section(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(
                "\n".join(
                    [
                        "mqtt:",
                        "  broker: \"core-mosquitto\"",
                        "  port: 1883",
                        "mesh:",
                        "  skylight_a:",
                        "    uuid: \"355aa138-c1a4-1118-5065-736554656368\"",
                        "    name: \"Skylight A\"",
                        "    type: \"pesetech_skylight\"",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            changed = ADDON_CONFIG.update_service_runtime_config(
                {
                    "operation": "service",
                    "raw_payload": "diagnostic-monitor-enabled btmon-monitor-enabled",
                    "diagnostic_monitor_summary_interval_seconds": 30,
                    "btmon_monitor_summary_interval_seconds": 45,
                },
                path,
            )

            self.assertTrue(changed)
            config = ADDON_CONFIG.load_simple_yaml(path.read_text(encoding="utf-8"))
            self.assertEqual(config["mqtt"]["broker"], "core-mosquitto")
            self.assertEqual(config["mesh"]["skylight_a"]["uuid"], "355aa138-c1a4-1118-5065-736554656368")
            self.assertEqual(
                config["diagnostic_monitor"],
                {
                    "enabled": True,
                    "path": "/share/pesetech-command-monitor.jsonl",
                    "summary_interval_seconds": 30,
                },
            )
            self.assertEqual(
                config["diagnostic_export"],
                {
                    "enabled": False,
                    "port": 8766,
                    "tail_bytes": 1048576,
                },
            )
            self.assertEqual(
                config["btmon_monitor"],
                {
                    "enabled": True,
                    "adapter": "",
                    "raw_path": "/share/pesetech-btmon.log",
                    "events_path": "/share/pesetech-btmon-events.jsonl",
                    "summary_path": "/share/pesetech-btmon-summary.jsonl",
                    "summary_interval_seconds": 45,
                    "max_bytes": 26214400,
                    "max_files": 3,
                    "events_max_bytes": 5242880,
                    "events_max_files": 3,
                    "summary_max_bytes": 5242880,
                    "summary_max_files": 3,
                },
            )

    def test_service_runtime_patch_leaves_config_alone_when_monitor_not_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            original = "mqtt:\n  broker: \"core-mosquitto\"\nmesh: {}\n"
            path.write_text(original, encoding="utf-8")

            changed = ADDON_CONFIG.update_service_runtime_config({"operation": "service"}, path)

            self.assertFalse(changed)
            self.assertEqual(path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
