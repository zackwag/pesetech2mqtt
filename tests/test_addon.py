import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "pesetech_ble_mesh"


class AddonLayoutTest(unittest.TestCase):
    def test_standard_repository_layout(self):
        self.assertTrue((ROOT / "repository.yaml").is_file())
        for name in ("config.yaml", "Dockerfile", "run.sh", "README.md", "DOCS.md"):
            self.assertTrue((ADDON / name).is_file(), name)

    def test_manifest_has_one_optionless_mqtt_service_path(self):
        manifest = (ADDON / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("version: 0.2.1", manifest)
        self.assertIn("boot: auto", manifest)
        self.assertIn("  - mqtt:need", manifest)
        self.assertIn("options: {}", manifest)
        self.assertIn("schema: {}", manifest)
        self.assertNotIn("ports:", manifest)
        self.assertNotIn("homeassistant_api", manifest)
        self.assertNotIn("8766", manifest)
        self.assertIn("read_only: true", manifest)

    def test_retired_subsystems_are_absent(self):
        for name in ("gateway", "docker", "scripts"):
            self.assertFalse((ROOT / name).exists())
        files = "\n".join(str(path.relative_to(ADDON)) for path in (ADDON / "app").rglob("*.py"))
        for retired in ("diagnostic", "btmon", "skylight_programs", "raw_command", "provisioner"):
            self.assertNotIn(retired, files)

    def test_startup_monitors_both_required_processes(self):
        script = (ADDON / "run.sh").read_text(encoding="utf-8")
        self.assertIn('wait -n "$meshd_pid" "$gateway_pid"', script)
        self.assertIn("Home Assistant Watchdog will restart the add-on", script)
        self.assertIn("python3 -m app.import_mesh --ensure", script)
        self.assertIn("/usr/bin/bluetooth-meshd", script)

    def test_bluez_install_includes_dbus_policy(self):
        script = (ADDON / "build" / "install-bluez.sh").read_text(encoding="utf-8")
        self.assertIn("make install", script)
        self.assertNotIn("install -m 0755 mesh/bluetooth-meshd", script)


if __name__ == "__main__":
    unittest.main()
