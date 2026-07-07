import json
import asyncio
import importlib.util
import sys
import tempfile
import unittest

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "gateway"))

from modules.skylight_programs import (  # noqa: E402
    build_program_commands,
    load_program_config,
    normalize_program_config,
    program_config_hash,
    save_program_config,
)


BRIDGE_SPEC = importlib.util.spec_from_file_location(
    "skylight_programs_bridge",
    REPO_ROOT / "gateway/mqtt/bridges/skylight_programs.py",
)
BRIDGE_MODULE = importlib.util.module_from_spec(BRIDGE_SPEC)
sys.modules[BRIDGE_SPEC.name] = BRIDGE_MODULE
BRIDGE_SPEC.loader.exec_module(BRIDGE_MODULE)
SkylightProgramsMqttBridge = BRIDGE_MODULE.SkylightProgramsMqttBridge


class FakeConfig:
    def optional(self, _path, fallback=None):
        return fallback


class FakeMessenger:
    discovery_prefix = "homeassistant"
    topic = "mqtt_mesh"
    config = FakeConfig()

    def __init__(self):
        self.published = []

    def node_topic(self, component, node):
        return f"{self.discovery_prefix}/{component}/{self.topic}/{node}"

    async def publish(self, component, node, topic, message, **kwargs):
        self.published.append((component, node, topic, message, kwargs))


class SkylightProgramConfigTest(unittest.TestCase):
    def test_default_config_uses_fixed_group_rows(self):
        config = normalize_program_config()

        self.assertEqual([row["clock_id"] for row in config["hcl"]], [16, 19, 17, 18])
        self.assertEqual([row["clock_id"] for row in config["schedule"]], [11, 12])
        self.assertEqual([row["time"] for row in config["hcl"]], ["08:30", "09:00", "20:00", "21:00"])
        self.assertEqual(config["dawn_fluctuation"]["scene_type_id"], 79)
        self.assertEqual(config["hcl"][1]["scene"], "10000K")

    def test_sample_config_normalizes_and_builds_expected_programs(self):
        config = load_program_config(str(REPO_ROOT / "docker/config/pesetech-skylight-programs.json.sample"))
        commands = build_program_commands(config)

        self.assertEqual([command["kind"] for command in commands], [
            "dawn_fluctuation",
            "phased_off",
            "hcl",
            "hcl",
            "hcl",
            "hcl",
            "schedule",
            "schedule",
        ])
        self.assertEqual(commands[0]["desired"]["start_seconds"], 7 * 3600 + 15 * 60)
        self.assertEqual(commands[0]["desired"]["scene"], "3000K")
        self.assertEqual(commands[2]["desired"]["clock_id"], 16)
        self.assertEqual(commands[-2]["desired"]["power"], 1)
        self.assertEqual(commands[-1]["desired"]["power"], 0)

    def test_save_program_config_round_trips_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "programs.json"
            config = normalize_program_config()
            config["dawn_fluctuation"]["time"] = "08:15"
            saved = save_program_config(str(path), config)

            self.assertTrue(path.exists())
            self.assertEqual(saved["dawn_fluctuation"]["time"], "08:15")
            self.assertEqual(load_program_config(str(path)), saved)
            self.assertEqual(json.loads(path.read_text())["dawn_fluctuation"]["time"], "08:15")
            self.assertEqual(program_config_hash(saved), program_config_hash(load_program_config(str(path))))

    def test_invalid_time_is_rejected(self):
        config = normalize_program_config()
        config["phased_off"]["time"] = "99:99"

        with self.assertRaises(ValueError):
            normalize_program_config(config)

    def test_mqtt_discovery_exposes_program_entities(self):
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            messenger = FakeMessenger()
            bridge = SkylightProgramsMqttBridge(messenger)
            loop.run_until_complete(bridge._publish_discovery())
        finally:
            loop.close()
            asyncio.set_event_loop(None)

        configs = [message for message in messenger.published if message[2] == "config"]
        default_entity_ids = {
            payload["default_entity_id"]
            for _component, _node, _topic, payload, _kwargs in configs
        }

        self.assertEqual(len(configs), 32)
        self.assertIn("switch.pesetech_skylight_dawn_fluctuation_enabled", default_entity_ids)
        self.assertIn("select.pesetech_skylight_hcl_4_cct_scene", default_entity_ids)
        self.assertIn("button.pesetech_skylight_reinforce_now", default_entity_ids)
        self.assertIn("sensor.pesetech_skylight_programs_status", default_entity_ids)


if __name__ == "__main__":
    unittest.main()
