#!/usr/bin/env python3
import argparse
import inspect


EXPECTED_MODEL_IDS = {
    "GenericOnOffServer": 0x1000,
    "LightLightnessServer": 0x1300,
    "LightCTLServer": 0x1303,
    "LightCTLClient": 0x1305,
}
REQUIRED_LIGHT_CTL_CLIENT_METHODS = {
    "tid",
    "send_app",
    "repeat",
    "get_ctl",
    "set_ctl_unack",
}
REQUIRED_LIGHT_LIGHTNESS_CLIENT_METHODS = {
    "tid",
    "send_app",
    "repeat",
    "get_lightness",
    "set_lightness_unack",
}
EXPECTED_TEMPERATURE_SET_UNACK_HEX = "8265200300002a"
EXPECTED_LIGHTNESS_SET_UNACK_HEX = "824d00802a"
EXPECTED_METHOD_SIGNATURES = {
    "ManagementInterface.import_subnet": ("net_index", "net_key"),
    "ManagementInterface.import_app_key": ("net_index", "app_index", "app_key"),
    "ManagementInterface.import_remote_node": ("primary", "count", "device_key"),
    "Application.add_app_key": ("net_key_index", "app_key_index", "app_key"),
    "Application.delete_app_key": ("net_key_index", "app_key_index"),
    "models.ConfigClient.add_app_key": ("destination", "net_index", "app_key_index", "net_key_index", "app_key"),
    "models.ConfigClient.delete_app_key": ("destination", "net_index", "app_key_index", "net_key_index"),
    "models.ConfigClient.bind_app_key": ("destination", "net_index", "element_address", "app_key_index", "model"),
    "models.LightCTLClient.set_ctl_unack": (
        "destination",
        "app_index",
        "ctl_temperature",
        "ctl_lightness",
        "delay",
        "retransmissions",
        "send_interval",
    ),
}


def method_parameter_names(owner, method_name):
    method = getattr(owner, method_name, None)
    if method is None:
        return None

    parameters = list(inspect.signature(method).parameters)
    if parameters and parameters[0] == "self":
        parameters = parameters[1:]
    return tuple(parameters)


def check_method_signature(errors, owner_name, owner, method_name, expected):
    actual = method_parameter_names(owner, method_name)
    qualified_name = f"{owner_name}.{method_name}"
    if actual is None:
        errors.append(f"{qualified_name} is missing")
    elif actual != expected:
        errors.append(f"{qualified_name} parameters expected {expected!r}, got {actual!r}")


