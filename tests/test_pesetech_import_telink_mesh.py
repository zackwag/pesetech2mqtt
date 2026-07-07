import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "pesetech_import_telink_mesh.py"
)
spec = importlib.util.spec_from_file_location("pesetech_import_telink_mesh", SCRIPT_PATH)
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


def sample_mesh(nodes=None, net_key_index=0, app_key_index=0, bound_net_key=None):
    if bound_net_key is None:
        bound_net_key = net_key_index

    return {
        "ivIndex": "0000002A",
        "sequenceNumber": 17,
        "netKeys": [
            {
                "index": net_key_index,
                "key": "00112233445566778899AABBCCDDEEFF",
            }
        ],
        "appKeys": [
            {
                "index": app_key_index,
                "boundNetKey": bound_net_key,
                "key": "112233445566778899AABBCCDDEEFF00",
            }
        ],
        "provisioners": [
            {
                "UUID": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "allocatedUnicastRange": [{"lowAddress": "0001", "highAddress": "0400"}],
            }
        ],
        "nodes": nodes
        or [
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
                "configComplete": True,
                "appKeys": [{"index": app_key_index, "updated": False}],
                "elements": [
                    {"index": 0, "models": [{"modelId": "1000"}, {"modelId": "1300"}, {"modelId": "1303"}]},
                    {"index": 1, "models": []},
                    {"index": 2, "models": [{"modelId": "1306"}]},
                ],
            },
        ],
    }


