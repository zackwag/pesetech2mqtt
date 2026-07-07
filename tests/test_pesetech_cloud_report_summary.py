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
    / "pesetech_cloud_report_summary.py"
)
spec = importlib.util.spec_from_file_location("pesetech_cloud_report_summary", SCRIPT_PATH)
summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary)


class PesetechCloudReportSummaryTest(unittest.TestCase):
    def test_prints_home_id_next_step_without_mesh_keys(self):
        report = {
            "status": "endpoint-fetch-failed",
            "candidate_count": 0,
            "homes": [{"home_id": "home-1", "name": "Studio"}],
            "requested_home_ids": [],
            "candidates": [
                {
                    "summary": [
                        "net key 00112233445566778899AABBCCDDEEFF",
                        "device key FFEEDDCCBBAA99887766554433221100",
                    ]
                }
            ],
        }
        output = io.StringIO()

        summary.print_summary(report, stream=output)

        text = output.getvalue()
        self.assertIn("status: endpoint-fetch-failed", text)
        self.assertIn("homes: home-1 (Studio)", text)
        self.assertIn("set cloud_home_id", text)
        self.assertNotIn("00112233445566778899AABBCCDDEEFF", text)
        self.assertNotIn("FFEEDDCCBBAA99887766554433221100", text)

    def test_prints_candidate_next_step(self):
        output = io.StringIO()

        summary.print_summary({"status": "candidate-selection-failed", "candidate_count": 3}, stream=output)

        text = output.getvalue()
        self.assertIn("candidates: 3", text)
        self.assertIn("set cloud_candidate", text)

    def test_main_reports_invalid_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "report.json"
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