def check_runtime():
    errors = []
    details = []

    try:
        from bluetooth_mesh import models
        from bluetooth_mesh.application import Application
        from bluetooth_mesh.interfaces import ManagementInterface
        from bluetooth_mesh.messages import AccessMessage
        from bluetooth_mesh.messages.generic.light.ctl import LightCTLOpcode
        from bluetooth_mesh.messages.generic.light.lightness import LightLightnessOpcode
    except ImportError as exc:
        return [f"Could not import python-bluetooth-mesh runtime: {exc}"], details

    for model_name, expected_model_id in EXPECTED_MODEL_IDS.items():
        model = getattr(models, model_name, None)
        actual_model_id = getattr(model, "MODEL_ID", (None, None))[1] if model is not None else None
        if actual_model_id != expected_model_id:
            errors.append(f"models.{model_name}.MODEL_ID[1] expected 0x{expected_model_id:04x}, got {actual_model_id!r}")

    light_ctl_client = getattr(models, "LightCTLClient", None)
    for method_name in sorted(REQUIRED_LIGHT_CTL_CLIENT_METHODS):
        if light_ctl_client is None or not hasattr(light_ctl_client, method_name):
            errors.append(f"models.LightCTLClient is missing {method_name}()")

    light_lightness_client = getattr(models, "LightLightnessClient", None)
    for method_name in sorted(REQUIRED_LIGHT_LIGHTNESS_CLIENT_METHODS):
        if light_lightness_client is None or not hasattr(light_lightness_client, method_name):
            errors.append(f"models.LightLightnessClient is missing {method_name}()")

    if not hasattr(ManagementInterface, "import_remote_node"):
        errors.append("ManagementInterface is missing import_remote_node(); Telink mesh.json imports cannot preload device keys.")
    else:
        details.append("management_import_remote_node: available")

    for qualified_name, expected in EXPECTED_METHOD_SIGNATURES.items():
        owner_name, method_name = qualified_name.rsplit(".", 1)
        if owner_name == "ManagementInterface":
            owner = ManagementInterface
        elif owner_name == "Application":
            owner = Application
        elif owner_name == "models.ConfigClient":
            owner = getattr(models, "ConfigClient", None)
        elif owner_name == "models.LightCTLClient":
            owner = getattr(models, "LightCTLClient", None)
        else:
            owner = None

        if owner is None:
            errors.append(f"{owner_name} is missing")
        else:
            check_method_signature(errors, owner_name, owner, method_name, expected)

    opcode = getattr(LightCTLOpcode, "LIGHT_CTL_TEMPERATURE_SET_UNACKNOWLEDGED", None)
    if int(opcode or 0) != 0x8265:
        errors.append(f"LIGHT_CTL_TEMPERATURE_SET_UNACKNOWLEDGED expected 0x8265, got {opcode!r}")
        return errors, details

    try:
        payload = AccessMessage.build(
            {
                "opcode": opcode,
                "params": {
                    "ctl_temperature": 800,
                    "ctl_delta_uv": 0,
                    "tid": 42,
                },
            }
        )
    except Exception as exc:
        errors.append(f"AccessMessage could not build CTL Temperature Set Unacknowledged payload: {exc}")
    else:
        payload_hex = payload.hex()
        details.append(f"ctl_temperature_set_unack: {payload_hex}")
        if payload_hex != EXPECTED_TEMPERATURE_SET_UNACK_HEX:
            errors.append(
                "CTL Temperature Set Unacknowledged payload "
                f"expected {EXPECTED_TEMPERATURE_SET_UNACK_HEX}, got {payload_hex}"
            )

    lightness_opcode = getattr(LightLightnessOpcode, "LIGHT_LIGHTNESS_SET_UNACKNOWLEDGED", None)
    if int(lightness_opcode or 0) != 0x824D:
        errors.append(f"LIGHT_LIGHTNESS_SET_UNACKNOWLEDGED expected 0x824d, got {lightness_opcode!r}")
        return errors, details

    try:
        payload = AccessMessage.build(
            {
                "opcode": lightness_opcode,
                "params": {
                    "lightness": 32768,
                    "tid": 42,
                },
            }
        )
    except Exception as exc:
        errors.append(f"AccessMessage could not build Light Lightness Set Unacknowledged payload: {exc}")
    else:
        payload_hex = payload.hex()
        details.append(f"lightness_set_unack: {payload_hex}")
        if payload_hex != EXPECTED_LIGHTNESS_SET_UNACK_HEX:
            errors.append(
                "Light Lightness Set Unacknowledged payload "
                f"expected {EXPECTED_LIGHTNESS_SET_UNACK_HEX}, got {payload_hex}"
            )

    return errors, details


def print_report(errors, details, quiet=False):
    if not quiet:
        print("Pesetech BLE Mesh runtime check")
        for detail in details:
            print(f"  {detail}")

    if errors:
        if not quiet:
            print("\nRuntime check failed:")
            for error in errors:
                print(f"  - {error}")
        return 1

    if not quiet:
        print("\nRuntime check passed.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Verify python-bluetooth-mesh APIs needed by the Pesetech bridge.")
    parser.add_argument("--quiet", action="store_true", help="Only set the exit status.")
    args = parser.parse_args()

    errors, details = check_runtime()
    return print_report(errors, details, quiet=args.quiet)


if __name__ == "__main__":
    raise SystemExit(main())
