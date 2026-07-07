import os
from pathlib import Path


def rotate_if_needed(path, next_bytes, max_bytes, max_files):
    if not path or max_bytes <= 0:
        return
    if not os.path.exists(path):
        return
    if os.path.getsize(path) + next_bytes <= max_bytes:
        return

    max_files = max(1, int(max_files or 1))
    prune_extra_rotations(path, max_files)
    for index in range(max_files, 0, -1):
        source = f"{path}.{index}"
        target = f"{path}.{index + 1}"
        if not os.path.exists(source):
            continue
        if index >= max_files:
            os.remove(source)
        else:
            os.replace(source, target)

    os.replace(path, f"{path}.1")


def prune_extra_rotations(path, max_files):
    base = Path(path)
    directory = base.parent
    stem = base.name + "."
    if not directory.exists():
        return
    for candidate in directory.iterdir():
        name = candidate.name
        if not name.startswith(stem):
            continue
        suffix = name[len(stem) :]
        if suffix.isdigit() and int(suffix) > max_files:
            candidate.unlink()
