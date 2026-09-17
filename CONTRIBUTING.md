# Contributing to pesetech2mqtt

Thanks for considering a contribution to this Pesetech/Lepu BLE Mesh skylight bridge for Home Assistant.

## Getting started

```bash
git clone https://github.com/zackwag/pesetech2mqtt.git
cd pesetech2mqtt
pip install -r requirements.txt
```

## Development

```bash
make test    # run the unit test suite (python -m unittest discover -s tests)
make lint    # ruff check app tests
make build   # build the Docker image
```

CI runs `make lint`, `make test`, and a Docker build cache warm-up on every push and PR (`.github/workflows/ci.yml`), and requires `make test` to pass on PRs before merge (`.github/workflows/test.yml`, the required "Test" check).

## Commit messages and pull requests

This repo uses [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `chore:`, etc.). Pull requests are squash-merged, and the **PR title** becomes the commit on `main` — so PR titles must follow this format. This is enforced automatically by the "Conventional Commits" check.

Direct pushes to `main` are allowed but must also use a Conventional Commits-formatted commit message (validated by the same check).

## Opening a pull request

1. Fork the repo and create a branch off `main`.
2. Make your changes.
3. Open a pull request with a Conventional Commits-formatted title.
4. Wait for CI to pass — required checks must be green before merge.

## Reporting issues

Use [GitHub Issues](../../issues) for bugs and feature requests.
