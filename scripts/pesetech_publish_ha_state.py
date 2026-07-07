#!/usr/bin/env python3
import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


def load_report(path):
    report_path = Path(path)
    if not report_path.exists() or report_path.stat().st_size == 0:
        return {}
    with report_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def node_summary(node):
    steps = []
    for step in node.get("steps", []):
        steps.append(
            {
                "name": step.get("name"),
                "required": step.get("required", True),
                "status": step.get("status"),
                "elapsed_seconds": step.get("elapsed_seconds"),
                "error": step.get("error", ""),
            }
        )
    return {
        "id": node.get("id"),
        "name": node.get("name"),
        "unicast": node.get("unicast"),
        "uuid": node.get("uuid"),
        "status": node.get("status"),
        "state": node.get("state", {}),
        "steps": steps,
        "error": node.get("error", ""),
    }


def build_payload(report, fallback_status, message):
    status = str(report.get("status") or fallback_status or "unknown")
    operation = report.get("operation", "read-state")
    friendly_operation = str(operation).replace("-", " ").title()
    nodes = [node_summary(node) for node in report.get("nodes", [])]
    failed_step_statuses = {"failed", "error", "timeout"}
    failed_steps = sum(
        1
        for node in report.get("nodes", [])
        for step in node.get("steps", [])
        if step.get("status") in failed_step_statuses
    )
    attributes = {
        "friendly_name": f"Pesetech BLE Mesh {friendly_operation}",
        "message": message,
        "operation": operation,
        "started_at": report.get("started_at"),
        "finished_at": report.get("finished_at"),
        "published_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "timeout_seconds": report.get("timeout_seconds"),
        "sent_light_commands": report.get("sent_light_commands", False),
        "published_mqtt": report.get("published_mqtt", False),
        "node_count": len(nodes),
        "failed_step_count": failed_steps,
        "nodes": nodes,
    }
    if report.get("error"):
        attributes["error"] = report.get("error")
    return {"state": status[:255], "attributes": attributes}


def post_state(ha_url, token, entity_id, payload, timeout):
    url = ha_url.rstrip("/") + f"/api/states/{entity_id}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()


def main():
    parser = argparse.ArgumentParser(description="Publish a Pesetech add-on report as a Home Assistant state entity.")
    parser.add_argument("--report", required=True)
    parser.add_argument("--ha-url", default="http://supervisor/core")
    parser.add_argument("--entity-id", default="sensor.pesetech_ble_mesh_read_state")
    parser.add_argument("--status", default="unknown")
    parser.add_argument("--message", default="")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HA_TOKEN")
    if not token:
        print("No SUPERVISOR_TOKEN/HA_TOKEN available; skipping HA state publish.")
        return 0

    try:
        report = load_report(args.report)
        payload = build_payload(report, args.status, args.message)
        post_state(args.ha_url, token, args.entity_id, payload, args.timeout)
        print(f"Published {args.report} to Home Assistant state {args.entity_id}.")
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print(f"Warning: could not publish HA state: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
