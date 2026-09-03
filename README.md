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
- Runs as a Home Assistant add-on **or** a standalone Docker container
- MQTT reconnection — recovers from broker restarts without reimporting the mesh

---

## Requirements

- Bluetooth adapter accessible to the host
- Pesetech/Lepu skylight and its exported mesh JSON (`pesetech_mesh.json`)
- MQTT broker (e.g. [Mosquitto](https://mosquitto.org/))
- Home Assistant with MQTT integration enabled

---

## Run in Docker

### 1. Get your mesh export

Copy the Pesetech app's mesh export JSON to `docker/pesetech_mesh.json`.

### 2. Configure

```bash
cp config.yaml.example docker/config/config.yaml
```

Edit `docker/config/config.yaml` and fill in each skylight's UUID and name. UUIDs
come from the mesh export — run the container once and check the logs if you're
unsure which UUID belongs to which light.

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

## Run as a Home Assistant Add-on

1. In Home Assistant, add this repository:
   `https://github.com/zackwag/pesetech-home-assistant`
2. Install **Pesetech BLE Mesh Gateway**.
3. Copy the Pesetech mesh export to `/share/pesetech_mesh.json`.
4. Start the add-on.

The first start imports all skylights from the export. Later starts reuse the
saved mesh configuration.

> **Note:** Off-with-transition is unreliable on these lights. Use a
> brightness/color transition first, then turn off separately.

---

## Development

```sh
make test    # run unit tests
make lint    # run ruff
make addon-image  # build HA add-on image(s)
```
