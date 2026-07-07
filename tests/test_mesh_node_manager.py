import sys
import types
import unittest
import uuid
from pathlib import Path


GATEWAY_DIR = Path(__file__).resolve().parents[1] / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

yaml = types.ModuleType("yaml")
yaml.safe_load = lambda stream: {}
sys.modules["yaml"] = yaml

from mesh.manager import NodeManager
from tools import Config


class FakeStore:
    def __init__(self, data=None):
        self.data = data or {}
        self.persisted = False

    def items(self):
        return self.data.items()

    def reset(self):
        self.data.clear()

    def set(self, key, value):
        self.data[key] = value

    def persist(self):
        self.persisted = True


class BaseFakeNode:
    def __init__(self, uuid, type, unicast, count, configured=False, config=None):
        self.uuid = uuid
        self.type = type
        self.unicast = unicast
        self.count = count
        self.configured = configured
        self.config = config

    def yaml(self):
        return {
            "type": self.type,
            "unicast": self.unicast,
            "count": self.count,
            "configured": self.configured,
        }


class GenericNode(BaseFakeNode):
    pass


class PesetechNode(BaseFakeNode):
    pass


class MeshNodeManagerTest(unittest.TestCase):
    def test_create_applies_matching_config_to_newly_provisioned_node(self):
        node_uuid = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")
        store = FakeStore()
        config = Config(
            config={
                "mesh": {
                    "skylight": {
                        "uuid": str(node_uuid),
                        "name": "Pesetech Skylight",
                        "type": "pesetech_skylight",
                    }
                }
            }
        )
        manager = NodeManager(store, config, {"generic": GenericNode, "pesetech_skylight": PesetechNode})

        manager.create(node_uuid, {"type": "generic", "unicast": 4, "count": 3})
        node = manager.get(node_uuid)

        self.assertIsInstance(node, PesetechNode)
        self.assertEqual(node.type, "pesetech_skylight")
        self.assertEqual(node.config.require("id"), "skylight")
        self.assertEqual(node.config.require("name"), "Pesetech Skylight")

        manager.persist()

        self.assertTrue(store.persisted)
        self.assertEqual(store.data[str(node_uuid)]["type"], "pesetech_skylight")

    def test_create_matches_config_uuid_case_insensitively(self):
        node_uuid = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")
        store = FakeStore()
        config = Config(
            config={
                "mesh": {
                    "skylight": {
                        "uuid": "00112233-4455-6677-8899-AABBCCDDEEFF",
                        "name": "Pesetech Skylight",
                        "type": "pesetech_skylight",
                    }
                }
            }
        )
        manager = NodeManager(store, config, {"generic": GenericNode, "pesetech_skylight": PesetechNode})

        manager.create(node_uuid, {"type": "generic", "unicast": 4, "count": 3})
        node = manager.get(node_uuid)

        self.assertIsInstance(node, PesetechNode)
        self.assertEqual(node.config.require("id"), "skylight")

    def test_existing_store_node_matches_config_uuid_case_insensitively(self):
        node_uuid = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")
        store = FakeStore(
            {
                str(node_uuid): {
                    "type": "generic",
                    "unicast": 4,
                    "count": 3,
                    "configured": True,
                }
            }
        )
        config = Config(
            config={
                "mesh": {
                    "skylight": {
                        "uuid": "00112233-4455-6677-8899-AABBCCDDEEFF",
                        "name": "Pesetech Skylight",
                        "type": "pesetech_skylight",
                    }
                }
            }
        )

        manager = NodeManager(store, config, {"generic": GenericNode, "pesetech_skylight": PesetechNode})
        node = manager.get(node_uuid)

        self.assertIsInstance(node, PesetechNode)
        self.assertEqual(node.type, "pesetech_skylight")
        self.assertEqual(node.config.require("id"), "skylight")

    def test_malformed_config_uuid_is_ignored_for_node_matching(self):
        node_uuid = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")
        store = FakeStore()
        config = Config(
            config={
                "mesh": {
                    "skylight": {
                        "uuid": "not-a-uuid",
                        "type": "pesetech_skylight",
                    }
                }
            }
        )
        manager = NodeManager(store, config, {"generic": GenericNode, "pesetech_skylight": PesetechNode})

        manager.create(node_uuid, {"type": "generic", "unicast": 4, "count": 3})
        node = manager.get(node_uuid)

        self.assertIsInstance(node, GenericNode)
        self.assertEqual(node.type, "generic")


if __name__ == "__main__":
    unittest.main()
