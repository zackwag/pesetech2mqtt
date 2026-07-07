#!/usr/bin/env python3
import argparse
import json
import sys
import tarfile
from pathlib import Path, PurePosixPath


PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"
MISSING = "MISSING"
INFO = "INFO"


class DiagnosticsBundle:
    def __init__(self, files):
        self.files = files

    @classmethod
    def open(cls, path):
        path = Path(path)
        if path.is_dir():
            return cls(read_directory(path))
        if tarfile.is_tarfile(path):
            return cls(read_tarball(path))
        raise ValueError(f"{path} is not a diagnostics directory or tar archive")

    def text(self, relative_path):
        return self.files.get(relative_path)

    def json(self, relative_path):
        text = self.text(relative_path)
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def has(self, relative_path):
        return relative_path in self.files


def strip_archive_root(name):
    parts = PurePosixPath(name).parts
    if len(parts) > 1 and parts[0].startswith("pesetech-diagnostics-"):
        return PurePosixPath(*parts[1:]).as_posix()
    return PurePosixPath(*parts).as_posix()


def read_tarball(path):
    files = {}
    with tarfile.open(path, "r:*") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            relative_path = strip_archive_root(member.name)
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            files[relative_path] = extracted.read().decode("utf-8", errors="replace")
    return files


def diagnostics_root(path):
    if (path / "manifest.json").exists():
        return path
    candidates = sorted(child for child in path.iterdir() if child.is_dir() and (child / "manifest.json").exists())
    if candidates:
        return candidates[0]
    return path


def read_directory(path):
    root = diagnostics_root(path)
    files = {}
    for child in root.rglob("*"):
        if child.is_file():
            files[child.relative_to(root).as_posix()] = child.read_text(encoding="utf-8", errors="replace")
    return files


def command_exit_zero(text):
    return "exit=0" in (text or "")


def contains(text, needle):
    return needle in (text or "")


def report_item(items, status, name, detail):
    items.append((status, name, detail))


def input_exists(manifest, name):
    inputs = (manifest or {}).get("inputs") or {}
    status = inputs.get(name)
    return bool(isinstance(status, dict) and status.get("exists"))


def first_present(*values):
    for value in values:
        if value is not None and value != "":
            return value
    return None


def first_mesh_entry(config):
    mesh = (config or {}).get("mesh") or {}
    if not isinstance(mesh, dict) or len(mesh) != 1:
        return None, {}
    device_id, node = next(iter(mesh.items()))
    return device_id, node if isinstance(node, dict) else {}


def nested_dict(value, *keys):
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def status_proof_stale(status_report, proof_name):
    return nested_dict(status_report, "proofs", proof_name).get("stale") is True


def status_report_stale(status_report, report_name):
    return nested_dict(status_report, "reports", report_name).get("stale") is True


def status_report_context_mismatches(status_report, report_name):
    mismatches = nested_dict(status_report, "reports", report_name).get("context_mismatches")
    return mismatches if isinstance(mismatches, list) else []


def status_report_integrity_errors(status_report, report_name):
    errors = nested_dict(status_report, "reports", report_name).get("integrity_errors")
    return errors if isinstance(errors, list) else []


def import_check_payload_errors(import_check):
    errors = []
    if import_check.get("operation") != "import-check":
        errors.append("operation")
    if import_check.get("dry_run") is not True:
        errors.append("dry_run")
    if import_check.get("sent_light_commands") is not False:
        errors.append("sent_light_commands")
    if import_check.get("published_mqtt") is not False:
        errors.append("published_mqtt")
    if import_check.get("wrote_files") is not False:
        errors.append("wrote_files")
    node = import_check.get("selected_node") if isinstance(import_check.get("selected_node"), dict) else {}
    if not node.get("uuid") or not node.get("unicast"):
        errors.append("selected_node")
    return errors


def stale_status_context(status_report):
    stale = []
    if status_report_stale(status_report, "import_check"):
        stale.append("import_check")
    if status_report_stale(status_report, "readiness"):
        stale.append("readiness_report")
    for label, proof_name in (("mqtt_move_proof", "mqtt_move"), ("ha_service_proof", "ha_service")):
        if status_proof_stale(status_report, proof_name):
            stale.append(label)
    if status_report_stale(status_report, "final_audit"):
        stale.append("final_audit")
    return ", ".join(stale) if stale else None


