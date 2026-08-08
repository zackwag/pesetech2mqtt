import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from support import install_stubs


STUBS = install_stubs()

from app import gateway


def node_files():
    node_uuid = "00112233-4455-6677-8899-aabbccddeeff"
    config = {
        "mesh": {
            "skylight_a": {
                "uuid": node_uuid,
                "name": "Skylight A",
                "default_entity_id": "light.skylight_a",
                "type": "pesetech_skylight",
            }
        }
    }
    store = {
        "keychain": {
            "device_key": "00" * 16,
            "network_key": "11" * 16,
            "network_key_index": 0,
            "app_key": "22" * 16,
            "app_key_index": 0,
            "app_key_bound_net_key_index": 0,
        },
        "local": {"address": 1, "iv_index": 42},
        "nodes": {
            node_uuid: {
                "type": "pesetech_skylight",
                "unicast": 0x0200,
                "count": 3,
                "imported_models": {"1000": 0x0200, "1300": 0x0200, "1303": 0x0201, "1306": 0x0202},
            }
        },
        "remote_nodes": {node_uuid: {"unicast": 0x0200, "count": 3, "device_key": "33" * 16}},
    }
    return config, store


class Operation:
    def __init__(self, error=None):
        self.error = error

    def __await__(self):
        async def run():
            if self.error:
                raise self.error
        return run().__await__()


class GatewayTest(unittest.IsolatedAsyncioTestCase):
    async def test_loads_existing_files_without_rewriting_them(self):
        config, store = node_files()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.yaml"
            store_path = root / "store.yaml"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            store_path.write_text(json.dumps(store), encoding="utf-8")
            before = (config_path.read_bytes(), store_path.read_bytes())

            app = gateway.PesetechGateway(asyncio.get_running_loop(), root)

            self.assertEqual([node.id for node in app.nodes], ["skylight_a"])
            self.assertEqual(before, (config_path.read_bytes(), store_path.read_bytes()))

    def test_missing_required_model_is_a_startup_error(self):
        config, store = node_files()
        del store["nodes"][next(iter(store["nodes"]))]["imported_models"]["1306"]
        with self.assertRaisesRegex(ValueError, "missing required imported models: 1306"):
            gateway.load_nodes(config, store)

    async def test_only_exact_bluez_already_exists_is_accepted(self):
        app = object.__new__(gateway.PesetechGateway)

        await app._import("node", Operation(STUBS.DBusError(gateway.ALREADY_EXISTS, "exists")))

        with self.assertRaises(STUBS.DBusError):
            await app._import("node", Operation(STUBS.DBusError("org.bluez.mesh.Error.Failed", "failed")))
        with self.assertRaises(RuntimeError):
            await app._import("node", Operation(RuntimeError("failed")))


if __name__ == "__main__":
    unittest.main()
