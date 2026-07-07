import importlib.util
import sys
import types
import unittest
from enum import IntEnum
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "pesetech_runtime_check.py"
)
spec = importlib.util.spec_from_file_location("pesetech_runtime_check", SCRIPT_PATH)
runtime_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime_check)


class FakeLightCTLOpcode(IntEnum):
    LIGHT_CTL_TEMPERATURE_SET_UNACKNOWLEDGED = 0x8265


class FakeLightLightnessOpcode(IntEnum):
    LIGHT_LIGHTNESS_SET_UNACKNOWLEDGED = 0x824D


class FakeAccessMessage:
    @staticmethod
    def build(message):
        if message["opcode"] == FakeLightLightnessOpcode.LIGHT_LIGHTNESS_SET_UNACKNOWLEDGED:
            params = message["params"]
            return b"\x82\x4d" + int(params["lightness"]).to_bytes(2, "little") + bytes([params["tid"]])
        if message["opcode"] != FakeLightCTLOpcode.LIGHT_CTL_TEMPERATURE_SET_UNACKNOWLEDGED:
            raise ValueError("unexpected opcode")
        params = message["params"]
        return (
            b"\x82\x65"
            + int(params["ctl_temperature"]).to_bytes(2, "little")
            + int(params["ctl_delta_uv"]).to_bytes(2, "little")
            + bytes([params["tid"]])
        )


def fake_runtime_modules(missing_method=None, missing_remote_import=False, bad_signature=False):
    bluetooth_mesh = types.ModuleType("bluetooth_mesh")
    application = types.ModuleType("bluetooth_mesh.application")
    models = types.ModuleType("bluetooth_mesh.models")
    interfaces = types.ModuleType("bluetooth_mesh.interfaces")

    class Application:
        async def add_app_key(self, net_key_index, app_key_index, app_key):
            pass

        async def delete_app_key(self, net_key_index, app_key_index):
            pass

    class GenericOnOffServer:
        MODEL_ID = (None, 0x1000)

    class LightLightnessServer:
        MODEL_ID = (None, 0x1300)

    class LightLightnessClient:
        MODEL_ID = (None, 0x1302)

        def get_lightness(self):
            pass

        def repeat(self):
            pass

        def send_app(self):
            pass

        def set_lightness_unack(self):
            pass

        def tid(self):
            pass

    class LightCTLServer:
        MODEL_ID = (None, 0x1303)

    class LightCTLClient:
        MODEL_ID = (None, 0x1305)

        def get_ctl(self):
            pass

        def repeat(self):
            pass

        def send_app(self):
            pass

        def set_ctl_unack(
            self,
            destination,
            app_index,
            ctl_temperature,
            ctl_lightness,
            delay=0.5,
            retransmissions=6,
            send_interval=0.075,
        ):
            pass

        def tid(self):
            pass

    class ConfigClient:
        async def add_app_key(self, destination, net_index, app_key_index, net_key_index, app_key):
            pass

        async def delete_app_key(self, destination, net_index, app_key_index, net_key_index):
            pass

        async def bind_app_key(self, destination, net_index, element_address, app_key_index, model):
            pass

    if bad_signature:
        async def import_app_key_wrong(self, app_index, net_index, app_key):
            pass

    if missing_method:
        delattr(LightCTLClient, missing_method)

    class ManagementInterface:
        def import_subnet(self, net_index, net_key):
            pass

        def import_app_key(self, net_index, app_index, app_key):
            pass

        def import_remote_node(self, primary, count, device_key):
            pass

    if bad_signature:
        ManagementInterface.import_app_key = import_app_key_wrong

    if missing_remote_import:
        delattr(ManagementInterface, "import_remote_node")

    application.Application = Application
    models.ConfigClient = ConfigClient
    models.GenericOnOffServer = GenericOnOffServer
    models.LightLightnessClient = LightLightnessClient
    models.LightLightnessServer = LightLightnessServer
    models.LightCTLServer = LightCTLServer
    models.LightCTLClient = LightCTLClient
    interfaces.ManagementInterface = ManagementInterface
    bluetooth_mesh.models = models

    messages = types.ModuleType("bluetooth_mesh.messages")
    messages.AccessMessage = FakeAccessMessage
    generic = types.ModuleType("bluetooth_mesh.messages.generic")
    light = types.ModuleType("bluetooth_mesh.messages.generic.light")
    ctl = types.ModuleType("bluetooth_mesh.messages.generic.light.ctl")
    lightness = types.ModuleType("bluetooth_mesh.messages.generic.light.lightness")
    ctl.LightCTLOpcode = FakeLightCTLOpcode
    lightness.LightLightnessOpcode = FakeLightLightnessOpcode

    return {
        "bluetooth_mesh": bluetooth_mesh,
        "bluetooth_mesh.application": application,
        "bluetooth_mesh.models": models,
        "bluetooth_mesh.interfaces": interfaces,
        "bluetooth_mesh.messages": messages,
        "bluetooth_mesh.messages.generic": generic,
        "bluetooth_mesh.messages.generic.light": light,
        "bluetooth_mesh.messages.generic.light.ctl": ctl,
        "bluetooth_mesh.messages.generic.light.lightness": lightness,
    }


