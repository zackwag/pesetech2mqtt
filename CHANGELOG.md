# Changelog

## [1.1.0](https://github.com/zackwag/pesetech2mqtt/compare/v1.0.2...v1.1.0) (2026-09-17)


### Features

* **ci:** adopt release-please ([#6](https://github.com/zackwag/pesetech2mqtt/issues/6)) ([56a1be2](https://github.com/zackwag/pesetech2mqtt/commit/56a1be23c0558bd00eff5ee5459ab865d9dc4ee9))


### Bug Fixes

* **ci:** install requirements.txt before running tests ([#8](https://github.com/zackwag/pesetech2mqtt/issues/8)) ([612245d](https://github.com/zackwag/pesetech2mqtt/commit/612245deb9a7b13a39a7def0bc3d4ba8e95b8b58))
* **docker:** bump base image from bullseye to bookworm ([#10](https://github.com/zackwag/pesetech2mqtt/issues/10)) ([6ad423e](https://github.com/zackwag/pesetech2mqtt/commit/6ad423e5d657c84e3e3a420251f87ea6353096cd))


### Documentation

* add CONTRIBUTING and AGENTS guides ([#5](https://github.com/zackwag/pesetech2mqtt/issues/5)) ([e4b76db](https://github.com/zackwag/pesetech2mqtt/commit/e4b76dba0fafbb94a8d20c17f2c996ad135ede5f))

## 1.0.2

- Fix naming collision between Docker Compose host-path vars and container-internal env vars: renamed `DATA_DIR`/`MESH_JSON` in `docker-compose.yml` and `.env.example` to `PESETECH_DATA_DIR`/`PESETECH_MESH_JSON` so user-set values are not inadvertently injected into the container via `env_file`

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