def status_next_operation_context(status_report):
    next_operation = (status_report or {}).get("next_operation")
    return next_operation if isinstance(next_operation, dict) else {}


def format_context_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def cloud_home_entries(cloud_report):
    if not isinstance(cloud_report, dict):
        return []
    entries = []
    seen = set()
    for item in cloud_report.get("homes") or []:
        if not isinstance(item, dict):
            continue
        home_id = str(item.get("home_id") or "").strip()
        if not home_id or home_id in seen:
            continue
        seen.add(home_id)
        entry = {"home_id": home_id}
        name = str(item.get("name") or "").strip()
        if name:
            entry["name"] = name
        source = str(item.get("source") or "").strip()
        if source:
            entry["source"] = source
        entries.append(entry)
    return entries


def format_cloud_homes(entries, *, limit=5):
    if not entries:
        return ""
    labels = []
    for entry in entries[:limit]:
        label = entry["home_id"]
        if entry.get("name"):
            label += f" ({entry['name']})"
        labels.append(label)
    if len(entries) > limit:
        labels.append(f"+{len(entries) - limit} more")
    return ", ".join(labels)


def target_context(bundle):
    manifest = bundle.json("manifest.json") or {}
    options = manifest.get("options") or {}
    inputs = manifest.get("inputs") or {}
    readiness = bundle.json("pesetech-readiness.json") or {}
    status_report = bundle.json("pesetech-status.json") or {}
    mesh_daemon = bundle.json("pesetech-mesh-daemon-check.json") or {}
    cloud_report = bundle.json("pesetech-cloud-fetch-report.json") or {}
    cloud_homes = cloud_home_entries(cloud_report)
    import_check = bundle.json("pesetech-import-check.json") or {}
    final_audit = bundle.json("pesetech-final-audit.json") or {}
    status_next_operation = status_next_operation_context(status_report)
    config = bundle.json("config.redacted.json") or {}
    mqtt = config.get("mqtt") if isinstance(config, dict) else {}
    mqtt = mqtt if isinstance(mqtt, dict) else {}
    config_device_id, mesh_node = first_mesh_entry(config)

    discovery_prefix = first_present(options.get("discovery_prefix"), mqtt.get("discovery_prefix"))
    mesh_topic = first_present(options.get("mesh_topic"), mqtt.get("node_id"))
    device_id = first_present(options.get("device_id"), config_device_id)

    pairs = [
        ("mode", manifest.get("mode")),
        ("ha_url", first_present(options.get("ha_url"), readiness.get("ha_url"))),
        (
            "ha_entity_id",
            first_present(options.get("ha_entity_id"), readiness.get("ha_entity_id"), mesh_node.get("default_entity_id")),
        ),
        ("mqtt_source", first_present(options.get("mqtt_source"), readiness.get("mqtt_source"))),
        ("status_suggested_next_operation", status_report.get("suggested_next_operation")),
        ("status_configuration_snippet", status_next_operation.get("configuration_snippet")),
        ("status_moves_real_light", status_next_operation.get("moves_real_light")),
        ("status_no_motion_gate", status_next_operation.get("no_motion_gate")),
        ("status_stale_evidence", stale_status_context(status_report)),
        ("final_audit_objective_proven", final_audit.get("objective_proven")),
        ("mesh_daemon_status", mesh_daemon.get("status")),
        ("mesh_daemon_message", mesh_daemon.get("message")),
        ("cloud_region", options.get("cloud_region")),
        ("cloud_candidate", options.get("cloud_candidate")),
        ("cloud_home_id", options.get("cloud_home_id")),
        ("import_mesh_candidate", options.get("import_mesh_candidate")),
        ("cloud_fetch_status", cloud_report.get("status")),
        ("cloud_home_count", len(cloud_homes)),
        ("cloud_homes", format_cloud_homes(cloud_homes)),
        ("cloud_output_exists", (inputs.get("cloud_output") or {}).get("exists") if isinstance(inputs.get("cloud_output"), dict) else None),
        ("import_check_status", import_check.get("status")),
        ("import_check_selected_uuid", nested_dict(import_check, "selected_node").get("uuid")),
        ("import_check_selected_unicast", nested_dict(import_check, "selected_node").get("unicast")),
        ("discovery_prefix", discovery_prefix),
        ("mesh_topic", mesh_topic),
        ("device_id", device_id),
        ("discovery_topic", discovery_topic(discovery_prefix, mesh_topic, device_id)),
        ("broker", first_present(options.get("broker"), mqtt.get("broker"))),
        ("port", first_present(options.get("port"), mqtt.get("port"))),
        ("username_present", options.get("username_present")),
        ("password_present", options.get("password_present")),
        ("proof_run_id", options.get("proof_run_id")),
    ]
    return [(name, format_context_value(value)) for name, value in pairs if value is not None and value != ""]