class PesetechImportTelinkMeshTest(unittest.TestCase):
    def test_selects_ctl_temperature_node_and_builds_gateway_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mesh_path = root / "mesh.json"
            config_path = root / "config.yaml"
            store_path = root / "store.yaml"
            mesh_path.write_text(json.dumps(sample_mesh()), encoding="utf-8")
            config_path.write_text("mqtt:\n  broker: homeassistant.local\nmesh: {}\n", encoding="utf-8")
            args = importer.argparse.Namespace(
                mesh_json=str(mesh_path),
                config=str(config_path),
                store=str(store_path),
                device_id="skylight",
                device_name="Pesetech Skylight",
                node_uuid=None,
                node_unicast=None,
                local_address=None,
                force=False,
                dry_run=False,
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = importer.import_telink_mesh(args)

            self.assertEqual(exit_code, 0)
            self.assertIn("selected node: 00112233-4455-6677-8899-aabbccddeeff at 0002", output.getvalue())
            config = importer.load_yaml(config_path)
            store = importer.load_yaml(store_path)
            self.assertEqual(config["mqtt"]["broker"], "homeassistant.local")
            self.assertEqual(config["mesh"]["skylight"]["uuid"], "00112233-4455-6677-8899-aabbccddeeff")
            self.assertEqual(config["mesh"]["skylight"]["default_entity_id"], "light.skylight")
            self.assertEqual(store["keychain"]["network_key"], "00112233445566778899aabbccddeeff")
            self.assertEqual(store["keychain"]["network_key_index"], 0)
            self.assertEqual(store["keychain"]["app_key"], "112233445566778899aabbccddeeff00")
            self.assertEqual(store["keychain"]["app_key_index"], 0)
            self.assertEqual(store["keychain"]["app_key_bound_net_key_index"], 0)
            self.assertEqual(store["local"]["iv_index"], 42)
            self.assertNotIn(store["local"]["address"], {1, 2, 3, 4})
            self.assertEqual(store["nodes"]["00112233-4455-6677-8899-aabbccddeeff"]["count"], 3)
            self.assertEqual(
                store["remote_nodes"]["00112233-4455-6677-8899-aabbccddeeff"]["device_key"],
                "ffeeddccbbaa99887766554433221100",
            )

    def test_preserves_nonzero_key_indexes_from_telink_mesh(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mesh_path = root / "mesh.json"
            config_path = root / "config.yaml"
            store_path = root / "store.yaml"
            mesh_path.write_text(json.dumps(sample_mesh(net_key_index=7, app_key_index=5)), encoding="utf-8")
            args = importer.argparse.Namespace(
                mesh_json=str(mesh_path),
                config=str(config_path),
                store=str(store_path),
                device_id="skylight",
                device_name="Pesetech Skylight",
                node_uuid=None,
                node_unicast=None,
                local_address=None,
                force=False,
                dry_run=False,
            )
            output = io.StringIO()

            with redirect_stdout(output):
                self.assertEqual(importer.import_telink_mesh(args), 0)

            store = importer.load_yaml(store_path)
            self.assertEqual(store["keychain"]["network_key_index"], 7)
            self.assertEqual(store["keychain"]["app_key_index"], 5)
            self.assertEqual(store["keychain"]["app_key_bound_net_key_index"], 7)
            self.assertIn("net key index: 7", output.getvalue())
            self.assertIn("app key index: 5", output.getvalue())

    def test_can_override_imported_default_entity_id(self):
        config = importer.update_config(
            {},
            sample_mesh()["nodes"][1],
            "skylight",
            "Pesetech Skylight",
            "light.sunroom_sky",
        )

        self.assertEqual(config["mesh"]["skylight"]["default_entity_id"], "light.sunroom_sky")

    def test_rejects_non_light_default_entity_id(self):
        with self.assertRaisesRegex(importer.ImportErrorWithDetail, "default_entity_id must be a Home Assistant light entity id"):
            importer.update_config(
                {},
                sample_mesh()["nodes"][1],
                "skylight",
                "Pesetech Skylight",
                "switch.skylight",
            )

    def test_refuses_node_app_key_index_missing_from_mesh_app_keys(self):
        mesh = sample_mesh(app_key_index=5)
        mesh["nodes"][1]["appKeys"] = [{"index": 6, "updated": False}]

        with self.assertRaises(importer.ImportErrorWithDetail) as context:
            importer.selected_keys(mesh, mesh["nodes"][1])

        self.assertIn("bound to app key index(es) 6", str(context.exception))

    def test_requires_explicit_node_when_multiple_ctl_temperature_nodes_exist(self):
        mesh = sample_mesh()
        mesh["nodes"].append(
            {
                "UUID": "11111111-2222-3333-4444-555555555555",
                "name": "Second",
                "unicastAddress": "0010",
                "deviceKey": "1234567890ABCDEF1234567890ABCDEF",
                "elements": [{"index": 0, "models": [{"modelId": "1306"}]}],
            }
        )

        with self.assertRaises(importer.ImportErrorWithDetail) as context:
            importer.select_node(mesh)

        self.assertIn("--node-uuid", str(context.exception))

    def test_loads_mesh_json_string_wrapper_from_home_response(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mesh_path = root / "home_response.json"
            mesh_path.write_text(
                json.dumps(
                    {
                        "homeId": "home-1",
                        "homeName": "Office",
                        "meshJson": json.dumps(sample_mesh()),
                    }
                ),
                encoding="utf-8",
            )

            storage = importer.load_mesh_storage(mesh_path)

            self.assertEqual(storage["nodes"][1]["UUID"], "00112233-4455-6677-8899-aabbccddeeff")

    def test_loads_json_node_wrapper_from_upload_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mesh_path = root / "upload_payload.json"
            mesh_path.write_text(json.dumps({"homeId": "home-1", "jsonNode": sample_mesh()}), encoding="utf-8")

            storage = importer.load_mesh_storage(mesh_path)

            self.assertEqual(storage["netKeys"][0]["key"], "00112233445566778899AABBCCDDEEFF")

    def test_imports_single_sync_data_info_wrapper(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mesh_path = root / "sync_data_response.json"
            config_path = root / "config.yaml"
            store_path = root / "store.yaml"
            mesh_path.write_text(
                json.dumps(
                    {
                        "code": 200,
                        "data": {
                            "info": [
                                {
                                    "homeId": "home-1",
                                    "homeName": "Office",
                                    "meshJson": json.dumps(sample_mesh()),
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            args = importer.argparse.Namespace(
                mesh_json=str(mesh_path),
                config=str(config_path),
                store=str(store_path),
                device_id="skylight",
                device_name="Pesetech Skylight",
                node_uuid=None,
                node_unicast=None,
                local_address=None,
                force=False,
                dry_run=False,
            )

            self.assertEqual(importer.import_telink_mesh(args), 0)
            store = importer.load_yaml(store_path)
            self.assertEqual(
                store["remote_nodes"]["00112233-4455-6677-8899-aabbccddeeff"]["device_key"],
                "ffeeddccbbaa99887766554433221100",
            )

    def test_refuses_multiple_mesh_storage_candidates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mesh_path = root / "multiple_homes.json"
            first = sample_mesh()
            first["nodes"][1]["name"] = "Office Skylight"
            second = sample_mesh()
            second["nodes"][1]["UUID"] = "11111111-2222-3333-4444-555555555555"
            second["nodes"][1]["name"] = "Kitchen Skylight"
            second["nodes"][1]["unicastAddress"] = "0010"
            mesh_path.write_text(
                json.dumps(
                    {
                        "data": {
                            "info": [
                                {"homeId": "home-1", "meshJson": json.dumps(first)},
                                {"homeId": "home-2", "meshJson": json.dumps(second)},
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(importer.ImportErrorWithDetail) as context:
                importer.load_mesh_storage(mesh_path)

            error = str(context.exception)
            self.assertIn("Multiple Telink MeshStorage objects", error)
            self.assertIn("--mesh-candidate N", error)
            self.assertIn("1. root.data.info[0].meshJson<json>", error)
            self.assertIn("2. root.data.info[1].meshJson<json>", error)
            self.assertIn("counts: netKeys=1, appKeys=1, nodes=2", error)
            self.assertIn("likely CTL light nodes", error)
            self.assertIn("Office Skylight", error)
            self.assertIn("Kitchen Skylight", error)
            self.assertNotIn("00112233445566778899aabbccddeeff", error.lower())

    def test_selects_mesh_storage_candidate_from_multi_home_response(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mesh_path = root / "multiple_homes.json"
            config_path = root / "config.yaml"
            store_path = root / "store.yaml"
            first = sample_mesh()
            second = sample_mesh()
            second["nodes"][1]["UUID"] = "11111111-2222-3333-4444-555555555555"
            second["nodes"][1]["name"] = "Kitchen Skylight"
            second["nodes"][1]["unicastAddress"] = "0010"
            mesh_path.write_text(
                json.dumps(
                    {
                        "data": {
                            "info": [
                                {"homeId": "home-1", "meshJson": json.dumps(first)},
                                {"homeId": "home-2", "meshJson": json.dumps(second)},
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = importer.argparse.Namespace(
                mesh_json=str(mesh_path),
                config=str(config_path),
                store=str(store_path),
                device_id="kitchen_sky",
                device_name="Kitchen Skylight",
                mesh_candidate=2,
                node_uuid=None,
                node_unicast=None,
                local_address=None,
                force=False,
                dry_run=False,
            )
            output = io.StringIO()

            with redirect_stdout(output):
                self.assertEqual(importer.import_telink_mesh(args), 0)

            config = importer.load_yaml(config_path)
            store = importer.load_yaml(store_path)
            self.assertIn("mesh candidate: 2", output.getvalue())
            self.assertEqual(config["mesh"]["kitchen_sky"]["uuid"], "11111111-2222-3333-4444-555555555555")
            self.assertEqual(store["nodes"]["11111111-2222-3333-4444-555555555555"]["unicast"], 16)

    def test_rejects_mesh_storage_candidate_out_of_range(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mesh_path = root / "mesh.json"
            mesh_path.write_text(json.dumps(sample_mesh()), encoding="utf-8")

            with self.assertRaises(importer.ImportErrorWithDetail) as context:
                importer.load_mesh_storage(mesh_path, mesh_candidate=2)

            self.assertIn("--mesh-candidate must be between 1 and 1", str(context.exception))

    def test_refuses_to_overwrite_existing_store_without_force(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store_path = root / "store.yaml"
            store_path.write_text("keychain:\n  network_key: old\n", encoding="utf-8")

            with self.assertRaises(importer.ImportErrorWithDetail):
                importer.ensure_no_live_store(store_path, force=False)

            importer.ensure_no_live_store(store_path, force=True)

    def test_update_config_recovers_from_null_mesh_mapping(self):
        config = {
            "mqtt": {"broker": "homeassistant.local"},
            "mesh": None,
        }
        node = sample_mesh()["nodes"][1]

        updated = importer.update_config(config, node, "skylight", "Pesetech Skylight")

        self.assertEqual(updated["mqtt"]["broker"], "homeassistant.local")
        self.assertEqual(updated["mesh"]["skylight"]["uuid"], "00112233-4455-6677-8899-aabbccddeeff")

    def test_dry_run_does_not_write_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mesh_path = root / "mesh.json"
            config_path = root / "config.yaml"
            store_path = root / "store.yaml"
            mesh_path.write_text(json.dumps(sample_mesh()), encoding="utf-8")
            args = importer.argparse.Namespace(
                mesh_json=str(mesh_path),
                config=str(config_path),
                store=str(store_path),
                device_id="skylight",
                device_name="Pesetech Skylight",
                node_uuid=None,
                node_unicast=None,
                local_address=None,
                force=False,
                dry_run=True,
            )

            self.assertEqual(importer.import_telink_mesh(args), 0)
            self.assertFalse(config_path.exists())
            self.assertFalse(store_path.exists())

    def test_dry_run_writes_key_free_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mesh_path = root / "mesh.json"
            config_path = root / "config.yaml"
            store_path = root / "store.yaml"
            report_path = root / "import-check.json"
            mesh_path.write_text(json.dumps(sample_mesh()), encoding="utf-8")
            args = importer.argparse.Namespace(
                mesh_json=str(mesh_path),
                config=str(config_path),
                store=str(store_path),
                device_id="skylight",
                device_name="Pesetech Skylight",
                default_entity_id="light.skylight",
                mesh_candidate=0,
                node_uuid=None,
                node_unicast=None,
                local_address=None,
                force=False,
                dry_run=True,
                report_output=str(report_path),
            )

            self.assertEqual(importer.import_telink_mesh(args), 0)

            self.assertFalse(config_path.exists())
            self.assertFalse(store_path.exists())
            report_text = report_path.read_text(encoding="utf-8")
            report = json.loads(report_text)
            self.assertEqual(report["operation"], "import-check")
            self.assertEqual(report["status"], "passed")
            self.assertTrue(report["dry_run"])
            self.assertFalse(report["sent_light_commands"])
            self.assertFalse(report["published_mqtt"])
            self.assertFalse(report["wrote_files"])
            self.assertEqual(report["requested"]["device_id"], "skylight")
            self.assertEqual(report["requested"]["default_entity_id"], "light.skylight")
            self.assertEqual(report["requested"]["mesh_candidate"], 0)
            self.assertEqual(report["selected_node"]["uuid"], "00112233-4455-6677-8899-aabbccddeeff")
            self.assertEqual(report["selected_node"]["unicast"], "0002")
            self.assertEqual(report["selected_node"]["models"], ["1000", "1300", "1303", "1306"])
            self.assertEqual(report["iv_index"], "0000002A")
            self.assertEqual(report["net_key_index"], 0)
            self.assertEqual(report["app_key_index"], 0)
            self.assertNotIn("00112233445566778899aabbccddeeff", report_text)
            self.assertNotIn("112233445566778899aabbccddeeff00", report_text)
            self.assertNotIn("ffeeddccbbaa99887766554433221100", report_text)

    def test_failed_dry_run_writes_key_free_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mesh_path = root / "mesh.json"
            report_path = root / "import-check.json"
            mesh_path.write_text(json.dumps(sample_mesh()), encoding="utf-8")

            exit_code = importer.main(
                [
                    str(mesh_path),
                    "--config",
                    str(root / "config.yaml"),
                    "--store",
                    str(root / "store.yaml"),
                    "--mesh-candidate",
                    "2",
                    "--dry-run",
                    "--report-output",
                    str(report_path),
                ]
            )

            self.assertEqual(exit_code, 1)
            report_text = report_path.read_text(encoding="utf-8")
            report = json.loads(report_text)
            self.assertEqual(report["operation"], "import-check")
            self.assertEqual(report["status"], "failed")
            self.assertIn("--mesh-candidate must be between 1 and 1", report["error"])
            self.assertNotIn("00112233445566778899aabbccddeeff", report_text)
            self.assertNotIn("112233445566778899aabbccddeeff00", report_text)
            self.assertNotIn("ffeeddccbbaa99887766554433221100", report_text)


if __name__ == "__main__":
    unittest.main()
