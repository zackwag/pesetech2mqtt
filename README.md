# Pesetech BLE Mesh Gateway

[![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](LICENSE)
[![Backend](https://img.shields.io/badge/backend-Python%203-3776AB?style=flat-square&logo=python)](https://www.python.org/)

A Python bridge that connects [Pesetech/Lepu artificial skylights](https://www.pesetech.com/)
over Bluetooth Mesh to [Home Assistant](https://www.home-assistant.io/) via MQTT Discovery.

---

## Features

- Controls Pesetech/Lepu skylights over BLE Mesh
- Auto-discovers lights in Home Assistant via MQTT Discovery
- Supports on/off, brightness, color temperature, and transitions
- Runs as a standalone Docker container
- MQTT reconnection — recovers from broker restarts without reimporting the mesh

---

## Requirements

- Bluetooth adapter accessible to the host
- Pesetech/Lepu skylight and its exported mesh JSON (`pesetech_mesh.json`)
- MQTT broker (e.g. [Mosquitto](https://mosquitto.org/))
- Home Assistant with MQTT integration enabled

---

## Pairing

The gateway does not provision devices itself — it joins an existing BLE Mesh
network as a secondary node. You first pair your skylights using the
**Telink SIG Mesh** app, then export the mesh configuration for use here.

### 1. Install the provisioning app

Install **Telink SIG Mesh** on Android or iOS (search the app store for
"Telink SIG Mesh"). This is the standard provisioning tool for Telink
BLE Mesh devices, which is what Pesetech/Lepu skylights use internally.

### 2. Provision your skylights

1. Open the app and create a new mesh network (or open an existing one).
2. Tap **Add Device** and put each skylight into pairing mode (typically by
   power-cycling it — consult your skylight's manual).
3. The app scans for unprovisioned BLE devices. Select each skylight and add
   it to the mesh. Give each one a descriptive name (e.g. "Living Room") —
   these names become the default entity names in Home Assistant.
4. Repeat for every skylight you want to control.

### 3. Export the mesh configuration

In the Telink SIG Mesh app, export or share the mesh configuration JSON.
The exact menu path varies by app version but is typically under
**Mesh → Share** or **Settings → Export**. The result is a JSON file —
this is your `pesetech_mesh.json`.

> **Security note:** this file contains the cryptographic keys for your mesh
> network (network key, application key, per-device keys). Treat it like a
> password — do not commit it to version control.

The gateway identifies Pesetech skylights by their BLE Mesh model signature
(`1000`, `1300`, `1303`, `1306`). Any non-skylight nodes (your phone, other
devices) are automatically ignored during import.

---

## Run in Docker

### 1. Get your mesh export

Copy the exported `pesetech_mesh.json` to `docker/pesetech_mesh.json`. It will be
mounted read-only at `/data/pesetech_mesh.json` inside the container.

### 2. Configure (optional)

On first start the gateway automatically imports all skylights from the mesh
export and generates `docker/config/config.yaml`. You can customise that file
afterwards to rename lights or override entity IDs — see
[Configuration Reference](#configuration-reference) below.

### 3. Build and run

**Against your existing MQTT broker:**

```bash
# .env
MQTT_HOST=192.168.1.x

docker compose up --build
```

**With the bundled Mosquitto broker:**

```bash
COMPOSE_PROFILES=mqtt docker compose up --build
# or:
docker compose --profile mqtt up --build
```

Check the logs:

```bash
docker compose logs -f gateway
```

The skylights appear automatically in Home Assistant under Settings → Devices & Services → MQTT.

---

## Configuration Reference

### `config.yaml`

```yaml
mesh:
  living_room_skylight:          # entity ID slug — used in MQTT topics
    type: pesetech_skylight
    uuid: "00000000-0000-0000-0000-000000000000"   # from mesh export
    name: "Living Room Skylight"                   # friendly name in HA
    default_entity_id: "light.living_room_skylight"  # optional override
```

Add one entry per skylight. The entity ID slug must be unique and may only contain
lowercase letters, digits, and underscores.

### Environment variables (Docker)

| Variable | Default | Description |
| --- | --- | --- |
| `MQTT_HOST` | `127.0.0.1` | MQTT broker hostname or IP |
| `MQTT_PORT` | `1883` / `8883` | Port — defaults to 8883 when `MQTT_SSL=true` |
| `MQTT_USERNAME` | — | MQTT username (optional) |
| `MQTT_PASSWORD` | — | MQTT password (optional) |
| `MQTT_SSL` | — | Set to `true` to enable TLS |
| `LOG_LEVEL` | `INFO` | Log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## Notes

- Off-with-transition is unreliable on these lights. Use a brightness/color
  transition first, then turn off separately.

---

## Development

```sh
make test    # run unit tests
make lint    # run ruff
make build   # build Docker image
```
