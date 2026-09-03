# Changelog

## 1.0.1

- Add `PYTHONUNBUFFERED=1` to Dockerfile so logs appear immediately in `docker compose logs`
- Add `docker/README.md` for Docker Hub
- Add `docker/config/.gitkeep` so the data directory exists on fresh clone

## 1.0.0

- Forked from [hrdwdmrbl/pesetech-home-assistant](https://github.com/hrdwdmrbl/pesetech-home-assistant)
- Removed Home Assistant add-on wrapper; project is now a standalone Docker container
- Added `docker-compose.yml` with optional bundled Mosquitto broker (opt-in via `COMPOSE_PROFILES=mqtt`)
- Added env-var MQTT configuration (`MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_SSL`)
- Added `LOG_LEVEL` env var for runtime log verbosity
- Added MQTT reconnection — recovers from broker restarts without reimporting the mesh
- Added Docker healthcheck via `/tmp/gateway.healthy` heartbeat file
- Added GitHub Actions CI (lint + test on every push/PR)
- Added ruff linting (`make lint`)
- Renamed project to `pesetech2mqtt`; updated MQTT gateway ID and HA unique IDs accordingly
- Fixed env-var precedence in Docker Compose (layered `env_file` replaces `environment:` defaults)
- Changed mesh JSON default path from `/share/pesetech_mesh.json` to `/data/pesetech_mesh.json`

## 0.2.1 (upstream)

- Restore BlueZ's required D-Bus policy during image installation.

## 0.2.0 (upstream)

- Reduced the add-on to one automatic import and service path.
- Made acknowledged command failures restart the add-on through Home Assistant Watchdog.

## 0.1.0 (upstream)

- Initial public release.
