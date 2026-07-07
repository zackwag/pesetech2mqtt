#!/usr/bin/env python3
import argparse
import json
import shlex
import time
from pathlib import Path


STEP_NAMES = ["on", "brightness", "warm", "cool", "off"]
SUCCESS_STATUSES = {200, 201}
HA_BRIGHTNESS_TOLERANCE = 2
HA_KELVIN_TOLERANCE = 150
MQTT_BRIGHTNESS_TOLERANCE = 2
MQTT_MIRED_TOLERANCE = 1
HOST_HA_URL_PLACEHOLDER = "<home_assistant_url_reachable_from_workstation>"
HOST_MQTT_BROKER_PLACEHOLDER = "<externally_reachable_mqtt_host>"
LIGHT_MOVEMENT_OPERATIONS = {"move-test", "ha-service-test", "proof-test"}
SAFE_NO_MOTION_OPERATIONS = {
    "runtime-check",
    "mesh-daemon-check",
    "ble-scan",
    "status",
    "preflight",
    "cloud-fetch",
    "import-check",
    "readiness-test",
    "ha-api-check",
    "diagnostics",
}


def file_status(path):
    path = Path(path)
    status = {
        "path": str(path),
        "exists": path.exists(),
    }
    if path.exists() and path.is_file():
        stat = path.stat()
        status["size_bytes"] = stat.st_size
        status["mtime"] = stat.st_mtime
    return status


def clean_string(value):
    if value is None:
        return ""
    return str(value).strip()


def clean_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_yamlish(path):
    path = Path(path)
    if not path.exists() or not path.is_file():
        return None
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def config_mesh_device_count(path):
    loaded = load_yamlish(path)
    if isinstance(loaded, dict):
        mesh = loaded.get("mesh")
        if isinstance(mesh, dict):
            return len(mesh)
        return 0

    path = Path(path)
    if not path.exists() or not path.is_file():
        return 0

    mesh_indent = None
    count = 0
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip(" "))
        if stripped == "mesh:":
            mesh_indent = indent
            continue
        if mesh_indent is None:
            continue
        if indent <= mesh_indent:
            break
        if indent == mesh_indent + 2 and stripped.endswith(":") and stripped != "{}:":
            count += 1
    return count


def existing_mtimes(paths):
    mtimes = []
    for path in paths:
        path = Path(path)
        if path.exists() and path.is_file():
            mtimes.append(path.stat().st_mtime)
    return mtimes


def is_stale(path, fresh_after_paths):
    path = Path(path)
    if not path.exists() or not path.is_file():
        return False
    required_after = existing_mtimes(fresh_after_paths)
    if not required_after:
        return False
    return path.stat().st_mtime < max(required_after)


def load_json(path):
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None


def load_jsonl(path):
    path = Path(path)
    events = []
    errors = []
    if not path.exists() or not path.is_file() or path.stat().st_size == 0:
        return events, errors

    with path.open("r", encoding="utf-8", errors="replace") as proof_file:
        for line_number, line in enumerate(proof_file, start=1):
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as exc:
                errors.append(f"line {line_number}: invalid JSON: {exc}")

    return events, errors


def sequence_matches_run_id(sequence):
    run_ids = {
        event.get("run_id")
        for event in sequence
        if event.get("run_id") is not None
    }
    return len(run_ids) <= 1


def latest_sequence(events):
    if len(events) < len(STEP_NAMES):
        return None

    for index in range(len(events) - len(STEP_NAMES), -1, -1):
        window = events[index : index + len(STEP_NAMES)]
        if [event.get("step") for event in window] == STEP_NAMES and sequence_matches_run_id(window):
            return window

    return None


def values_match(actual, expected, tolerance=0):
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return abs(actual - expected) <= tolerance
    return actual == expected


def attribute_tolerance(key, *, mqtt=False):
    if mqtt:
        return MQTT_BRIGHTNESS_TOLERANCE if key == "brightness" else MQTT_MIRED_TOLERANCE
    return HA_KELVIN_TOLERANCE if key == "color_temp_kelvin" else HA_BRIGHTNESS_TOLERANCE


