import os
import json
import sys
import tempfile
import unittest

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_btmon_module():
    sys.path.insert(0, str(REPO_ROOT / "gateway"))
    from diagnostics import btmon

    return btmon


BTMON = load_btmon_module()


class BtmonMonitorTest(unittest.TestCase):
    def test_marker_enables_monitor_and_derives_adapter(self):
        previous_raw = os.environ.get("PESETECH_RAW_PAYLOAD")
        previous_io = os.environ.get("PESETECH_MESH_IO")
        os.environ["PESETECH_RAW_PAYLOAD"] = "diagnostic-monitor-enabled btmon-monitor-enabled"
        os.environ["PESETECH_MESH_IO"] = "auto:hci2"
        try:
            monitor = BTMON.BtmonMonitor.from_config(FakeConfig())
        finally:
            restore_env("PESETECH_RAW_PAYLOAD", previous_raw)
            restore_env("PESETECH_MESH_IO", previous_io)

        self.assertTrue(monitor.enabled)
        self.assertEqual(monitor.adapter, "hci2")

    def test_classifies_important_controller_lines(self):
        self.assertEqual(BTMON.classify_btmon_line("< HCI Command: LE Set Scan Enable"), "hci_command")
        self.assertEqual(BTMON.classify_btmon_line("> HCI Event: LE Meta Event"), "hci_event")
        self.assertEqual(BTMON.classify_btmon_line("> ACL Data RX: Handle 64"), "acl_rx")
        self.assertEqual(BTMON.classify_btmon_line("Event type: Connectable undirected - ADV_IND"), "advertising_report")
        self.assertTrue(BTMON.is_important_btmon_line("> HCI Event: Disconnect Complete", "disconnect"))

    def test_selected_events_skip_routine_mesh_device_found(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            monitor = BTMON.BtmonMonitor(enabled=True, events_path=str(path))

            monitor.record_line("@ MGMT Event: Mesh Device Found (0x0031) plen 44")
            monitor.record_line("Status: Success (0x00)")
            monitor.record_line("> ACL Data RX: Handle 64")
            monitor.record_line("Status: Failed (0x03)")

            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual([event["category"] for event in events], ["acl_rx", "status_or_error"])
        self.assertEqual(events[1]["line"], "Status: Failed (0x03)")

    def test_summary_includes_bearer_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.jsonl"
            events_path = Path(directory) / "events.jsonl"
            monitor = BTMON.BtmonMonitor(enabled=True, summary_path=str(path), events_path=str(events_path))

            monitor.record_line("Event type: Connectable undirected - ADV_IND")
            monitor.record_line("> ACL Data RX: Handle 64")
            monitor.record_line("Status: Failed (0x03)")
            monitor.flush_summary()

            summary = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

        self.assertEqual(summary["bearer_counts"]["advertising"], 1)
        self.assertEqual(summary["bearer_counts"]["acl_gatt"], 1)
        self.assertEqual(summary["bearer_counts"]["non_success_status"], 1)


class FakeConfig:
    def optional(self, _path, fallback=None):
        return fallback


def restore_env(name, previous):
    if previous is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = previous


if __name__ == "__main__":
    unittest.main()
