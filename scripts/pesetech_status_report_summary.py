#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


def clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def print_cloud_hint(report, stream):
    cloud = ((report.get("reports") or {}).get("cloud_fetch") or {})
    if not isinstance(cloud, dict) or not cloud:
        return
    status = clean_text(cloud.get("status"))
    if not status:
        return
    print(f"  cloud_fetch_status: {status}", file=stream)
    homes = []
    seen = set()
    for item in cloud.get("homes") or []:
        if not isinstance(item, dict):
            continue
        home_id = clean_text(item.get("home_id"))
        if not home_id or home_id in seen:
            continue
        seen.add(home_id)
        label = home_id
        name = clean_text(item.get("name"))
        if name:
            label += f" ({name})"
        homes.append(label)
    if homes:
        print(f"  cloud_homes: {', '.join(homes)}", file=stream)


def load_report(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"could not read status report: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"status report is not valid JSON: {exc}") from exc


def print_summary(report, stream=sys.stdout):
    if not isinstance(report, dict):
        raise RuntimeError("status report root is not an object")

    print("Pesetech status report summary:", file=stream)
    print(f"  suggested_next_operation: {clean_text(report.get('suggested_next_operation')) or '<unknown>'}", file=stream)
    next_action = clean_text(report.get("next_action"))
    if next_action:
        print(f"  next_action: {next_action}", file=stream)

    next_operation = report.get("next_operation") if isinstance(report.get("next_operation"), dict) else {}
    snippet = clean_text(next_operation.get("configuration_snippet"))
    if snippet:
        print("  configuration_snippet:", file=stream)
        print(f"    {snippet}", file=stream)
    if "moves_real_light" in next_operation:
        print(f"  moves_real_light: {str(next_operation.get('moves_real_light')).lower()}", file=stream)
    if "no_motion_gate" in next_operation:
        print(f"  no_motion_gate: {str(next_operation.get('no_motion_gate')).lower()}", file=stream)

    print_cloud_hint(report, stream)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Print a key-free summary of a Pesetech add-on status report.")
    parser.add_argument("report", help="Path to pesetech-status.json")
    args = parser.parse_args(argv)

    try:
        print_summary(load_report(args.report))
    except RuntimeError as exc:
        print(f"Status report summary failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
