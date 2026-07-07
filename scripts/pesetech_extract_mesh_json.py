#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pesetech_import_telink_mesh import (
    LIGHT_CTL_TEMPERATURE_SERVER,
    ImportErrorWithDetail,
    find_mesh_storage_candidates,
    format_node,
    node_model_ids,
    non_provisioner_nodes,
    storage_iv_index,
)


ANCHORS = ("meshJson", "jsonNode", "provisioners", "netKeys", "appKeys")
DEFAULT_MAX_BYTES = 20 * 1024 * 1024


def iter_input_files(inputs, recursive=True):
    for item in inputs:
        path = Path(item)
        if path.is_file():
            yield path
        elif path.is_dir():
            children = path.rglob("*") if recursive else path.iterdir()
            for child in children:
                if child.is_file():
                    yield child
        else:
            raise FileNotFoundError(path)


def read_text(path, max_bytes=DEFAULT_MAX_BYTES):
    size = path.stat().st_size
    if size > max_bytes:
        return None, f"skipped; file is {size} bytes, above --max-bytes {max_bytes}"

    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(encoding), None
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore"), None


def add_json_value(values, seen, location, value):
    try:
        fingerprint = json.dumps(value, sort_keys=True, separators=(",", ":"))
    except TypeError:
        fingerprint = repr(value)
    if fingerprint in seen:
        return
    seen.add(fingerprint)
    values.append((location, value))


def parse_json_values_from_text(text, source_label):
    values = []
    seen = set()
    decoder = json.JSONDecoder()

    try:
        add_json_value(values, seen, source_label, json.loads(text))
    except json.JSONDecodeError:
        pass

    for line_number, line in enumerate(text.splitlines(), start=1):
        if not any(anchor in line for anchor in ANCHORS):
            continue
        starts = [index for index, char in enumerate(line) if char in "{["]
        if not starts:
            continue
        for start in starts[:20]:
            try:
                value, _end = decoder.raw_decode(line, start)
            except json.JSONDecodeError:
                continue
            add_json_value(values, seen, f"{source_label}:line{line_number}:{start}", value)

    positions = set()
    for anchor in ANCHORS:
        offset = 0
        while True:
            index = text.find(anchor, offset)
            if index < 0:
                break
            window_start = max(0, index - 5000)
            window_end = min(len(text), index + len(anchor) + 5000)
            for position in range(window_start, window_end):
                if text[position] in "{[":
                    positions.add(position)
            offset = index + len(anchor)

    for position in sorted(positions)[:2000]:
        try:
            value, _end = decoder.raw_decode(text, position)
        except json.JSONDecodeError:
            continue
        add_json_value(values, seen, f"{source_label}:offset{position}", value)

    return values


def mesh_fingerprint(storage):
    return json.dumps(storage, sort_keys=True, separators=(",", ":"))


def discover_mesh_candidates(inputs, recursive=True, max_bytes=DEFAULT_MAX_BYTES):
    candidates = []
    seen = set()
    skipped = []

    for path in iter_input_files(inputs, recursive=recursive):
        try:
            text, skip_reason = read_text(path, max_bytes=max_bytes)
        except OSError as exc:
            skipped.append((path, str(exc)))
            continue

        if skip_reason:
            skipped.append((path, skip_reason))
            continue

        for location, value in parse_json_values_from_text(text, str(path)):
            for mesh_path, storage in find_mesh_storage_candidates(value, location):
                fingerprint = mesh_fingerprint(storage)
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                candidates.append(
                    {
                        "source": path,
                        "location": mesh_path,
                        "storage": storage,
                    }
                )

    return candidates, skipped


def summarize_candidate(candidate, index):
    storage = candidate["storage"]
    lines = [f"{index}. {candidate['source']}"]
    lines.append(f"   location: {candidate['location']}")
    try:
        lines.append(f"   iv_index: {storage_iv_index(storage):08X}")
    except (TypeError, ValueError):
        lines.append(f"   iv_index: {storage.get('ivIndex', '<missing>')}")

    lines.append(
        "   counts: "
        f"netKeys={len(storage.get('netKeys') or [])}, "
        f"appKeys={len(storage.get('appKeys') or [])}, "
        f"nodes={len(storage.get('nodes') or [])}"
    )

    try:
        nodes = non_provisioner_nodes(storage)
    except ImportErrorWithDetail:
        nodes = storage.get("nodes") or []
    ctl_nodes = [node for node in nodes if LIGHT_CTL_TEMPERATURE_SERVER in node_model_ids(node)]
    visible_nodes = ctl_nodes or nodes
    if visible_nodes:
        lines.append("   likely light nodes:")
        for node in visible_nodes[:5]:
            try:
                lines.append(f"     - {format_node(node)}")
            except (TypeError, ValueError, ImportErrorWithDetail):
                lines.append(
                    "     - "
                    f"{node.get('UUID', '<missing uuid>')} "
                    f"at {node.get('unicastAddress', '<missing address>')}"
                )
        if len(visible_nodes) > 5:
            lines.append(f"     - ... {len(visible_nodes) - 5} more")
    return "\n".join(lines)


def print_candidates(candidates, skipped=None, stream=None):
    stream = stream or sys.stdout
    if candidates:
        print(f"Found {len(candidates)} Telink MeshStorage candidate(s):", file=stream)
        for index, candidate in enumerate(candidates, start=1):
            print(summarize_candidate(candidate, index), file=stream)
    else:
        print("No Telink MeshStorage candidates found.", file=stream)

    if skipped:
        print(f"Skipped {len(skipped)} file(s). Use --max-bytes if a capture file was too large.", file=stream)


def select_candidate(candidates, selection):
    if not candidates:
        raise ImportErrorWithDetail("No Telink MeshStorage candidates were found.")
    if selection is not None:
        if not 1 <= selection <= len(candidates):
            raise ImportErrorWithDetail(f"--candidate must be between 1 and {len(candidates)}.")
        return candidates[selection - 1]
    if len(candidates) != 1:
        raise ImportErrorWithDetail("Multiple mesh candidates found; rerun with --candidate N.")
    return candidates[0]


def write_storage(output, storage):
    text = json.dumps(storage, indent=2, sort_keys=False) + "\n"
    if output == "-":
        sys.stdout.write(text)
        return

    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"Wrote normalized Telink mesh JSON to {path}")


def extract_mesh_json(args):
    candidates, skipped = discover_mesh_candidates(
        args.inputs,
        recursive=not args.no_recursive,
        max_bytes=args.max_bytes,
    )

    should_list = args.list or args.output != "-"
    if should_list:
        print_candidates(candidates, skipped)

    if not args.output:
        return 0 if candidates else 1

    try:
        candidate = select_candidate(candidates, args.candidate)
    except ImportErrorWithDetail as exc:
        print(f"Extraction failed: {exc}", file=sys.stderr)
        return 2

    write_storage(args.output, candidate["storage"])
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Extract a Telink/Pesetech MeshStorage JSON object from raw JSON, HAR, logs, or captured responses."
    )
    parser.add_argument("inputs", nargs="+", help="Capture file(s) or directories to scan.")
    parser.add_argument("-o", "--output", help="Write normalized mesh JSON to this path, or '-' for stdout.")
    parser.add_argument("--candidate", type=int, help="1-based candidate number to write when multiple meshes are found.")
    parser.add_argument("--list", action="store_true", help="List candidates even when writing an output file.")
    parser.add_argument("--no-recursive", action="store_true", help="Do not recurse into input directories.")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES, help="Maximum file size to scan.")
    return extract_mesh_json(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