def state_contains_expected(state, expected, *, tolerant_attributes=False):
    if not isinstance(state, dict) or not isinstance(expected, dict):
        return False

    for key, value in expected.items():
        if isinstance(value, dict):
            actual = state.get(key)
            if not isinstance(actual, dict):
                return False
            for nested_key, nested_value in value.items():
                if nested_key not in actual:
                    return False
                tolerance = attribute_tolerance(nested_key) if tolerant_attributes else 0
                if not values_match(actual.get(nested_key), nested_value, tolerance):
                    return False
        elif not values_match(state.get(key), value):
            return False

    return True


def attributes_match(state, expected, *, mqtt=False):
    if not isinstance(state, dict) or not isinstance(expected, dict):
        return False
    for key, value in expected.items():
        if key not in state:
            return False
        if not values_match(state.get(key), value, attribute_tolerance(key, mqtt=mqtt)):
            return False
    return True


def publish_ok(event):
    publish = event.get("publish")
    if not isinstance(publish, dict):
        return True
    return not publish.get("error") and publish.get("rc") in (None, 0) and publish.get("published") is not False


def response_ok(event):
    if event.get("response_error"):
        return False
    response = event.get("response")
    return isinstance(response, dict) and response.get("status") in SUCCESS_STATUSES


def proof_summary(path, *, kind, fresh_after_paths=None):
    events, errors = load_jsonl(path)
    sequence = latest_sequence(events)
    stale = is_stale(path, fresh_after_paths or [])
    summary = {
        "path": str(path),
        "events": len(events),
        "errors": errors,
        "complete_sequence": sequence is not None,
        "passed": False,
        "stale": stale,
    }
    if events:
        run_ids = sorted({event.get("run_id") for event in events if event.get("run_id")})
        summary["run_ids"] = run_ids
        summary["latest_steps"] = [event.get("step") for event in events[-len(STEP_NAMES) :]]
    if stale:
        summary["errors"] = errors + ["proof log is older than current setup/proof inputs"]
        return summary
    if sequence is None or errors:
        return summary

    observed = [event.get("observed") for event in sequence]
    summary.update(
        {
            "run_id": sequence[0].get("run_id"),
            "steps": [event.get("step") for event in sequence],
            "observed_confirmed": sum(value is True for value in observed),
            "observed_rejected": sum(value is False for value in observed),
            "observed_unrecorded": sum(value is None for value in observed),
            "precondition_visible_start": any(event.get("precondition_visible_start") is True for event in sequence),
            "matched_state_steps": [
                event.get("step")
                for event in sequence
                if event.get("matched_state") is not None
            ],
        }
    )

    if kind == "mqtt":
        command_topics = sorted({event.get("command_topic") for event in sequence if event.get("command_topic")})
        state_topics = sorted({event.get("state_topic") for event in sequence if event.get("state_topic")})
        if command_topics:
            summary["command_topics"] = command_topics
        if state_topics:
            summary["state_topics"] = state_topics
        summary["passed"] = all(
            publish_ok(event)
            and state_contains_expected(event.get("matched_state"), event.get("expected_state") or {})
            for event in sequence
        )
    elif kind == "ha":
        auth_sources = sorted({event.get("auth_source") for event in sequence if event.get("auth_source")})
        entity_ids = sorted({event.get("entity_id") for event in sequence if event.get("entity_id")})
        urls = sorted({event.get("home_assistant_url") for event in sequence if event.get("home_assistant_url")})
        mqtt_topics = sorted({event.get("mqtt_state_topic") for event in sequence if event.get("mqtt_state_topic")})
        if auth_sources:
            summary["auth_sources"] = auth_sources
        if entity_ids:
            summary["entity_ids"] = entity_ids
        if urls:
            summary["home_assistant_urls"] = urls
        if mqtt_topics:
            summary["mqtt_state_topics"] = mqtt_topics
        summary["matched_mqtt_state_steps"] = [
            event.get("step")
            for event in sequence
            if event.get("matched_mqtt_state") is not None
        ]
        summary["passed"] = all(
            response_ok(event)
            and state_contains_expected(
                event.get("matched_state"),
                event.get("expected_state") or {},
                tolerant_attributes=True,
            )
            and state_contains_expected(event.get("matched_mqtt_state"), event.get("expected_mqtt_state") or {})
            and all(field in (event.get("matched_mqtt_state") or {}) for field in event.get("required_mqtt_fields") or [])
            and all(field in (event.get("expected_mqtt_attributes") or {}) for field in event.get("required_mqtt_fields") or [])
            and attributes_match(
                event.get("matched_mqtt_state"),
                event.get("expected_mqtt_attributes") or {},
                mqtt=True,
            )
            for event in sequence
        )

    return summary


