# Pesetech BLE Mesh for Home Assistant

Home Assistant add-on and MQTT bridge for controlling Pesetech/Lepu artificial skylights over Bluetooth Mesh.

Supported controls:

- on/off
- brightness
- color temperature
- transitions for brightness and color temperature

## Prerequisites

- Home Assistant OS or Supervised
- MQTT broker/add-on
- Bluetooth adapter visible to Home Assistant
- Pesetech/Lepu skylight already in the Pesetech app, or an exported `/share/pesetech_mesh.json`

## Install

1. In Home Assistant, add this repository as an add-on repository:
   `https://github.com/hrdwdmrbl/pesetech-home-assistant`
2. Install **Pesetech BLE Mesh Gateway**.
3. Start with the default options.

## Setup

Change only `operation` as you move through the setup:

1. `runtime-check`
2. `mesh-daemon-check`
3. `cloud-fetch` with your Pesetech cloud token or username/password, or copy mesh JSON to `/share/pesetech_mesh.json`
4. `import-check`
5. `import`
6. `service`

Leave other options alone unless you know why you need them.

## Notes

- The light appears in Home Assistant through MQTT discovery.
- Bluetooth adapter quality matters.
- Off-with-transition is unreliable on these lights; use brightness/color transitions, then turn off separately.

## Development

```sh
make test
make addon-generate
```
