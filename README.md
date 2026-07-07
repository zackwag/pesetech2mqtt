# Pesetech BLE Mesh for Home Assistant

Home Assistant add-on and MQTT bridge for controlling Pesetech/Lepu artificial skylights over Bluetooth Mesh.

The current skylight support exposes normal Home Assistant light controls:

- on/off
- brightness
- color temperature
- transition-aware brightness/color changes

The bridge uses Home Assistant MQTT discovery, so the skylights appear as regular `light` entities once the add-on is configured and running.

## Repository Layout

- `gateway/`: Bluetooth Mesh gateway and MQTT bridge runtime.
- `docker/`: Docker runtime used by the generated Home Assistant add-on.
- `scripts/`: add-on generation, import, validation, diagnostics, and proof helpers.
- `tests/`: unit tests for the bridge, add-on config generation, and helper scripts.

## Development

Run the test suite:

```sh
python3 -m pytest
```

Generate a local Home Assistant add-on package tree:

```sh
make addon-generate
```

The generated add-on is written under `.tmp/`, which is intentionally ignored by git.

## Configuration Notes

The add-on can import an existing Pesetech/Telink mesh from the official app/cloud export path, or it can provision a fresh mesh device. Do not commit `config.yaml`, `store.yaml`, mesh keys, Home Assistant tokens, Pesetech cloud credentials, add-on build outputs, or diagnostic archives.

This project is still experimental. Keep a way to recover or reset your skylight before provisioning or importing mesh state.
