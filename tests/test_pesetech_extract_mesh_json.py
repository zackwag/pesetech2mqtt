import argparse
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
    / "pesetech_extract_mesh_json.py"
)
spec = importlib.util.spec_from_file_location("pesetech_extract_mesh_json", SCRIPT_PATH)
extractor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extractor)


def sample_mesh(iv_index="0000002A"):
    return {
        "ivIndex": iv_index,
        "sequenceNumber": 17,
        "netKeys": [
            {
                "index": 0,
                "key": "00112233445566778899AABBCCDDEEFF",
            }
        ],
        "appKeys": [
            {
                "index": 0,
                "boundNetKey": 0,
                "key": "112233445566778899AABBCCDDEEFF00",
            }
        ],
        "provisioners": [{"UUID": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}],
        "nodes": [
            {
                "UUID": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "unicastAddress": "0001",
                "deviceKey": "00000000000000000000000000000000",
                "elements": [{"index": 0, "models": []}],
            },
            {
                "UUID": "00112233-4455-6677-8899-aabbccddeeff",
                "name": "Skylight",
                "unicastAddress": "0002",
                "deviceKey": "FFEEDDCCBBAA99887766554433221100",
                "elements": [
                    {"index": 0, "models": [{"modelId": "1000"}, {"modelId": "1300"}]},
                    {"index": 1, "models": [{"modelId": "1306"}]},
                ],
            },
        ],
    }


class PesetechExtractMeshJsonTest(unittest.TestCase):
    def test_discovers_mesh_json_inside_har_response_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "capture.har"
            response = {"data": {"info": [{"homeName": "Office", "meshJson": json.dumps(sample_mesh())}]}}
            path.write_text(
                json.dumps({"log": {"entries": [{"response": {"content": {"text": json.dumps(response)}}}]}}),
                encoding="utf-8",
            )

            candidates, skipped = extractor.discover_mesh_candidates([path])

            self.assertEqual(skipped, [])
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["storage"]["ivIndex"], "0000002A")

    def test_discovers_mesh_json_inside_log_line(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "app.log"
            path.write_text(
                "INFO response "
                + json.dumps({"homeId": "home-1", "meshJson": json.dumps(sample_mesh())})
                + " done\n",
                encoding="utf-8",
            )

            candidates, _skipped = extractor.discover_mesh_candidates([path])

            self.assertEqual(len(candidates), 1)
            self.assertIn("line1", candidates[0]["location"])

    def test_discovers_mesh_json_inside_binary_like_mmkv_blob(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "default"
            home_info = json.dumps({"homeId": "home-1", "homeName": "Office", "meshJson": json.dumps(sample_mesh())})
            path.write_bytes(b"\x00\x10homeInfo\x00\x02" + home_info.encode("utf-8") + b"\x00\x00crc")

            candidates, skipped = extractor.discover_mesh_candidates([path])

            self.assertEqual(skipped, [])
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["storage"]["nodes"][1]["name"], "Skylight")

    def test_summary_does_not_print_mesh_keys(self):
        candidate = {"source": Path("capture.har"), "location": "root.data", "storage": sample_mesh()}

        summary = extractor.summarize_candidate(candidate, 1)

        self.assertIn("Skylight", summary)
        self.assertNotIn("00112233445566778899AABBCCDDEEFF", summary)
        self.assertNotIn("FFEEDDCCBBAA99887766554433221100", summary)

    def test_writes_single_candidate_as_normalized_mesh_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            capture = root / "capture.json"
            output = root / "mesh.json"
            capture.write_text(json.dumps({"jsonNode": sample_mesh()}), encoding="utf-8")
            args = argparse.Namespace(
                inputs=[str(capture)],
                output=str(output),
                candidate=None,
                list=False,
                no_recursive=False,
                max_bytes=extractor.DEFAULT_MAX_BYTES,
            )

            with redirect_stdout(io.StringIO()):
                exit_code = extractor.extract_mesh_json(args)

            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["ivIndex"], "0000002A")

    def test_requires_candidate_number_when_multiple_meshes_exist(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            capture = root / "capture.json"
            output = root / "mesh.json"
            capture.write_text(
                json.dumps(
                    {
                        "data": {
                            "info": [
                                {"meshJson": json.dumps(sample_mesh("0000002A"))},
                                {"meshJson": json.dumps(sample_mesh("0000002B"))},
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                inputs=[str(capture)],
                output=str(output),
                candidate=None,
                list=False,
                no_recursive=False,
                max_bytes=extractor.DEFAULT_MAX_BYTES,
            )

            stderr = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                exit_code = extractor.extract_mesh_json(args)

            self.assertEqual(exit_code, 2)
            self.assertIn("--candidate N", stderr.getvalue())
            self.assertFalse(output.exists())

            args.candidate = 2
            with redirect_stdout(io.StringIO()):
                self.assertEqual(extractor.extract_mesh_json(args), 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["ivIndex"], "0000002B")


if __name__ == "__main__":
    unittest.main()