def cloud_summary(report):
    if not isinstance(report, dict):
        return None
    return {
        "status": report.get("status"),
        "region": report.get("region"),
        "requested_home_ids": report.get("requested_home_ids") or [],
        "home_count": report.get("home_count"),
        "homes": report.get("homes") or [],
        "candidate_count": report.get("candidate_count"),
        "selected_candidate": report.get("selected_candidate"),
        "output": report.get("output"),
        "error": report.get("error"),
    }


def simple_report_summary(report, *, path=None, fresh_after_paths=None):
    if not isinstance(report, dict):
        return None
    summary = {
        key: report.get(key)
        for key in (
            "status",
            "operation",
            "sent_light_commands",
            "published_mqtt",
            "provisioned",
            "imported",
            "ha_entity_id",
            "mqtt_source",
            "exit_code",
        )
        if key in report
    }
    if path:
        summary["stale"] = is_stale(path, fresh_after_paths or [])
    return summary


def mesh_scan_summary(report, *, path=None):
    if not isinstance(report, dict):
        return None
    uuids = report.get("unprovisioned_uuids")
    if not isinstance(uuids, list):
        uuids = []
    return {
        "status": report.get("status"),
        "operation": report.get("operation"),
        "found_count": clean_int(report.get("found_count"), 0),
        "unprovisioned_uuids": [clean_string(uuid) for uuid in uuids if clean_string(uuid)],
        "scan_seconds": clean_int(report.get("scan_seconds"), 0),
        "scan_repeat": clean_int(report.get("scan_repeat"), 0),
        "sent_light_commands": report.get("sent_light_commands"),
        "published_mqtt": report.get("published_mqtt"),
        "provisioned": report.get("provisioned"),
        "imported": report.get("imported"),
        "stale": is_stale(path, []) if path else False,
    }


def ble_scan_summary(report, *, path=None):
    if not isinstance(report, dict):
        return None
    names = report.get("names")
    if not isinstance(names, list):
        names = []
    return {
        "status": report.get("status"),
        "operation": report.get("operation"),
        "dev_found_count": clean_int(report.get("dev_found_count"), 0),
        "unique_address_count": clean_int(report.get("unique_address_count"), 0),
        "names": [clean_string(name) for name in names if clean_string(name)],
        "hci_indexes": report.get("hci_indexes") if isinstance(report.get("hci_indexes"), list) else [],
        "scan_modes": report.get("scan_modes") if isinstance(report.get("scan_modes"), list) else [],
        "sent_light_commands": report.get("sent_light_commands"),
        "published_mqtt": report.get("published_mqtt"),
        "started_bluetooth_meshd": report.get("started_bluetooth_meshd"),
        "provisioned": report.get("provisioned"),
        "imported": report.get("imported"),
        "stale": is_stale(path, []) if path else False,
    }


def import_requested_context(args):
    return {
        "mesh_candidate": clean_int(getattr(args, "import_mesh_candidate", 0), 0),
        "node_uuid": clean_string(getattr(args, "import_node_uuid", "")),
        "node_unicast": clean_string(getattr(args, "import_node_unicast", "")),
        "local_address": clean_string(getattr(args, "import_local_address", "")),
        "device_id": clean_string(getattr(args, "device_id", "")) or "skylight",
        "default_entity_id": clean_string(getattr(args, "ha_entity_id", "")) or "light.skylight",
    }


def import_context_mismatches(report, args):
    requested = report.get("requested") if isinstance(report.get("requested"), dict) else {}
    expected = import_requested_context(args)
    mismatches = []
    for key, expected_value in expected.items():
        actual_value = requested.get(key, 0 if key == "mesh_candidate" else "")
        if key == "mesh_candidate":
            actual_value = clean_int(actual_value, 0)
        else:
            actual_value = clean_string(actual_value)
        if actual_value != expected_value:
            mismatches.append(key)
    return mismatches


def same_path(left, right):
    left = clean_string(left)
    right = clean_string(right)
    if not left or not right:
        return False
    try:
        return Path(left) == Path(right)
    except TypeError:
        return False


