#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


def clean_text(value):
    if value is None:
        return ""
    text = str(value).strip()
    return text


def cloud_home_labels(report, *, limit=8):
    labels = []
    seen = set()
    for item in report.get("homes") or []:
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
        labels.append(label)

    if len(labels) > limit:
        return labels[:limit] + [f"+{len(labels) - limit} more"]
    return labels


def load_report(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"could not read cloud fetch report: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"cloud fetch report is not valid JSON: {exc}") from exc


def print_summary(report, stream=sys.stdout):
    if not isinstance(report, dict):
        raise RuntimeError("cloud fetch report root is not an object")

    status = clean_text(report.get("status")) or "<unknown>"
    candidate_count = report.get("candidate_count")
    selected_candidate = report.get("selected_candidate")
    requested_home_ids = [clean_text(value) for value in report.get("requested_home_ids") or []]
    requested_home_ids = [value for value in requested_home_ids if value]
    homes = cloud_home_labels(report)

    print("Cloud fetch report summary:", file=stream)
    print(f"  status: {status}", file=stream)
    if candidate_count is not None:
        print(f"  candidates: {candidate_count}", file=stream)
    if selected_candidate:
        print(f"  selected_candidate: {selected_candidate}", file=stream)
    if requested_home_ids:
        print(f"  requested_home_ids: {', '.join(requested_home_ids)}", file=stream)
    if homes:
        print(f"  homes: {', '.join(homes)}", file=stream)

    if status == "written":
        print("  next: set operation=import-check, then import if it passes.", file=stream)
    elif status == "candidate-selection-failed":
        print("  next: set cloud_candidate to the desired candidate number, then rerun cloud-fetch.", file=stream)
    elif homes and not requested_home_ids:
        print("  next: set cloud_home_id to one of the homes above, then rerun cloud-fetch.", file=stream)
    elif homes:
        print("  next: cloud_home_id was already set; review the cloud-fetch error or try another home.", file=stream)
    else:
        print("  next: fix the cloud-fetch issue above or provide /share/pesetech_mesh.json manually.", file=stream)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Print a key-free summary of a Pesetech cloud fetch report.")
    parser.add_argument("report", help="Path to /share/pesetech_cloud_fetch_report.json")
    args = parser.parse_args(argv)

    try:
        print_summary(load_report(args.report))
    except RuntimeError as exc:
        print(f"Cloud fetch report summary failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