def discovery_topic(discovery_prefix, mesh_topic, device_id):
    if not discovery_prefix or not mesh_topic or not device_id:
        return None
    return f"{discovery_prefix}/light/{mesh_topic}/{device_id}/config"


def review(bundle):
    items = []
    manifest = bundle.json("manifest.json")
    if manifest is None:
        report_item(items, FAIL, "manifest", "manifest.json is missing or invalid")
    else:
        mode = manifest.get("mode") or "unknown"
        report_item(items, PASS, "manifest", f"mode={mode}; created_at={manifest.get('created_at', '<unknown>')}")

    hardware = bundle.json("bluetooth-hardware.json")
    if hardware is None:
        report_item(items, MISSING, "bluetooth", "bluetooth-hardware.json is missing or invalid")
    else:
        adapters = hardware.get("adapters") or []
        meshd = hardware.get("bluetooth_meshd_candidates") or []
        executable_meshd = [item for item in meshd if item.get("exists") and item.get("executable")]
        if adapters and executable_meshd:
            report_item(items, PASS, "bluetooth", f"{len(adapters)} adapter(s); bluetooth-meshd executable found")
        elif not adapters:
            report_item(items, FAIL, "bluetooth", "no hci* Bluetooth adapter was visible")
        else:
            report_item(items, FAIL, "bluetooth", "Bluetooth adapter visible, but no executable bluetooth-meshd was found")

    runtime = bundle.text("runtime-check.txt")
    if runtime is None:
        report_item(items, MISSING, "runtime", "runtime-check.txt is missing")
    elif command_exit_zero(runtime) and contains(runtime, "Runtime check passed."):
        report_item(items, PASS, "runtime", "Bluetooth Mesh Python runtime checks passed")
    else:
        report_item(items, FAIL, "runtime", "runtime check did not pass")

    inputs = (manifest or {}).get("inputs") or {}
    mesh_daemon_report = bundle.json("pesetech-mesh-daemon-check.json")
    if mesh_daemon_report is None:
        status = FAIL if input_exists(manifest, "mesh_daemon_report") else INFO
        detail = (
            "mesh daemon check report is missing or invalid"
            if status == FAIL
            else "mesh-daemon-check report not present; run operation=mesh-daemon-check before first HA OS scan/import"
        )
        report_item(items, status, "mesh_daemon", detail)
    elif (
        mesh_daemon_report.get("status") == "passed"
        and mesh_daemon_report.get("sent_light_commands") is False
        and mesh_daemon_report.get("published_mqtt") is False
        and mesh_daemon_report.get("provisioned") is False
        and mesh_daemon_report.get("imported") is False
    ):
        adapters = mesh_daemon_report.get("bluetooth_adapters") or []
        adapter_detail = f"{len(adapters)} adapter(s)" if adapters else "adapter count unknown"
        report_item(items, PASS, "mesh_daemon", f"{adapter_detail}; bluetooth-meshd no-motion startup gate passed")
    else:
        message = mesh_daemon_report.get("message") or "mesh-daemon-check report did not show a safe pass"
        report_item(items, FAIL, "mesh_daemon", message)

    cloud_output = inputs.get("cloud_output") if isinstance(inputs, dict) else None
    cloud_report = bundle.json("pesetech-cloud-fetch-report.json")
    cloud_homes = cloud_home_entries(cloud_report)
    if isinstance(cloud_report, dict):
        status = cloud_report.get("status") or "<unknown>"
        candidate_count = cloud_report.get("candidate_count")
        home_detail = format_cloud_homes(cloud_homes)
        detail = f"status={status}"
        if candidate_count is not None:
            detail += f"; candidates={candidate_count}"
        if home_detail:
            detail += f"; homes={home_detail}"
        if status == "written":
            report_item(items, PASS, "cloud_fetch", detail)
        elif status == "candidate-selection-failed":
            report_item(items, WARN, "cloud_fetch", detail + "; set cloud_candidate, then rerun cloud-fetch")
        elif cloud_homes:
            report_item(items, WARN, "cloud_fetch", detail + "; set cloud_home_id to one of these homes, then rerun cloud-fetch")
        else:
            report_item(items, WARN, "cloud_fetch", detail + "; rerun cloud-fetch or provide /share/pesetech_mesh.json")

    if isinstance(cloud_output, dict):
        cloud_candidates = bundle.text("cloud-mesh-candidates.txt")
        if cloud_output.get("exists"):
            if cloud_candidates is None:
                report_item(items, MISSING, "cloud_mesh", "cloud mesh output exists, but candidate summary is missing")
            elif command_exit_zero(cloud_candidates) and contains(cloud_candidates, "Telink MeshStorage candidate"):
                report_item(items, PASS, "cloud_mesh", "cloud mesh output contains at least one Telink MeshStorage candidate")
            else:
                report_item(items, FAIL, "cloud_mesh", "cloud mesh output did not list a valid Telink MeshStorage candidate")
        else:
            report_item(items, INFO, "cloud_mesh", "cloud mesh output was not present; scan/provision path may not use cloud import")

    status_report = bundle.json("pesetech-status.json")
    if status_report is None:
        status = FAIL if input_exists(manifest, "status_report") else INFO
        detail = (
            "status report is missing or invalid"
            if status == FAIL
            else "status report not present; run add-on operation=status for a read-only next-step summary"
        )
        report_item(items, status, "status", detail)
    elif (
        status_report.get("operation") == "status"
        and status_report.get("read_only") is True
        and status_report.get("sent_light_commands") is False
        and status_report.get("published_mqtt") is False
    ):
        suggested = status_report.get("suggested_next_operation") or "<unknown>"
        next_operation = status_next_operation_context(status_report)
        snippet = next_operation.get("configuration_snippet")
        moves = next_operation.get("moves_real_light")
        detail = f"read-only status report suggests operation={suggested}"
        if snippet:
            detail += f"; snippet={snippet}"
        if moves is not None:
            detail += f"; moves_real_light={format_context_value(moves)}"
        report_item(items, PASS, "status", detail)
    else:
        report_item(items, FAIL, "status", "status report did not show a safe read-only pass")

    import_check = bundle.json("pesetech-import-check.json")
    import_check_stale = status_report_stale(status_report or {}, "import_check")
    import_check_mismatches = status_report_context_mismatches(status_report or {}, "import_check")
    import_check_integrity = status_report_integrity_errors(status_report or {}, "import_check")
    if import_check is None:
        status = FAIL if input_exists(manifest, "import_check_report") else INFO
        detail = (
            "import-check report is missing or invalid"
            if status == FAIL
            else "import-check report not present; run operation=import-check before importing /share/pesetech_mesh.json"
        )
        report_item(items, status, "import_check", detail)
    elif import_check_stale:
        report_item(items, WARN, "import_check", "import-check passed earlier, but status marked it stale; rerun import-check")
    elif import_check_mismatches:
        report_item(items, WARN, "import_check", f"import-check options changed since report: {', '.join(import_check_mismatches)}")
    elif import_check_integrity:
        report_item(items, WARN, "import_check", f"status marked import-check report incomplete: {', '.join(import_check_integrity)}")
    elif import_check.get("status") == "passed" and import_check.get("dry_run") is True:
        payload_errors = import_check_payload_errors(import_check)
        if payload_errors:
            report_item(items, FAIL, "import_check", f"import-check report is incomplete: {', '.join(payload_errors)}")
        else:
            node = import_check.get("selected_node") if isinstance(import_check.get("selected_node"), dict) else {}
            node_detail = ""
            if node:
                node_detail = f"; selected={node.get('uuid', '<unknown uuid>')}@{node.get('unicast', '<unknown unicast>')}"
            report_item(items, PASS, "import_check", f"key-free dry-run import-check passed{node_detail}")
    elif import_check.get("status") == "failed":
        report_item(items, FAIL, "import_check", import_check.get("error") or "import-check failed")
    else:
        report_item(items, FAIL, "import_check", "import-check report did not show a passed dry run")

    preflight = bundle.text("preflight.txt")
    if preflight is None:
        report_item(items, MISSING, "preflight", "preflight.txt is missing")
    elif command_exit_zero(preflight) and contains(preflight, "Config preflight passed."):
        report_item(items, PASS, "preflight", "gateway config/store and host checks passed")
    else:
        report_item(items, FAIL, "preflight", "preflight did not pass")

    discovery = bundle.text("discovery-retained.txt")
    if discovery is None:
        report_item(items, MISSING, "mqtt_discovery", "live retained discovery capture is missing")
    elif command_exit_zero(discovery) and contains(discovery, "Discovery verification passed."):
        report_item(items, PASS, "mqtt_discovery", "retained Home Assistant MQTT discovery payload verified")
    else:
        report_item(items, FAIL, "mqtt_discovery", "retained MQTT discovery verification failed or timed out")

    api_check = bundle.text("home-assistant-api-check.txt")
    if api_check is None:
        report_item(items, MISSING, "ha_api", "Home Assistant API check output is missing")
    elif command_exit_zero(api_check) and contains(api_check, "Home Assistant API check passed."):
        report_item(items, PASS, "ha_api", "Home Assistant API/token check passed")
    else:
        report_item(items, FAIL, "ha_api", "Home Assistant API/token check failed")

    entity_check = bundle.text("home-assistant-entity-check.txt")
    if entity_check is None:
        report_item(items, MISSING, "ha_entity", "Home Assistant entity check output is missing")
    elif command_exit_zero(entity_check) and contains(entity_check, "Home Assistant entity check passed."):
        report_item(items, PASS, "ha_entity", "configured Home Assistant light entity exists")
    else:
        report_item(items, FAIL, "ha_entity", "configured Home Assistant light entity was not confirmed")

    readiness = bundle.json("pesetech-readiness.json")
    readiness_stale = status_report_stale(status_report or {}, "readiness")
    if readiness is None:
        status = MISSING if not input_exists(manifest, "readiness_report") else FAIL
        report_item(items, status, "readiness", "readiness report is missing or invalid")
    elif readiness_stale:
        report_item(items, WARN, "readiness", "readiness report passed earlier, but status marked it stale; rerun readiness-test")
    elif readiness.get("status") == "passed" and readiness.get("sent_light_commands") is False:
        report_item(items, PASS, "readiness", f"no-motion gate passed for {readiness.get('ha_entity_id', '<unknown entity>')}")
    else:
        report_item(items, FAIL, "readiness", "readiness report did not show a no-motion pass")

    proof = bundle.text("proof-verification.txt")
    mqtt_move_stale = status_proof_stale(status_report or {}, "mqtt_move")
    if proof is None:
        report_item(items, MISSING, "mqtt_move_proof", "MQTT movement proof verification is missing")
    elif mqtt_move_stale:
        report_item(items, WARN, "mqtt_move_proof", "MQTT movement proof is present, but status marked it stale; rerun move-test")
    elif command_exit_zero(proof) and contains(proof, "Proof verification passed."):
        report_item(items, PASS, "mqtt_move_proof", "direct MQTT movement proof verified")
    else:
        report_item(items, FAIL, "mqtt_move_proof", "direct MQTT movement proof did not verify")

    ha_proof = bundle.text("ha-service-proof-verification.txt")
    ha_service_stale = status_proof_stale(status_report or {}, "ha_service")
    if ha_proof is None:
        report_item(items, MISSING, "ha_service_proof", "Home Assistant service proof verification is missing")
    elif ha_service_stale:
        report_item(items, WARN, "ha_service_proof", "Home Assistant service proof is present, but status marked it stale; rerun ha-service-test")
    elif command_exit_zero(ha_proof) and contains(ha_proof, "Home Assistant service proof verification passed"):
        report_item(items, PASS, "ha_service_proof", "Home Assistant service proof verified")
    else:
        report_item(items, FAIL, "ha_service_proof", "Home Assistant service proof did not verify")

    final_audit = bundle.json("pesetech-final-audit.json")
    final_audit_stale = status_report_stale(status_report or {}, "final_audit")
    if final_audit is None:
        report_item(items, MISSING, "final_audit", "final audit JSON is missing or invalid")
    elif final_audit_stale:
        report_item(items, WARN, "final_audit", "final audit JSON passed earlier, but status marked it stale; rerun proof-test or host strict proof")
    elif final_audit.get("passed") is True:
        strict = final_audit.get("strict_visual_proof")
        objective_proven = final_audit.get("objective_proven")
        status = PASS if strict is True and objective_proven is True else WARN
        if strict is True and objective_proven is True:
            detail = "strict visual proof passed; objective_proven=true"
        elif strict is True:
            detail = "strict visual proof passed, but objective_proven is false or missing"
        else:
            detail = "technical audit passed, but strict visual proof is false"
        report_item(items, status, "final_audit", detail)
    else:
        report_item(items, FAIL, "final_audit", "final audit did not pass")

    return items