def import_check_integrity_errors(report, args):
    errors = []
    if report.get("operation") != "import-check":
        errors.append("operation")
    if report.get("dry_run") is not True:
        errors.append("dry_run")
    if report.get("sent_light_commands") is not False:
        errors.append("sent_light_commands")
    if report.get("published_mqtt") is not False:
        errors.append("published_mqtt")
    if report.get("wrote_files") is not False:
        errors.append("wrote_files")
    if not same_path(report.get("source"), getattr(args, "mesh_json", "")):
        errors.append("source")
    selected_node = report.get("selected_node") if isinstance(report.get("selected_node"), dict) else {}
    if not clean_string(selected_node.get("uuid")) or not clean_string(selected_node.get("unicast")):
        errors.append("selected_node")
    return errors


def import_check_summary(report, *, args, path=None, fresh_after_paths=None):
    if not isinstance(report, dict):
        return None
    stale = is_stale(path, fresh_after_paths or []) if path else False
    mismatches = import_context_mismatches(report, args)
    dry_run = report.get("dry_run") is True
    status = report.get("status")
    integrity_errors = import_check_integrity_errors(report, args)
    return {
        "status": status,
        "operation": report.get("operation"),
        "dry_run": dry_run,
        "passed": status == "passed" and not stale and not mismatches and not integrity_errors,
        "sent_light_commands": report.get("sent_light_commands"),
        "published_mqtt": report.get("published_mqtt"),
        "wrote_files": report.get("wrote_files"),
        "source": report.get("source"),
        "selected_node": report.get("selected_node") if isinstance(report.get("selected_node"), dict) else None,
        "requested": report.get("requested") if isinstance(report.get("requested"), dict) else None,
        "error": report.get("error"),
        "stale": stale,
        "context_mismatches": mismatches,
        "integrity_errors": integrity_errors,
    }


def final_audit_summary(report, *, path=None, fresh_after_paths=None):
    if not isinstance(report, dict):
        return None
    stale = is_stale(path, fresh_after_paths or []) if path else False
    objective = report.get("objective") if isinstance(report.get("objective"), dict) else {}
    errors = report.get("errors") if isinstance(report.get("errors"), list) else []
    next_action = objective.get("next_action")
    if stale:
        next_action = "Final audit is older than current setup/proof inputs. Rerun proof-test or host prove-ha-addon."
    return {
        "passed": False if stale else report.get("passed"),
        "objective_proven": False if stale else report.get("objective_proven"),
        "strict_visual_proof": report.get("strict_visual_proof"),
        "proof_run_id": report.get("proof_run_id"),
        "technical_state_proven": False if stale else objective.get("technical_state_proven"),
        "next_action": next_action,
        "requirements": report.get("requirements") if isinstance(report.get("requirements"), dict) else None,
        "errors_count": len(errors),
        "stale": stale,
    }


def host_ha_url(value):
    ha_url = clean_string(value)
    if not ha_url or ha_url.startswith("http://supervisor") or ha_url.startswith("https://supervisor"):
        return HOST_HA_URL_PLACEHOLDER
    return ha_url


def host_mqtt_broker(args):
    mqtt_source = clean_string(getattr(args, "mqtt_source", "none")) or "none"
    broker = clean_string(getattr(args, "mqtt_broker", ""))
    if mqtt_source == "manual" and broker:
        return broker
    return HOST_MQTT_BROKER_PLACEHOLDER