class RuntimeModuleContext:
    def __init__(self, modules):
        self.modules = modules
        self.saved = {}

    def __enter__(self):
        for name, module in self.modules.items():
            self.saved[name] = sys.modules.get(name)
            sys.modules[name] = module

    def __exit__(self, exc_type, exc, tb):
        for name, module in self.saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class PesetechRuntimeCheckTest(unittest.TestCase):
    def test_runtime_check_passes_with_required_mesh_api(self):
        with RuntimeModuleContext(fake_runtime_modules()):
            errors, details = runtime_check.check_runtime()

        self.assertEqual(errors, [])
        self.assertEqual(
            details,
            [
                "management_import_remote_node: available",
                "ctl_temperature_set_unack: 8265200300002a",
                "lightness_set_unack: 824d00802a",
            ],
        )

    def test_runtime_check_reports_missing_light_ctl_client_method(self):
        with RuntimeModuleContext(fake_runtime_modules(missing_method="send_app")):
            errors, _ = runtime_check.check_runtime()

        self.assertIn("models.LightCTLClient is missing send_app()", errors)

    def test_runtime_check_reports_missing_remote_node_import(self):
        with RuntimeModuleContext(fake_runtime_modules(missing_remote_import=True)):
            errors, _ = runtime_check.check_runtime()

        self.assertIn(
            "ManagementInterface is missing import_remote_node(); Telink mesh.json imports cannot preload device keys.",
            errors,
        )

    def test_runtime_check_reports_signature_mismatch(self):
        with RuntimeModuleContext(fake_runtime_modules(bad_signature=True)):
            errors, _ = runtime_check.check_runtime()

        self.assertIn(
            "ManagementInterface.import_app_key parameters expected ('net_index', 'app_index', 'app_key'), "
            "got ('app_index', 'net_index', 'app_key')",
            errors,
        )

    def test_runtime_check_reports_unexpected_ctl_set_signature(self):
        modules = fake_runtime_modules()

        class WrongLightCTLClient:
            MODEL_ID = (None, 0x1305)

            def get_ctl(self):
                pass

            def repeat(self):
                pass

            def send_app(self):
                pass

            def set_ctl_unack(
                self,
                destination,
                app_index,
                ctl_lightness,
                ctl_temperature,
                delay=0.5,
                retransmissions=6,
                send_interval=0.075,
            ):
                pass

            def tid(self):
                pass

        modules["bluetooth_mesh.models"].LightCTLClient = WrongLightCTLClient
        modules["bluetooth_mesh"].models = modules["bluetooth_mesh.models"]

        with RuntimeModuleContext(modules):
            errors, _ = runtime_check.check_runtime()

        self.assertIn(
            "models.LightCTLClient.set_ctl_unack parameters expected "
            "('destination', 'app_index', 'ctl_temperature', 'ctl_lightness', 'delay', 'retransmissions', 'send_interval'), "
            "got ('destination', 'app_index', 'ctl_lightness', 'ctl_temperature', 'delay', 'retransmissions', 'send_interval')",
            errors,
        )

    def test_print_report_returns_nonzero_for_errors(self):
        self.assertEqual(runtime_check.print_report(["boom"], [], quiet=True), 1)
        self.assertEqual(runtime_check.print_report([], [], quiet=True), 0)


if __name__ == "__main__":
    unittest.main()
