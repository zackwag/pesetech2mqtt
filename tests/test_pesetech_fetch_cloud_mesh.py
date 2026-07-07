import argparse
import importlib.util
import json
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "pesetech_fetch_cloud_mesh.py"
)
spec = importlib.util.spec_from_file_location("pesetech_fetch_cloud_mesh", SCRIPT_PATH)
cloud = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cloud)


def sample_mesh(iv_index="0000002A"):
    return {
        "ivIndex": iv_index,
        "sequenceNumber": 17,
        "netKeys": [{"index": 0, "key": "00112233445566778899AABBCCDDEEFF"}],
        "appKeys": [{"index": 0, "boundNetKey": 0, "key": "112233445566778899AABBCCDDEEFF00"}],
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
                "elements": [{"index": 0, "models": [{"modelId": "1306"}]}],
            },
        ],
    }


class MockCloudServer:
    def __init__(self, routes):
        self.routes = routes
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.requests.append(
                    {
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "user_agent": self.headers.get("User-Agent"),
                        "accept_language": self.headers.get("Accept-Language"),
                        "body": body,
                    }
                )
                response = outer.routes.get(self.path, {"code": 404})
                http_status = response.get("_http_status", 200) if isinstance(response, dict) else 200
                payload = (
                    {key: value for key, value in response.items() if key != "_http_status"}
                    if isinstance(response, dict)
                    else response
                )
                encoded = json.dumps(payload).encode("utf-8")
                self.send_response(http_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, _format, *_args):
                return

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    @property
    def base_url(self):
        host, port = self.server.server_address
        return f"http://{host}:{port}"


def args_for(base_url, output=None, endpoint=None, token="secret-token", candidate=None, raw_output=None, report_output=None):
    return argparse.Namespace(
        output=output,
        candidate=candidate,
        list=False,
        raw_output=raw_output,
        report_output=report_output,
        base_url=base_url,
        region="europe",
        endpoint=endpoint,
        home_id=None,
        token=token,
        token_file=None,
        token_env="PESETECH_TEST_TOKEN",
        username="",
        username_file=None,
        username_env="PESETECH_TEST_USERNAME",
        password="",
        password_file=None,
        password_env="PESETECH_TEST_PASSWORD",
        user_origin=1,
        timeout=3,
        user_agent="test-agent",
        accept_language="en",
    )