def strict_proof_hint(args):
    ha_url = host_ha_url(getattr(args, "ha_url", ""))
    ha_entity_id = clean_string(getattr(args, "ha_entity_id", "")) or "light.skylight"
    mqtt_source = clean_string(getattr(args, "mqtt_source", "none")) or "none"
    mqtt_port = clean_int(getattr(args, "mqtt_port", None), 1883)
    discovery_prefix = clean_string(getattr(args, "discovery_prefix", "")) or "homeassistant"
    mesh_topic = clean_string(getattr(args, "mesh_topic", "")) or "mqtt_mesh"
    device_id = clean_string(getattr(args, "device_id", "")) or "skylight"
    base = [
        "python3",
        "scripts/pesetech_hardware_session.py",
        "prove-ha-addon",
        "--ha-url",
        ha_url,
        "--ha-entity-id",
        ha_entity_id,
        "--broker",
        host_mqtt_broker(args),
        "--port",
        str(mqtt_port),
        "--discovery-prefix",
        discovery_prefix,
        "--mesh-topic",
        mesh_topic,
        "--device-id",
        device_id,
        "--candidate-timeout",
        "10",
    ]
    readiness_argv = base[:3] + ["--readiness-only"] + base[3:]
    notes = [
        "Run these from the gateway checkout on a workstation while the Home Assistant add-on is running in service mode.",
        "The readiness command checks Home Assistant API, retained MQTT discovery, and entity creation without moving the light.",
        "Run the full command only when you are watching the real skylight and can confirm each visible step.",
        "Set HOME_ASSISTANT_TOKEN to a Home Assistant long-lived access token first, or add --ha-token-file pointing at a file containing that token.",
        "Add --username and --password only if the externally reachable MQTT broker requires them; status output does not include credentials.",
    ]
    if ha_url == HOST_HA_URL_PLACEHOLDER:
        notes.append("Replace the Home Assistant URL placeholder with a URL reachable from this workstation, not http://supervisor/core.")
    if HOST_MQTT_BROKER_PLACEHOLDER in base:
        notes.append("Replace the MQTT broker placeholder with the broker host reachable from this workstation.")
    if mqtt_source in {"supervisor", "supervisor_pending", "persisted"}:
        notes.append("Supervisor or persisted MQTT settings may use internal Home Assistant hostnames; the host proof needs the external broker host and port.")
    return {
        "readiness_argv": readiness_argv,
        "full_argv": base,
        "readiness_command": shlex.join(readiness_argv),
        "full_command": shlex.join(base),
        "mqtt_source": mqtt_source,
        "notes": notes,
    }


def next_operation_hint(operation):
    snippet = f"operation: {operation}"
    notes = [
        "Set this operation in the add-on Configuration tab, save, then start or restart the add-on.",
    ]
    if operation in LIGHT_MOVEMENT_OPERATIONS:
        notes.append("This operation can move the real skylight; run it only while watching the light.")
    elif operation in SAFE_NO_MOTION_OPERATIONS:
        notes.append("This operation is a no-motion gate and should not send light-control commands.")
    elif operation == "service":
        notes.append("This leaves the gateway running for normal Home Assistant control.")
    elif operation in {"scan", "provision", "configure", "import"}:
        notes.append("This operation touches Bluetooth Mesh setup state; keep the skylight nearby and read the add-on log.")

    if operation == "cloud-fetch":
        notes.append("Provide /share/pesetech_cloud_token.txt, or both username/password files, before starting.")
    elif operation == "import-check":
        notes.append("This validates /share/pesetech_mesh.json without writing gateway config or store.")
    elif operation == "import":
        notes.append("This writes /data/config.yaml and /data/store.yaml from /share/pesetech_mesh.json.")
    elif operation == "scan":
        notes.append("For the reset path, raise mesh_scan_repeat and start scan before physically resetting the skylight.")
    elif operation == "provision":
        notes.append("Set skylight_uuid to a UUID from the latest mesh scan report before starting provision.")
    elif operation == "readiness-test":
        notes.append("This verifies MQTT discovery and the Home Assistant entity before any movement test.")
    elif operation == "proof-test":
        notes.append("This is a technical add-on proof; strict final proof still requires host prove-ha-addon.")

    return {
        "operation": operation,
        "configuration_snippet": snippet,
        "moves_real_light": operation in LIGHT_MOVEMENT_OPERATIONS,
        "no_motion_gate": operation in SAFE_NO_MOTION_OPERATIONS,
        "notes": notes,
    }


