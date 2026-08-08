import json
import tempfile
import unittest
from pathlib import Path

from support import install_stubs


install_stubs()

from app import import_mesh


def light(node_uuid, name, unicast):
    return {
        "UUID": node_uuid,
        "name": name,
        "unicastAddress": f"{unicast:04X}",
        "deviceKey": "ffeeddccbbaa99887766554433221100",
        "appKeys": [{"index": 0}],
        "elements": [
            {"index": 0, "models": [{"modelId": "1000"}, {"modelId": "1300"}, {"modelId": "1303"}]},
            {"index": 1, "models": []},
            {"index": 2, "models": [{"modelId": "1306"}]},
        ],
    }


def mesh(nodes):
    provisioner_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    return {
        "ivIndex": "0000002A",
        "netKeys": [{"index": 0, "key": "00112233445566778899aabbccddeeff"}],
        "appKeys": [{"index": 0, "boundNetKey": 0, "key": "112233445566778899aabbccddeeff00"}],
        "provisioners": [{"UUID": provisioner_uuid}],
        "nodes": [
            {
                "UUID": provisioner_uuid,
                "unicastAddress": "0001",
                "deviceKey": "00" * 16,
                "elements": [{"index": 0, "models": []}],
            },
            *nodes,
            {
                "UUID": "99999999-8888-7777-6666-555555555555",
                "name": "Smart Pad",
                "unicastAddress": "0100",
                "deviceKey": "11" * 16,
                "elements": [{"index": 0, "models": [{"modelId": "1000"}]}],
            },
        ],
    }


class ImportMeshTest(unittest.TestCase):
    def test_imports_all_skylights_and_uses_real_names(self):
        storage = mesh(
            [
                light("00112233-4455-6677-8899-aabbccddeeff", "Skylight A", 0x0222),
                light("11111111-2222-3333-4444-555555555555", "Skylight B", 0x0333),
            ]
        )

        config, store = import_mesh.build_files(storage)

        self.assertEqual(set(config["mesh"]), {"skylight_a", "skylight_b"})
        self.assertEqual(config["mesh"]["skylight_a"]["default_entity_id"], "light.skylight_a")
        self.assertEqual(config["mesh"]["skylight_b"]["name"], "Skylight B")
        self.assertEqual(len(store["nodes"]), 2)
        self.assertEqual(store["nodes"]["00112233-4455-6677-8899-aabbccddeeff"]["imported_models"]["1306"], 0x0224)

    def test_blank_and_common_names_use_unicast(self):
        config, _ = import_mesh.build_files(
            mesh(
                [
                    light("00112233-4455-6677-8899-aabbccddeeff", "", 0x0020),
                    light("11111111-2222-3333-4444-555555555555", "Common Node", 0x0030),
                ]
            )
        )
        self.assertEqual(set(config["mesh"]), {"skylight_0020", "skylight_0030"})
        self.assertEqual(config["mesh"]["skylight_0020"]["name"], "Pesetech Skylight 0020")

    def test_duplicate_names_get_deterministic_unicast_suffixes(self):
        config, _ = import_mesh.build_files(
            mesh(
                [
                    light("00112233-4455-6677-8899-aabbccddeeff", "Skylight", 0x0020),
                    light("11111111-2222-3333-4444-555555555555", "Skylight", 0x0030),
                ]
            )
        )
        self.assertEqual(set(config["mesh"]), {"skylight_0020", "skylight_0030"})

    def test_multiple_mesh_objects_fail_plainly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mesh.json"
            path.write_text(json.dumps({"homes": [mesh([]), mesh([])]}), encoding="utf-8")
            with self.assertRaisesRegex(import_mesh.MeshImportError, "Multiple Telink MeshStorage"):
                import_mesh.load_mesh_storage(path)

    def test_ensure_data_covers_existing_first_run_missing_and_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "mesh.json"
            config = root / "config.yaml"
            store = root / "store.yaml"

            with self.assertRaisesRegex(import_mesh.MeshImportError, "First start requires"):
                import_mesh.ensure_data(source, config, store)

            source.write_text(
                json.dumps(mesh([light("00112233-4455-6677-8899-aabbccddeeff", "Skylight A", 0x0020)])),
                encoding="utf-8",
            )
            self.assertTrue(import_mesh.ensure_data(source, config, store))
            before = (config.read_bytes(), store.read_bytes())
            self.assertFalse(import_mesh.ensure_data(source, config, store))
            self.assertEqual(before, (config.read_bytes(), store.read_bytes()))

            store.unlink()
            with self.assertRaisesRegex(import_mesh.MeshImportError, "both exist or both be absent"):
                import_mesh.ensure_data(source, config, store)


if __name__ == "__main__":
    unittest.main()
