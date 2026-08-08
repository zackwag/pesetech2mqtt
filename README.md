# Pesetech BLE Mesh for Home Assistant

Home Assistant add-on and MQTT bridge for controlling Pesetech/Lepu artificial skylights over Bluetooth Mesh.

Supported controls:

- on/off
- brightness
- color temperature
- transitions for brightness and color temperature

## Prerequisites

- Home Assistant OS or Supervised
- MQTT broker add-on
- Bluetooth adapter visible to Home Assistant
- Pesetech/Lepu skylight and its exported mesh JSON

## Install

1. In Home Assistant, add this repository as an add-on repository:
   `https://github.com/hrdwdmrbl/pesetech-home-assistant`
2. Install **Pesetech BLE Mesh Gateway**.
3. Copy the Pesetech mesh export to `/share/pesetech_mesh.json`.
4. Start the add-on.

The first start imports every skylight from the file. Later starts reuse the saved mesh configuration.

## Notes

- The light appears in Home Assistant through MQTT discovery.
- Bluetooth adapter quality matters.
- Off-with-transition is unreliable on these lights; use brightness/color transitions, then turn off separately.

## Development

```sh
make test
make addon-image
```
