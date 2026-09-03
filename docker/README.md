# pesetech2mqtt

A lightweight Python bridge that connects Pesetech/Lepu artificial skylights
over Bluetooth Mesh to [Home Assistant](https://www.home-assistant.io/) via
MQTT Discovery.

Source & full docs: <https://github.com/zackwag/pesetech2mqtt>

## Tags

| Tag | Meaning |
| --- | --- |
| `latest` | Newest stable release |
| `X.Y.Z` | Exact release |
| `X.Y` | Latest patch of that minor line |

Images are multi-arch: `linux/amd64` and `linux/arm64`.

## Quick start

### Prerequisites

1. **Provision your skylights** using the Telink SIG Mesh app (Android/iOS).
2. **Export the mesh configuration** from the app — this is your `pesetech_mesh.json`.

### docker run

```bash
docker run -d --name pesetech2mqtt --restart unless-stopped \
  --network host \
  --privileged \
  -v "$PWD/data:/data" \
  -v "$PWD/pesetech_mesh.json:/data/pesetech_mesh.json:ro" \
  -e MQTT_HOST=192.168.1.x \
  zackwag/pesetech2mqtt:latest
```

### docker-compose.yml

```yaml
services:
  pesetech2mqtt:
    image: zackwag/pesetech2mqtt:latest
    restart: unless-stopped
    network_mode: host
    privileged: true
    environment:
      MQTT_HOST: 192.168.1.x
    volumes:
      - ./data:/data
      - ./pesetech_mesh.json:/data/pesetech_mesh.json:ro
```

## Configuration

| Path | Purpose |
| --- | --- |
| `/data` | Runtime data — `config.yaml`, `store.yaml`, mesh storage. Use a bind mount or named volume so configuration survives restarts. |
| `/data/pesetech_mesh.json` | Telink mesh export from the provisioning app — mount read-only. |

| Env var | Default | Purpose |
| --- | --- | --- |
| `MQTT_HOST` | `127.0.0.1` | MQTT broker hostname or IP |
| `MQTT_PORT` | `1883` / `8883` | Port — defaults to 8883 when `MQTT_SSL=true` |
| `MQTT_USERNAME` | — | MQTT username (optional) |
| `MQTT_PASSWORD` | — | MQTT password (optional) |
| `MQTT_SSL` | — | Set to `true` to enable TLS |
| `LOG_LEVEL` | `INFO` | Log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

On first start the gateway imports all skylights from the mesh export and writes
`config.yaml` and `store.yaml` to `/data`. Later starts reuse the saved
configuration — the mesh export is only needed for the initial import.

## Notes

- `--network host` and `--privileged` are required — `bluetooth-meshd` opens
  raw L2CAP sockets and needs direct access to the host Bluetooth adapter.
- Off-with-transition is unreliable on these lights. Use a brightness/color
  transition first, then turn off separately.
