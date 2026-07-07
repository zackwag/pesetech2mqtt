#!/usr/bin/env python3
import argparse
import tarfile
from pathlib import Path


EXCLUDED_PARTS = {
    ".git",
    ".pytest_cache",
    ".tmp",
    ".venv",
    "__pycache__",
}

EXCLUDED_NAMES = {
    ".DS_Store",
    "config.yaml",
    "store.yaml",
    "store.bak.yaml",
}

EXCLUDED_SUFFIXES = (
    ".pyc",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".zip",
    ".xapk",
    ".apk",
)


def is_excluded(path):
    path = Path(path)
    if any(part in EXCLUDED_PARTS for part in path.parts):
        return True
    if path.name in EXCLUDED_NAMES:
        return True
    return any(path.name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES)


def bundle_files(root):
    root = Path(root)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if not is_excluded(relative):
            yield relative


def make_bundle(root, output):
    root = Path(root).resolve()
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        for relative_path in bundle_files(root):
            archive.add(root / relative_path, arcname=str(relative_path))


def main():
    parser = argparse.ArgumentParser(description="Create a source bundle without local state or secrets.")
    parser.add_argument("--root", default=".", help="Repository root to bundle.")
    parser.add_argument("--output", required=True, help="Output .tar.gz path.")
    args = parser.parse_args()
    make_bundle(args.root, args.output)


if __name__ == "__main__":
    main()
