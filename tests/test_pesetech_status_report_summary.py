import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "pesetech_status_report_summary.py"
)
spec = importlib.util.spec_from_file_location("pesetech_status_report_summary", SCRIPT_PATH)
summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary)


class PesetechStatusReportSummaryTest(unittest.TestCase):
    def test_prints_next_operation_and_cloud_homes(self):
        report = {
            "suggested_next_operation": "cloud-fetch",
            "next_action": "Set cloud_home_id from reports.cloud_fetch.homes.",
            "next_operation": {
                "configuration_snippet": "operation: cloud-fetch",
                "moves_real_light": False,
                "no_motion_gate": True,
            },
            "reports": {
                "cloud_fetch": {
                    "status": "endpoint-fetch-failed",
                    "homes": [{"home_id": "home-1", "name": "Studio"}],
                }
            },
            "files": {
                "config": {"path": "/data/config.yaml"},
            },
        }
        output = io.StringIO()

        summary.print_summary(report, stream=output)

        text = output.getvalue()
        self.assertIn("suggested_next_operation: cloud-fetch", text)
        self.assertIn("configuration_snippet", text)
        self.assertIn("moves_real_light: false", text)
        self.assertIn("no_motion_gate: true", text)
        self.assertIn("cloud_fetch_status: endpoint-fetch-failed", text)
        self.assertIn("cloud_homes: home-1 (Studio)", text)
        self.assertNotIn("/data/config.yaml", text)

    def test_main_reports_invalid_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "status.json"
            path.write_text("{bad json", encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = summary.main([str(path)])

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("not valid JSON", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