class PesetechFetchCloudMeshTest(unittest.TestCase):
    def test_region_names_match_official_app_cloud_hosts(self):
        args = args_for("", token="")
        args.region = "asia"
        self.assertEqual(cloud.resolve_base_url(args), "http://test.lepuiot.com")

        args.region = "europe"
        self.assertEqual(cloud.resolve_base_url(args), "https://service.lepuiot.com")

        args.base_url = "http://127.0.0.1:1234"
        self.assertEqual(cloud.resolve_base_url(args), "http://127.0.0.1:1234")

    def test_fetches_home_list_and_writes_normalized_mesh(self):
        with tempfile.TemporaryDirectory() as temp_dir, MockCloudServer(
            {
                "/app/voice/homeList": {"code": 200, "data": [{"meshJson": json.dumps(sample_mesh())}]},
            }
        ) as server:
            output = Path(temp_dir) / "mesh.json"
            args = args_for(server.base_url, output=str(output), endpoint=["home-list"], token="Bearer abc123")

            with open(os.devnull, "w", encoding="utf-8") as devnull, redirect_stdout(devnull):
                self.assertEqual(cloud.fetch_cloud_mesh(args), 0)

            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["ivIndex"], "0000002A")
            self.assertEqual(server.requests[0]["path"], "/app/voice/homeList")
            self.assertEqual(server.requests[0]["authorization"], "Bearer abc123")
            self.assertEqual(server.requests[0]["user_agent"], "test-agent")
            self.assertEqual(server.requests[0]["body"], b"")

    def test_can_login_for_token_then_fetch_mesh(self):
        with tempfile.TemporaryDirectory() as temp_dir, MockCloudServer(
            {
                "/app/customer-login/login": {"code": 200, "data": {"authorization": "login-token"}},
                "/app/voice/homeList": {"code": 200, "data": [{"meshJson": json.dumps(sample_mesh())}]},
            }
        ) as server:
            output = Path(temp_dir) / "mesh.json"
            args = args_for(server.base_url, output=str(output), endpoint=["home-list"], token="")
            args.username = "person@example.com"
            args.password = "secret"

            with open(os.devnull, "w", encoding="utf-8") as devnull, redirect_stdout(devnull):
                self.assertEqual(cloud.fetch_cloud_mesh(args), 0)

            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["ivIndex"], "0000002A")
            self.assertEqual(server.requests[0]["path"], "/app/customer-login/login")
            self.assertIsNone(server.requests[0]["authorization"])
            self.assertEqual(
                json.loads(server.requests[0]["body"].decode("utf-8")),
                {"username": "person@example.com", "password": "secret", "userOrigin": 1},
            )
            self.assertEqual(server.requests[1]["path"], "/app/voice/homeList")
            self.assertEqual(server.requests[1]["authorization"], "Bearer login-token")

    def test_login_accepts_case_variants_and_zero_success_code(self):
        with tempfile.TemporaryDirectory() as temp_dir, MockCloudServer(
            {
                "/app/customer-login/login": {"Code": "0", "Data": {"Authorization": "Bearer mixed-token"}},
                "/app/voice/homeList": {"code": 200, "data": [{"meshJson": json.dumps(sample_mesh())}]},
            }
        ) as server:
            output = Path(temp_dir) / "mesh.json"
            args = args_for(server.base_url, output=str(output), endpoint=["home-list"], token="")
            args.username = "person@example.com"
            args.password = "secret"

            with open(os.devnull, "w", encoding="utf-8") as devnull, redirect_stdout(devnull):
                self.assertEqual(cloud.fetch_cloud_mesh(args), 0)

            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["ivIndex"], "0000002A")
            self.assertEqual(server.requests[1]["authorization"], "Bearer mixed-token")

    def test_fetches_default_home_list_and_sync_data_then_selects_candidate(self):
        with tempfile.TemporaryDirectory() as temp_dir, MockCloudServer(
            {
                "/app/voice/homeList": {"code": 200, "data": [{"meshJson": json.dumps(sample_mesh("0000002A"))}]},
                "/app/homeSource/syncData": {"code": 200, "data": {"info": [{"meshJson": json.dumps(sample_mesh("0000002B"))}]}},
            }
        ) as server:
            output = Path(temp_dir) / "mesh.json"
            args = args_for(server.base_url, output=str(output), endpoint=None, candidate=2)

            with open(os.devnull, "w", encoding="utf-8") as devnull, redirect_stdout(devnull):
                self.assertEqual(cloud.fetch_cloud_mesh(args), 0)

            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["ivIndex"], "0000002B")
            self.assertEqual([request["path"] for request in server.requests], ["/app/voice/homeList", "/app/homeSource/syncData"])

    def test_fetches_mesh_json_by_discovered_home_id(self):
        with tempfile.TemporaryDirectory() as temp_dir, MockCloudServer(
            {
                "/app/voice/homeList": {"code": 200, "data": [{"homeId": "home-1", "homeName": "Studio"}]},
                "/app/homeSource/getMeshJsonByHomeId": {
                    "code": 200,
                    "data": {"meshJson": json.dumps(sample_mesh("0000002C"))},
                },
                "/app/homeSource/syncData": {"code": 200, "data": {"info": []}},
            }
        ) as server:
            output = Path(temp_dir) / "mesh.json"
            report_output = Path(temp_dir) / "report.json"
            args = args_for(server.base_url, output=str(output), endpoint=None, report_output=str(report_output))

            with open(os.devnull, "w", encoding="utf-8") as devnull, redirect_stdout(devnull):
                self.assertEqual(cloud.fetch_cloud_mesh(args), 0)

            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["ivIndex"], "0000002C")
            report = json.loads(report_output.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "written")
            self.assertEqual(report["home_count"], 1)
            self.assertEqual(report["homes"][0]["home_id"], "home-1")
            self.assertEqual(report["homes"][0]["name"], "Studio")
            self.assertEqual(report["homes"][0]["source"], "home-list")
            self.assertEqual(report["requested_home_ids"], [])
            self.assertEqual(
                [request["path"] for request in server.requests],
                ["/app/voice/homeList", "/app/homeSource/getMeshJsonByHomeId", "/app/homeSource/syncData"],
            )
            self.assertEqual(
                json.loads(server.requests[1]["body"].decode("utf-8")),
                {"homeId": "home-1"},
            )

    def test_writes_home_list_mesh_when_later_default_endpoints_fail(self):
        with tempfile.TemporaryDirectory() as temp_dir, MockCloudServer(
            {
                "/app/voice/homeList": {
                    "code": 200,
                    "data": [
                        {
                            "homeId": "home-1",
                            "homeName": "Studio",
                            "meshJson": json.dumps(sample_mesh("0000002E")),
                        }
                    ],
                },
                "/app/homeSource/getMeshJsonByHomeId": {"_http_status": 500, "msg": "server down"},
                "/app/homeSource/syncData": {"_http_status": 502, "msg": "gateway down"},
            }
        ) as server:
            output = Path(temp_dir) / "mesh.json"
            report_output = Path(temp_dir) / "report.json"
            args = args_for(server.base_url, output=str(output), endpoint=None, report_output=str(report_output))

            with open(os.devnull, "w", encoding="utf-8") as devnull, redirect_stdout(devnull), redirect_stderr(devnull):
                self.assertEqual(cloud.fetch_cloud_mesh(args), 0)

            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["ivIndex"], "0000002E")
            report = json.loads(report_output.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "written")
            self.assertEqual(report["candidate_count"], 1)
            self.assertEqual(report["endpoint_error_count"], 2)
            self.assertEqual(
                [entry["endpoint"] for entry in report["endpoint_errors"]],
                ["mesh-json-by-home-id:home-1", "sync-data"],
            )
            self.assertEqual(
                [request["path"] for request in server.requests],
                ["/app/voice/homeList", "/app/homeSource/getMeshJsonByHomeId", "/app/homeSource/syncData"],
            )

    def test_later_default_endpoint_can_supply_mesh_after_home_list_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir, MockCloudServer(
            {
                "/app/voice/homeList": {"_http_status": 500, "msg": "server down"},
                "/app/homeSource/syncData": {"code": 200, "data": {"info": [{"meshJson": json.dumps(sample_mesh("0000002F"))}]}},
            }
        ) as server:
            output = Path(temp_dir) / "mesh.json"
            report_output = Path(temp_dir) / "report.json"
            args = args_for(server.base_url, output=str(output), endpoint=None, report_output=str(report_output))

            with open(os.devnull, "w", encoding="utf-8") as devnull, redirect_stdout(devnull), redirect_stderr(devnull):
                self.assertEqual(cloud.fetch_cloud_mesh(args), 0)

            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["ivIndex"], "0000002F")
            report = json.loads(report_output.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "written")
            self.assertEqual(report["endpoint_error_count"], 2)
            self.assertEqual(
                [entry["endpoint"] for entry in report["endpoint_errors"]],
                ["home-list", "mesh-json-by-home-id"],
            )
            self.assertEqual(
                [request["path"] for request in server.requests],
                ["/app/voice/homeList", "/app/homeSource/syncData"],
            )

    def test_fetches_mesh_json_by_explicit_home_id(self):
        with tempfile.TemporaryDirectory() as temp_dir, MockCloudServer(
            {
                "/app/homeSource/getMeshJsonByHomeId": {
                    "code": 200,
                    "data": {"meshJson": json.dumps(sample_mesh("0000002D"))},
                },
            }
        ) as server:
            output = Path(temp_dir) / "mesh.json"
            report_output = Path(temp_dir) / "report.json"
            args = args_for(
                server.base_url,
                output=str(output),
                endpoint=["mesh-json-by-home-id"],
                report_output=str(report_output),
            )
            args.home_id = ["home-explicit"]

            with open(os.devnull, "w", encoding="utf-8") as devnull, redirect_stdout(devnull):
                self.assertEqual(cloud.fetch_cloud_mesh(args), 0)

            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["ivIndex"], "0000002D")
            report = json.loads(report_output.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "written")
            self.assertEqual(report["requested_home_ids"], ["home-explicit"])
            self.assertEqual(report["home_count"], 0)
            self.assertEqual([request["path"] for request in server.requests], ["/app/homeSource/getMeshJsonByHomeId"])
            self.assertEqual(
                json.loads(server.requests[0]["body"].decode("utf-8")),
                {"homeId": "home-explicit"},
            )

    def test_mesh_json_by_home_id_without_home_id_reports_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report_output = Path(temp_dir) / "report.json"
            args = args_for(
                "http://127.0.0.1:9",
                output=str(Path(temp_dir) / "mesh.json"),
                endpoint=["mesh-json-by-home-id"],
                report_output=str(report_output),
            )

            with open(os.devnull, "w", encoding="utf-8") as devnull, redirect_stdout(devnull), redirect_stderr(devnull):
                self.assertEqual(cloud.fetch_cloud_mesh(args), 1)

            report = json.loads(report_output.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "endpoint-fetch-failed")
            self.assertIn("needs --home-id", report["error"])

    def test_requires_candidate_number_when_multiple_meshes_exist(self):
        with tempfile.TemporaryDirectory() as temp_dir, MockCloudServer(
            {
                "/app/voice/homeList": {"code": 200, "data": [{"meshJson": json.dumps(sample_mesh("0000002A"))}]},
                "/app/homeSource/syncData": {"code": 200, "data": {"info": [{"meshJson": json.dumps(sample_mesh("0000002B"))}]}},
            }
        ) as server:
            output = Path(temp_dir) / "mesh.json"
            args = args_for(server.base_url, output=str(output), endpoint=None)

            with open(os.devnull, "w", encoding="utf-8") as devnull, redirect_stdout(devnull), redirect_stderr(devnull):
                self.assertEqual(cloud.fetch_cloud_mesh(args), 2)
            self.assertFalse(output.exists())

    def test_reads_token_from_file_and_can_save_raw_response(self):
        with tempfile.TemporaryDirectory() as temp_dir, MockCloudServer(
            {
                "/app/voice/homeList": {"code": 200, "data": [{"meshJson": json.dumps(sample_mesh())}]},
            }
        ) as server:
            root = Path(temp_dir)
            token_file = root / "token.txt"
            raw_output = root / "raw.json"
            output = root / "mesh.json"
            token_file.write_text("Bearer file-token\n", encoding="utf-8")
            args = args_for(server.base_url, output=str(output), endpoint=["home-list"], token="")
            args.token_file = str(token_file)
            args.raw_output = str(raw_output)

            with open(os.devnull, "w", encoding="utf-8") as devnull, redirect_stdout(devnull):
                self.assertEqual(cloud.fetch_cloud_mesh(args), 0)

            self.assertTrue(raw_output.exists())
            self.assertEqual(server.requests[0]["authorization"], "Bearer file-token")

    def test_writes_key_free_cloud_fetch_report(self):
        with tempfile.TemporaryDirectory() as temp_dir, MockCloudServer(
            {
                "/app/voice/homeList": {"code": 200, "data": [{"meshJson": json.dumps(sample_mesh())}]},
            }
        ) as server:
            root = Path(temp_dir)
            output = root / "mesh.json"
            report_output = root / "report.json"
            args = args_for(
                server.base_url,
                output=str(output),
                endpoint=["home-list"],
                report_output=str(report_output),
            )

            with open(os.devnull, "w", encoding="utf-8") as devnull, redirect_stdout(devnull):
                self.assertEqual(cloud.fetch_cloud_mesh(args), 0)

            report_text = report_output.read_text(encoding="utf-8")
            report = json.loads(report_text)
            self.assertEqual(report["status"], "written")
            self.assertEqual(report["region"], "europe")
            self.assertEqual(report["requested_home_ids"], [])
            self.assertEqual(report["home_count"], 0)
            self.assertEqual(report["homes"], [])
            self.assertEqual(report["candidate_count"], 1)
            self.assertEqual(report["selected_candidate"], 1)
            self.assertEqual(report["output"], str(output))
            self.assertEqual(report["endpoints"], ["home-list"])
            self.assertIn("Skylight", "\n".join(report["candidates"][0]["summary"]))
            self.assertNotIn("00112233445566778899AABBCCDDEEFF", report_text)
            self.assertNotIn("FFEEDDCCBBAA99887766554433221100", report_text)

    def test_report_records_candidate_selection_failure_without_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir, MockCloudServer(
            {
                "/app/voice/homeList": {"code": 200, "data": [{"meshJson": json.dumps(sample_mesh("0000002A"))}]},
                "/app/homeSource/syncData": {"code": 200, "data": {"info": [{"meshJson": json.dumps(sample_mesh("0000002B"))}]}},
            }
        ) as server:
            root = Path(temp_dir)
            output = root / "mesh.json"
            report_output = root / "report.json"
            args = args_for(server.base_url, output=str(output), endpoint=None, report_output=str(report_output))

            with open(os.devnull, "w", encoding="utf-8") as devnull, redirect_stdout(devnull), redirect_stderr(devnull):
                self.assertEqual(cloud.fetch_cloud_mesh(args), 2)

            report_text = report_output.read_text(encoding="utf-8")
            report = json.loads(report_text)
            self.assertEqual(report["status"], "candidate-selection-failed")
            self.assertEqual(report["candidate_count"], 2)
            self.assertIsNone(report["selected_candidate"])
            self.assertIn("Multiple mesh candidates found", report["error"])
            self.assertNotIn("112233445566778899AABBCCDDEEFF00", report_text)

    def test_report_records_missing_credentials_without_secret_words(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_output = root / "report.json"
            args = args_for("http://127.0.0.1:9", output=str(root / "mesh.json"), token="", report_output=str(report_output))

            with open(os.devnull, "w", encoding="utf-8") as devnull, redirect_stdout(devnull), redirect_stderr(devnull):
                self.assertEqual(cloud.fetch_cloud_mesh(args), 2)

            report_text = report_output.read_text(encoding="utf-8")
            report = json.loads(report_text)
            self.assertEqual(report["status"], "credentials-missing")
            self.assertEqual(report["candidate_count"], 0)
            self.assertEqual(report["error"], "<redacted sensitive cloud error>")
            self.assertNotIn("PESETECH_TEST_TOKEN", report_text)
            self.assertNotIn("PESETECH_TEST_PASSWORD", report_text)

    def test_report_records_login_failure_without_secrets(self):
        with tempfile.TemporaryDirectory() as temp_dir, MockCloudServer(
            {
                "/app/customer-login/login": {"code": 401, "msg": "bad password token"},
            }
        ) as server:
            root = Path(temp_dir)
            report_output = root / "report.json"
            args = args_for(server.base_url, output=str(root / "mesh.json"), endpoint=["home-list"], token="", report_output=str(report_output))
            args.username = "person@example.com"
            args.password = "secret-password"

            with open(os.devnull, "w", encoding="utf-8") as devnull, redirect_stdout(devnull), redirect_stderr(devnull):
                self.assertEqual(cloud.fetch_cloud_mesh(args), 2)

            report_text = report_output.read_text(encoding="utf-8")
            report = json.loads(report_text)
            self.assertEqual(report["status"], "credential-or-login-failed")
            self.assertEqual(report["error"], "<redacted sensitive cloud error>")
            self.assertNotIn("secret-password", report_text)
            self.assertNotIn("person@example.com", report_text)

    def test_report_records_endpoint_fetch_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir, MockCloudServer(
            {
                "/app/voice/homeList": {"_http_status": 500, "msg": "server down"},
            }
        ) as server:
            root = Path(temp_dir)
            report_output = root / "report.json"
            args = args_for(server.base_url, output=str(root / "mesh.json"), endpoint=["home-list"], report_output=str(report_output))

            with open(os.devnull, "w", encoding="utf-8") as devnull, redirect_stdout(devnull), redirect_stderr(devnull):
                self.assertEqual(cloud.fetch_cloud_mesh(args), 1)

            report = json.loads(report_output.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "endpoint-fetch-failed")
            self.assertEqual(report["endpoints"], ["home-list"])
            self.assertIn("/app/voice/homeList returned HTTP 500", report["error"])

    def test_reads_token_from_environment(self):
        old_value = os.environ.get("PESETECH_TEST_TOKEN")
        os.environ["PESETECH_TEST_TOKEN"] = "env-token"
        try:
            args = args_for("http://127.0.0.1:9", token="")
            self.assertEqual(cloud.read_token(args), "env-token")
        finally:
            if old_value is None:
                os.environ.pop("PESETECH_TEST_TOKEN", None)
            else:
                os.environ["PESETECH_TEST_TOKEN"] = old_value


if __name__ == "__main__":
    unittest.main()