def next_action(items):
    status_by_name = {name: status for status, name, detail in items}
    detail_by_name = {name: detail for status, name, detail in items}
    if status_by_name.get("cloud_fetch") == WARN and "cloud_home_id" in detail_by_name.get("cloud_fetch", ""):
        return "Set add-on cloud_home_id to one of the discovered Pesetech homes, rerun cloud-fetch, then rerun import-check."
    if status_by_name.get("cloud_mesh") == FAIL:
        return "Fix cloud-fetch or provide a valid /share/pesetech_mesh.json, then rerun import-check."
    if status_by_name.get("import_check") == FAIL:
        return "Fix the import-check issue above, then rerun add-on operation=import-check before import."
    if status_by_name.get("import_check") == WARN:
        return "Rerun add-on operation=import-check so the mesh selection report matches the current file and options."
    if status_by_name.get("mesh_daemon") == FAIL:
        return "Run add-on operation=mesh-daemon-check and fix Bluetooth/D-Bus/BlueZ startup before scan, import, or movement tests."
    if status_by_name.get("status") == FAIL:
        return "Rerun add-on operation=status, then collect diagnostics again so the next-step report is valid."
    status_suggestion = suggested_operation_from_status(items)
    if status_suggestion and status_suggestion != "service" and not has_failure(items):
        return f"Run add-on operation={status_suggestion}; this is the next step from the latest read-only status report."
    if status_by_name.get("mqtt_move_proof") == WARN:
        return "Rerun add-on operation=move-test, or host prove-ha-addon without --readiness-only, because the latest status report marked the MQTT proof stale."
    if status_by_name.get("ha_service_proof") == WARN:
        return "Rerun add-on operation=ha-service-test, or host prove-ha-addon without --readiness-only, because the latest status report marked the Home Assistant proof stale."
    for name in ("bluetooth", "runtime", "preflight", "mqtt_discovery", "ha_api", "ha_entity", "readiness"):
        if status_by_name.get(name) in {FAIL, MISSING}:
            return "Fix the first failing setup gate above, rerun readiness-test, then collect diagnostics again."
    if status_by_name.get("mqtt_move_proof") in {FAIL, MISSING}:
        return "Run add-on operation=move-test, or host prove-ha-addon without --readiness-only, while watching the real skylight; collect diagnostics if it does not move."
    if status_by_name.get("ha_service_proof") in {FAIL, MISSING}:
        return "Run add-on operation=ha-service-test, or host prove-ha-addon without --readiness-only, to prove Home Assistant service control."
    if status_by_name.get("final_audit") == WARN:
        return "Run host prove-ha-addon without --readiness-only for strict per-step visual confirmation."
    if status_by_name.get("final_audit") == PASS:
        return "Strict final audit is present with objective_proven=true; review the report before marking the goal complete."
    return "Review the failing item above, then rerun diagnostics after the next hardware attempt."