def choose_next(report):
    files = report["files"]
    reports = report["reports"]
    proofs = report.get("proofs") or {}

    final_audit = reports.get("final_audit") or {}
    if (
        final_audit.get("passed") is True
        and final_audit.get("strict_visual_proof") is True
        and final_audit.get("objective_proven") is True
    ):
        return (
            "service",
            "Strict final audit is present with objective_proven true. Leave the gateway in service mode and review the proof evidence.",
        )
    if final_audit.get("passed") is True and final_audit.get("strict_visual_proof") is True:
        return (
            "service",
            "Strict final audit passed, but objective_proven is not true. Rerun host prove-ha-addon without --readiness-only so the final audit records objective_proven true.",
        )
    if final_audit.get("passed") is True:
        return (
            "service",
            "Technical audit passed, but strict visual proof is false. Run host prove-ha-addon without --readiness-only for final proof.",
        )

    runtime = reports.get("runtime") or {}
    runtime_ready = runtime.get("status") == "passed"
    mesh_daemon = reports.get("mesh_daemon") or {}
    mesh_daemon_ready = mesh_daemon.get("status") == "passed"

    has_move_proof = (proofs.get("mqtt_move") or {}).get("passed") is True
    has_ha_proof = (proofs.get("ha_service") or {}).get("passed") is True
    has_imported_state = (
        files["config"]["exists"]
        and files["store"]["exists"]
        and clean_int(files["config"].get("mesh_device_count"), 0) > 0
    )

    cloud = reports.get("cloud_fetch") or {}
    if not has_imported_state and cloud.get("status") == "candidate-selection-failed":
        return (
            "cloud-fetch",
            "Cloud returned multiple meshes. Set cloud_candidate to the desired candidate, then rerun cloud-fetch.",
        )
    if not has_imported_state and cloud.get("status") in {"credentials-missing", "credential-or-login-failed", "region-resolution-failed", "endpoint-fetch-failed"}:
        if cloud.get("home_count"):
            return (
                "cloud-fetch",
                "Cloud fetch did not produce a mesh, but it found Pesetech homes. Set cloud_home_id from reports.cloud_fetch.homes, then rerun cloud-fetch.",
            )
        return (
            "cloud-fetch",
            "Cloud fetch did not produce a mesh. Fix the reported cloud-fetch issue, then rerun cloud-fetch.",
        )

    if not runtime_ready:
        return (
            "runtime-check",
            "Run runtime-check first so the bundled Bluetooth Mesh Python APIs are verified before any Bluetooth daemon or light work.",
        )

    if not mesh_daemon_ready:
        detail = "failed" if mesh_daemon.get("status") == "failed" else "missing or not passed"
        return (
            "mesh-daemon-check",
            f"Bluetooth Mesh daemon check is {detail}. Run mesh-daemon-check before scan, import, or movement tests.",
        )

    preflight = reports.get("preflight") or {}
    if has_imported_state:
        if not (
            preflight.get("status") == "passed"
            and preflight.get("sent_light_commands") is False
            and not preflight.get("stale")
        ):
            if preflight.get("status") == "failed" and not preflight.get("stale"):
                return (
                    "preflight",
                    "Preflight failed. Fix the reported config, MQTT, or Bluetooth readiness issue, then rerun preflight.",
                )
            return (
                "preflight",
                "Gateway config and store exist. Run preflight, then readiness-test if preflight passes.",
            )

        readiness = reports.get("readiness") or {}
        if not (
            readiness.get("status") == "passed"
            and readiness.get("sent_light_commands") is False
            and not readiness.get("stale")
        ):
            if readiness.get("stale"):
                return (
                    "readiness-test",
                    "Readiness report is older than the current setup inputs. Rerun readiness-test before movement.",
                )
            return (
                "readiness-test",
                "Preflight passed without light-control commands. Run readiness-test to verify discovery and the Home Assistant entity before movement.",
            )

        if has_ha_proof and has_move_proof:
            return (
                "proof-test",
                "Both proof logs exist after the no-motion setup gates. Run proof-test again for a fresh non-interactive audit, or run the host strict proof.",
            )
        if has_move_proof:
            return (
                "ha-service-test",
                "MQTT movement proof exists after the no-motion setup gates. Run ha-service-test to prove Home Assistant service control.",
            )

        return (
            "move-test",
            "Readiness passed after the no-motion setup gates. Run move-test while watching the real skylight.",
        )

    if files["mesh_json"]["exists"]:
        import_check = reports.get("import_check") or {}
        if import_check.get("passed") is True:
            return (
                "import",
                "Import-check passed for the current mesh JSON and import options. Set operation to import to write config/store and start the gateway.",
            )
        if import_check.get("stale") or import_check.get("context_mismatches"):
            return (
                "import-check",
                "Mesh JSON exists, but the previous import-check report is stale or was run with different import options. Rerun import-check before import.",
            )
        if import_check.get("status") == "failed":
            return (
                "import-check",
                "The previous import-check failed. Fix the reported mesh selection issue, then rerun import-check.",
            )
        if import_check.get("integrity_errors"):
            return (
                "import-check",
                "Mesh JSON exists, but the previous import-check report is incomplete or does not match the current mesh JSON. Rerun import-check before import.",
            )
        return (
            "import-check",
            "Mesh JSON exists and no imported gateway store is present. Run import-check, then import if it passes.",
        )

    mesh_scan = reports.get("mesh_scan") or {}
    scan_uuids = mesh_scan.get("unprovisioned_uuids") or []
    if mesh_scan.get("status") == "passed" and clean_int(mesh_scan.get("found_count"), 0) > 0 and scan_uuids:
        first_uuid = scan_uuids[0]
        return (
            "provision",
            f"Mesh scan found unprovisioned node {first_uuid}. Set skylight_uuid to that UUID, then run provision.",
        )
    if mesh_scan.get("status") == "passed" and clean_int(mesh_scan.get("found_count"), 0) == 0:
        return (
            "scan",
            "The latest mesh scan found zero unprovisioned nodes. For the reset path, start scan with a longer repeat window, then physically reset the skylight; non-destructive alternative is cloud-fetch with Pesetech credentials.",
        )
    if mesh_scan.get("status") == "failed":
        return (
            "scan",
            "The latest mesh scan failed. Fix the Bluetooth Mesh scan issue in the report, then rerun scan.",
        )

    return (
        "scan",
        "No imported config/store, mesh JSON, or mesh scan result was found. Run scan for the reset path, or run cloud-fetch to reuse the official app mesh.",
    )


