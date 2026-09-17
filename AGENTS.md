# AGENTS.md

## Project overview

pesetech2mqtt is a Python 3 bridge that connects Pesetech/Lepu artificial skylights over Bluetooth LE Mesh to Home Assistant via MQTT Discovery. It runs as a standalone Docker container.

## Setup

```bash
pip install -r requirements.txt
```

## Build / Run

```bash
make build              # docker build -t pesetech2mqtt .
docker compose up        # run via docker-compose.yml
```

## Test

```bash
make test                # python -m unittest discover -s tests
```

## Lint / Format

```bash
make lint                # ruff check app tests
```

## Repository structure

- `app/` — application code: `gateway.py` (BLE mesh gateway), `mqtt.py` (MQTT Discovery publishing), `skylight.py` (device model), `import_mesh.py` (mesh JSON import)
- `tests/` — unittest-based test suite, one file per `app/` module
- `build/` — shell scripts to build native dependencies (bluez, ell, json-c) inside the Docker image
- `docker/` — container entrypoint, Mosquitto config, and Docker-specific README
- `Dockerfile`, `docker-compose.yml` — container build/run definitions
- `config.yaml.example`, `.env.example` — example runtime configuration

## Commit and PR conventions

- Commit messages and PR titles must follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`, `ci:`, `build:`, `perf:`, `style:`, `revert:`), optionally with a scope, e.g. `fix(api): handle null response`.
- This repo squash-merges pull requests only; the PR title becomes the final commit message on `main`.
- A "Conventional Commits" CI check enforces this on both PR titles and direct-push commit messages.
- Branch protection on `main`: no force-pushes, no branch deletion, required status checks must pass.