def suggested_operation_from_status(items):
    marker = "operation="
    for status, name, detail in items:
        if name != "status" or status != PASS or marker not in detail:
            continue
        suggested = detail.split(marker, 1)[1].split()[0].strip(".,;:")
        return suggested or None
    return None


def print_review(items, bundle=None):
    print("Pesetech diagnostics review")
    print()
    if bundle is not None:
        context = target_context(bundle)
        if context:
            print("Target context:")
            for name, value in context:
                print(f"  {name}={value}")
            print()
    for status, name, detail in items:
        print(f"{status:7} {name}: {detail}")
    print()
    print("Next action:")
    print(f"  {next_action(items)}")


def has_failure(items):
    return any(status == FAIL for status, name, detail in items)


def main():
    parser = argparse.ArgumentParser(description="Review a Pesetech diagnostics tarball or extracted diagnostics directory.")
    parser.add_argument("diagnostics", help="pesetech-diagnostics-*.tar.gz or extracted diagnostics directory.")
    args = parser.parse_args()

    try:
        bundle = DiagnosticsBundle.open(args.diagnostics)
        items = review(bundle)
    except Exception as exc:
        print(f"Failed to read diagnostics: {exc}", file=sys.stderr)
        return 2

    print_review(items, bundle)
    return 1 if has_failure(items) else 0


if __name__ == "__main__":
    raise SystemExit(main())