def build_status(args):
    data_dir = Path(getattr(args, "store", "/data/store.yaml")).parent
    mesh_scan_report = getattr(args, "mesh_scan_report", data_dir / "pesetech-mesh-scan.json")
    ble_scan_report = getattr(args, "ble_scan_report", data_dir / "pesetech-ble-scan.json")
    files = {
        "mesh_json": file_status(args.mesh_json),
        "config": file_status(args.config),
        "store": file_status(args.store),
        "cloud_report": file_status(args.cloud_report),
        "import_check_report": file_status(args.import_check_report),
        "runtime_report": file_status(args.runtime_report),
        "mesh_daemon_report": file_status(args.mesh_daemon_report),
        "mesh_scan_report": file_status(mesh_scan_report),
        "ble_scan_report": file_status(ble_scan_report),
        "preflight_report": file_status(args.preflight_report),
        "readiness_report": file_status(args.readiness_report),
        "move_proof_log": file_status(args.proof_log),
        "ha_proof_log": file_status(args.ha_proof_log),
        "final_audit_report": file_status(args.final_audit_report),
    }
    files["config"]["mesh_device_count"] = config_mesh_device_count(args.config)
    reports = {
        "cloud_fetch": cloud_summary(load_json(args.cloud_report)),
        "import_check": import_check_summary(
            load_json(args.import_check_report),
            args=args,
            path=args.import_check_report,
            fresh_after_paths=[args.mesh_json],
        ),
        "runtime": simple_report_summary(load_json(args.runtime_report)),
        "mesh_daemon": simple_report_summary(load_json(args.mesh_daemon_report)),
        "mesh_scan": mesh_scan_summary(load_json(mesh_scan_report), path=mesh_scan_report),
        "ble_scan": ble_scan_summary(load_json(ble_scan_report), path=ble_scan_report),
        "preflight": simple_report_summary(
            load_json(args.preflight_report),
            path=args.preflight_report,
            fresh_after_paths=[args.config, args.store],
        ),
        "readiness": simple_report_summary(
            load_json(args.readiness_report),
            path=args.readiness_report,
            fresh_after_paths=[args.config, args.store, args.preflight_report],
        ),
        "final_audit": final_audit_summary(
            load_json(args.final_audit_report),
            path=args.final_audit_report,
            fresh_after_paths=[args.config, args.store, args.proof_log, args.ha_proof_log],
        ),
    }
    reports = {key: value for key, value in reports.items() if value is not None}
    proofs = {
        "mqtt_move": proof_summary(
            args.proof_log,
            kind="mqtt",
            fresh_after_paths=[args.config, args.store, args.readiness_report],
        ),
        "ha_service": proof_summary(
            args.ha_proof_log,
            kind="ha",
            fresh_after_paths=[args.config, args.store, args.readiness_report, args.proof_log],
        ),
    }

    report = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "operation": "status",
        "read_only": True,
        "sent_light_commands": False,
        "published_mqtt": False,
        "files": files,
        "reports": reports,
        "proofs": proofs,
        "strict_proof": strict_proof_hint(args),
    }
    next_operation, next_action = choose_next(report)
    report["suggested_next_operation"] = next_operation
    report["next_action"] = next_action
    report["next_operation"] = next_operation_hint(next_operation)
    return report


