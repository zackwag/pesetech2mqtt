import json
import sys
import types
from collections import defaultdict
from enum import IntEnum


_STUBS = None


def install_stubs():
    global _STUBS
    if _STUBS is not None:
        return _STUBS

    yaml = types.ModuleType("yaml")
    yaml.YAMLError = ValueError
    yaml.safe_load = lambda source: json.loads(source.read() if hasattr(source, "read") else source)
    yaml.safe_dump = lambda value, sort_keys=False: json.dumps(value, indent=2, sort_keys=sort_keys) + "\n"
    sys.modules["yaml"] = yaml

    class GenericOnOffOpcode(IntEnum):
        GENERIC_ONOFF_GET = 0x8201
        GENERIC_ONOFF_SET = 0x8202
        GENERIC_ONOFF_STATUS = 0x8204

    class LightLightnessOpcode(IntEnum):
        LIGHT_LIGHTNESS_GET = 0x824B
        LIGHT_LIGHTNESS_SET = 0x824C
        LIGHT_LIGHTNESS_STATUS = 0x824E

    class LightCTLOpcode(IntEnum):
        LIGHT_CTL_SET = 0x825E
        LIGHT_CTL_STATUS = 0x8260
        LIGHT_CTL_TEMPERATURE_GET = 0x8261
        LIGHT_CTL_TEMPERATURE_SET = 0x8264
        LIGHT_CTL_TEMPERATURE_STATUS = 0x8266

    bluetooth_mesh = types.ModuleType("bluetooth_mesh")
    models = types.ModuleType("bluetooth_mesh.models")
    for name in (
        "ConfigClient",
        "HealthClient",
        "GenericOnOffClient",
        "LightLightnessClient",
        "LightCTLClient",
    ):
        setattr(models, name, type(name, (), {}))
    bluetooth_mesh.models = models

    application = types.ModuleType("bluetooth_mesh.application")

    class Application:
        def __init__(self, loop):
            self.loop = loop
            self.elements = {}

    class Element:
        pass

    application.Application = Application
    application.Element = Element

    crypto = types.ModuleType("bluetooth_mesh.crypto")

    class Key:
        def __init__(self, value):
            self.bytes = value

        def __eq__(self, other):
            return type(self) is type(other) and self.bytes == other.bytes

    crypto.ApplicationKey = type("ApplicationKey", (Key,), {})
    crypto.DeviceKey = type("DeviceKey", (Key,), {})
    crypto.NetworkKey = type("NetworkKey", (Key,), {})

    config = types.ModuleType("bluetooth_mesh.messages.config")
    config.GATTNamespaceDescriptor = type("GATTNamespaceDescriptor", (), {"MAIN": object()})
    onoff = types.ModuleType("bluetooth_mesh.messages.generic.onoff")
    onoff.GenericOnOffOpcode = GenericOnOffOpcode
    lightness = types.ModuleType("bluetooth_mesh.messages.generic.light.lightness")
    lightness.LightLightnessOpcode = LightLightnessOpcode
    ctl = types.ModuleType("bluetooth_mesh.messages.generic.light.ctl")
    ctl.LightCTLOpcode = LightCTLOpcode

    modules = {
        "bluetooth_mesh": bluetooth_mesh,
        "bluetooth_mesh.models": models,
        "bluetooth_mesh.application": application,
        "bluetooth_mesh.crypto": crypto,
        "bluetooth_mesh.messages": types.ModuleType("bluetooth_mesh.messages"),
        "bluetooth_mesh.messages.config": config,
        "bluetooth_mesh.messages.generic": types.ModuleType("bluetooth_mesh.messages.generic"),
        "bluetooth_mesh.messages.generic.onoff": onoff,
        "bluetooth_mesh.messages.generic.light": types.ModuleType("bluetooth_mesh.messages.generic.light"),
        "bluetooth_mesh.messages.generic.light.lightness": lightness,
        "bluetooth_mesh.messages.generic.light.ctl": ctl,
    }
    sys.modules.update(modules)

    dbus_next = types.ModuleType("dbus_next")
    dbus_errors = types.ModuleType("dbus_next.errors")

    class DBusError(Exception):
        def __init__(self, error_type, text=""):
            super().__init__(text)
            self.type = error_type

    dbus_errors.DBusError = DBusError
    dbus_next.errors = dbus_errors
    sys.modules["dbus_next"] = dbus_next
    sys.modules["dbus_next.errors"] = dbus_errors

    asyncio_mqtt = types.ModuleType("asyncio_mqtt")
    asyncio_mqtt_client = types.ModuleType("asyncio_mqtt.client")

    class MqttError(Exception):
        pass

    class Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    asyncio_mqtt.MqttError = MqttError
    asyncio_mqtt_client.Client = Client
    asyncio_mqtt.client = asyncio_mqtt_client
    sys.modules["asyncio_mqtt"] = asyncio_mqtt
    sys.modules["asyncio_mqtt.client"] = asyncio_mqtt_client

    _STUBS = types.SimpleNamespace(
        models=models,
        GenericOnOffOpcode=GenericOnOffOpcode,
        LightLightnessOpcode=LightLightnessOpcode,
        LightCTLOpcode=LightCTLOpcode,
        DBusError=DBusError,
        defaultdict=defaultdict,
    )
    return _STUBS
