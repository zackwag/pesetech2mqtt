import asyncio
import json
import os
import sys
import tempfile
import unittest

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_monitor_module():
    sys.path.insert(0, str(REPO_ROOT / "gateway"))
    from diagnostics import monitor

    return monitor


MONITOR_MODULE = load_monitor_module()


class FakeConfig:
    def __init__(self, values=None):
        self._values = values or {}

    def optional(self, path, fallback=None):
        return self._values.get(path, fallback)


class DiagnosticMonitorTest(unittest.TestCase):
    def test_disabled_monitor_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.jsonl"
            monitor = MONITOR_MODULE.DiagnosticMonitor(enabled=False, path=str(path))

            monitor.record("test", value=1)

            self.assertFalse(path.exists())

    def test_monitor_writes_jsonl_event(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.jsonl"
            monitor = MONITOR_MODULE.DiagnosticMonitor(enabled=True, path=str(path))

            monitor.record("test", payload={"bytes": b"\x01\x02"})

            events = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event"], "test")
            self.assertEqual(events[0]["payload"]["bytes"]["hex"], "0102")
            self.assertIn("run_id", events[0])

    def test_inbound_summary_aggregates_without_per_message_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.jsonl"
            monitor = MONITOR_MODULE.DiagnosticMonitor(enabled=True, path=str(path), summary_interval_seconds=60)

            monitor.record_inbound(0x0802, 0, 0x0001, {"opcode": "LIGHT_LIGHTNESS_STATUS", "value": 1})
            monitor.record_inbound(0x0802, 0, 0x0001, {"opcode": "LIGHT_LIGHTNESS_STATUS", "value": 2})
            monitor.flush_inbound_summary()

            events = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event"], "mesh_inbound_summary")
            self.assertEqual(events[0]["by_source_opcode"], {"0802:LIGHT_LIGHTNESS_STATUS": 2})
            self.assertEqual(
                events[0]["last_by_source_opcode"]["0802:LIGHT_LIGHTNESS_STATUS"]["message"]["value"],
                2,
            )

    def test_from_config_can_be_enabled_by_config(self):
        monitor = MONITOR_MODULE.DiagnosticMonitor.from_config(
            FakeConfig(
                {
                    "diagnostic_monitor.enabled": True,
                    "diagnostic_monitor.path": "/tmp/test-monitor.jsonl",
                    "diagnostic_monitor.summary_interval_seconds": 30,
                }
            )
        )

        self.assertTrue(monitor.enabled)
        self.assertEqual(monitor.path, "/tmp/test-monitor.jsonl")
        self.assertEqual(monitor.summary_interval_seconds, 30)

    def test_from_config_legacy_raw_payload_marker_enables_monitor(self):
        previous = os.environ.get("PESETECH_RAW_PAYLOAD")
        os.environ["PESETECH_RAW_PAYLOAD"] = "diagnostic-monitor-enabled"
        try:
            monitor = MONITOR_MODULE.DiagnosticMonitor.from_config(FakeConfig())
        finally:
            if previous is None:
                os.environ.pop("PESETECH_RAW_PAYLOAD", None)
            else:
                os.environ["PESETECH_RAW_PAYLOAD"] = previous

        self.assertTrue(monitor.enabled)


if __name__ == "__main__":
    unittest.main()