def print_status(report):
    print("Pesetech add-on status")
    print(f"  suggested_next_operation: {report['suggested_next_operation']}")
    print(f"  next_action: {report['next_action']}")
    next_operation = report.get("next_operation") or {}
    if next_operation:
        print("  configuration_snippet:")
        print(f"    {next_operation['configuration_snippet']}")
        print(f"  moves_real_light: {str(next_operation['moves_real_light']).lower()}")
    print()
    print("Files:")
    for name, status in report["files"].items():
        marker = "present" if status["exists"] else "missing"
        size = f", {status['size_bytes']} bytes" if status.get("size_bytes") is not None else ""
        print(f"  {name}: {marker}{size}")
    if report["reports"]:
        print()
        print("Reports:")
        for name, summary in report["reports"].items():
            print(f"  {name}: {json.dumps(summary, sort_keys=True)}")
    print()
    print("Proofs:")
    for name, summary in report["proofs"].items():
        print(
            f"  {name}: events={summary['events']}, "
            f"complete={summary['complete_sequence']}, "
            f"passed={summary['passed']}, "
            f"stale={summary['stale']}"
        )
        if summary.get("run_id"):
            print(f"    run_id: {summary['run_id']}")
        if summary.get("steps"):
            print(f"    steps: {', '.join(summary['steps'])}")
        if "observed_unrecorded" in summary:
            print(
                "    observed: "
                f"confirmed={summary['observed_confirmed']}, "
                f"rejected={summary['observed_rejected']}, "
                f"unrecorded={summary['observed_unrecorded']}"
            )
        if summary.get("auth_sources"):
            print(f"    auth_sources: {', '.join(summary['auth_sources'])}")
        if summary.get("errors"):
            print(f"    errors: {'; '.join(summary['errors'])}")
    proof = report.get("strict_proof")
    if proof:
        print()
        print("Strict host proof:")
        print(f"  readiness_command: {proof['readiness_command']}")
        print(f"  full_command: {proof['full_command']}")
        print("  note: add --username/--password if the external MQTT broker requires auth.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Print a read-only Pesetech add-on next-step status report.")
    parser.add_argument("--mesh-json", default="/share/pesetech_mesh.json")
    parser.add_argument("--config", default="/data/config.yaml")
    parser.add_argument("--store", default="/data/store.yaml")
    parser.add_argument("--cloud-report", default="/share/pesetech_cloud_fetch_report.json")
    parser.add_argument("--import-check-report", default="/data/pesetech-import-check.json")
    parser.add_argument("--runtime-report", default="/data/pesetech-runtime-check.json")
    parser.add_argument("--mesh-daemon-report", default="/data/pesetech-mesh-daemon-check.json")
    parser.add_argument("--mesh-scan-report", default="/data/pesetech-mesh-scan.json")
    parser.add_argument("--ble-scan-report", default="/data/pesetech-ble-scan.json")
    parser.add_argument("--preflight-report", default="/data/pesetech-preflight.json")
    parser.add_argument("--readiness-report", default="/data/pesetech-readiness.json")
    parser.add_argument("--proof-log", default="/data/pesetech-move-test.jsonl")
    parser.add_argument("--ha-proof-log", default="/data/pesetech-ha-service-proof.jsonl")
    parser.add_argument("--final-audit-report", default="/data/pesetech-final-audit.json")
    parser.add_argument("--ha-url", default="http://supervisor/core")
    parser.add_argument("--ha-entity-id", default="light.skylight")
    parser.add_argument("--mqtt-source", default="none")
    parser.add_argument("--mqtt-broker", default="")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--discovery-prefix", default="homeassistant")
    parser.add_argument("--mesh-topic", default="mqtt_mesh")
    parser.add_argument("--device-id", default="skylight")
    parser.add_argument("--import-mesh-candidate", type=int, default=0)
    parser.add_argument("--import-node-uuid", default="")
    parser.add_argument("--import-node-unicast", default="")
    parser.add_argument("--import-local-address", default="")
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args(argv)

    report = build_status(args)
    print_status(report)
    if args.output_json:
        output = Path(args.output_json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print()
        print(f"Wrote status report to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
