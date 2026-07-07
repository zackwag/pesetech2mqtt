#!/usr/bin/env python3
import argparse
import glob
import json
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path


REDACT_KEYS = {"password", "username", "token", "secret", "key"}
SENSITIVE_FLAGS = {
    "--password",
    "--username",
    "--token",
    "--mqtt-password",
    "--mqtt-username",
}


def redact_command(command):
    redacted = []
    redact_next = False
    for item in command:
        item = str(item)
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue
        if item in SENSITIVE_FLAGS:
            redacted.append(item)
            redact_next = True
            continue
        for flag in SENSITIVE_FLAGS:
            prefix = flag + "="
            if item.startswith(prefix):
                redacted.append(prefix + "<redacted>")
                break
        else:
            redacted.append(item)
    return redacted


def script_path(name):
    return Path(__file__).resolve().parent / name


def load_yaml(path):
    try:
        import yaml
    except ImportError:
        return None

    with open(path, "r") as input_file:
        return yaml.safe_load(input_file) or {}


def redact(value):
    if isinstance(value, dict):
        redacted = {}
        for key, child in value.items():
            if any(marker in str(key).lower() for marker in REDACT_KEYS):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = redact(child)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_command(command, cwd=None):
    display_command = redact_command(command)
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=30,
        )
    except Exception as exc:
        return f"$ {' '.join(display_command)}\n<failed: {exc}>\n"

    return f"$ {' '.join(display_command)}\nexit={result.returncode}\n{result.stdout}\n"


def copy_redacted_yaml(source, destination):
    data = load_yaml(source)
    if data is None:
        if source.exists():
            destination.write_text(redact_yaml_lines(source.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
        return

    write_text(destination, json.dumps(redact(data), indent=2, sort_keys=True) + "\n")


def redact_yaml_lines(text):
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        key = stripped.split(":", 1)[0].lower() if ":" in stripped else ""
        if key and any(marker in key for marker in REDACT_KEYS):
            indent = line[: len(line) - len(line.lstrip(" "))]
            lines.append(f"{indent}{stripped.split(':', 1)[0]}: <redacted>")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def file_status(path):
    path = Path(path)
    exists = path.exists()
    status = {
        "path": str(path),
        "exists": exists,
    }
    if exists and path.is_file():
        status["size_bytes"] = path.stat().st_size
    return status


def optional_file_status(path):
    return file_status(path) if path else None


def read_optional_text(path, limit=4096):
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return text[:limit].strip()


def copy_redacted_json(source, destination):
    try:
        payload = json.loads(source.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return False
    write_text(destination, json.dumps(redact(payload), indent=2, sort_keys=True) + "\n")
    return True


def bluetooth_adapter_snapshot(sys_class="/sys/class/bluetooth"):
    adapters = []
    for path in sorted(glob.glob(str(Path(sys_class) / "hci*"))):
        adapter_path = Path(path)
        adapter = {
            "name": adapter_path.name,
            "path": str(adapter_path),
            "address": read_optional_text(adapter_path / "address"),
            "type": read_optional_text(adapter_path / "type"),
        }
        device_path = adapter_path / "device"
        if device_path.exists():
            adapter["device_path"] = str(device_path)
            adapter["device_driver"] = read_optional_text(device_path / "driver" / "module")
        adapters.append({key: value for key, value in adapter.items() if value is not None})
    return adapters


def bluetooth_meshd_candidates(candidates=None):
    candidates = candidates or [
        "/usr/libexec/bluetooth/bluetooth-meshd",
        "/usr/lib/bluetooth/bluetooth-meshd",
        shutil.which("bluetooth-meshd"),
    ]
    statuses = []
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        status = file_status(candidate)
        status["executable"] = os.access(candidate, os.X_OK)
        statuses.append(status)
    return statuses


def bluetooth_hardware_snapshot(args):
    sys_class = getattr(args, "bluetooth_sys_class", "/sys/class/bluetooth")
    meshd_candidates = getattr(args, "bluetooth_meshd_candidates", None)
    system_bus_socket = "/run/dbus/system_bus_socket"
    return {
        "sys_class": str(sys_class),
        "sys_class_exists": Path(sys_class).exists(),
        "adapters": bluetooth_adapter_snapshot(sys_class),
        "bluetooth_meshd_candidates": bluetooth_meshd_candidates(meshd_candidates),
        "dbus_system_bus_socket": file_status(system_bus_socket),
    }


def collected_files(output_dir):
    files = []
    for path in sorted(Path(output_dir).rglob("*")):
        if not path.is_file():
            continue
        relative_path = path.relative_to(output_dir).as_posix()
        files.append({"path": relative_path, "size_bytes": path.stat().st_size})
    return files


def write_manifest(path, manifest, output_dir):
    for _ in range(5):
        manifest["collected_files"] = collected_files(output_dir)
        content = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        if path.exists() and path.read_text(encoding="utf-8") == content:
            return
        write_text(path, content)


def diagnostics_manifest(
    args,
    *,
    created_at,
    output_dir,
    archive,
    config_path,
    store_path,
    proof_log_path,
    ha_proof_log_path,
    final_audit_report_path,
    preflight_report_path,
    import_check_report_path,
    readiness_report_path,
    status_report_path,
    runtime_report_path,
    mesh_daemon_report_path,
    cloud_output_path,
    cloud_raw_output_path,
    cloud_report_path,
    cloud_token_file_path,
    cloud_username_file_path,
    cloud_password_file_path,
    compose_dir,
    skip_docker,
):
    ha_token_file = getattr(args, "ha_token_file", None)
    return {
        "created_at": created_at,
        "output_dir": str(output_dir),
        "archive": str(archive),
        "mode": "home_assistant_addon" if skip_docker else "docker_or_host",
        "inputs": {
            "config": file_status(config_path),
            "store": file_status(store_path),
            "proof_log": file_status(proof_log_path),
            "ha_proof_log": file_status(ha_proof_log_path) if ha_proof_log_path else None,
            "final_audit_report": file_status(final_audit_report_path) if final_audit_report_path else None,
            "preflight_report": file_status(preflight_report_path) if preflight_report_path else None,
            "import_check_report": file_status(import_check_report_path) if import_check_report_path else None,
            "readiness_report": file_status(readiness_report_path) if readiness_report_path else None,
            "status_report": file_status(status_report_path) if status_report_path else None,
            "runtime_report": file_status(runtime_report_path) if runtime_report_path else None,
            "mesh_daemon_report": file_status(mesh_daemon_report_path) if mesh_daemon_report_path else None,
            "cloud_output": optional_file_status(cloud_output_path),
            "cloud_raw_output": optional_file_status(cloud_raw_output_path),
            "cloud_report": optional_file_status(cloud_report_path),
            "cloud_token_file": optional_file_status(cloud_token_file_path),
            "cloud_username_file": optional_file_status(cloud_username_file_path),
            "cloud_password_file": optional_file_status(cloud_password_file_path),
            "compose_dir": file_status(compose_dir),
            "ha_token_file_present": bool(ha_token_file),
        },
        "options": {
            "skip_docker": skip_docker,
            "live_discovery": bool(getattr(args, "live_discovery", False)),
            "discovery_prefix": getattr(args, "discovery_prefix", None),
            "mesh_topic": getattr(args, "mesh_topic", None),
            "device_id": getattr(args, "device_id", None),
            "proof_run_id": getattr(args, "proof_run_id", None),
            "ha_api_context": bool(getattr(args, "ha_api_context", False)),
            "ha_url": getattr(args, "ha_url", None),
            "ha_entity_id": getattr(args, "ha_entity_id", None),
            "ha_candidate_search": getattr(args, "ha_candidate_search", None),
            "ha_require_attributes": bool(getattr(args, "ha_require_attributes", False)),
            "ha_require_mqtt_state": bool(getattr(args, "ha_require_mqtt_state", False)),
            "ha_require_mqtt_attributes": bool(getattr(args, "ha_require_mqtt_attributes", False)),
            "broker": getattr(args, "broker", None),
            "port": getattr(args, "port", None),
            "mqtt_source": getattr(args, "mqtt_source", None),
            "cloud_region": getattr(args, "cloud_region", None),
            "cloud_candidate": getattr(args, "cloud_candidate", None),
            "cloud_home_id": getattr(args, "cloud_home_id", None),
            "import_mesh_candidate": getattr(args, "import_mesh_candidate", None),
            "username_present": bool(getattr(args, "username", None)),
            "password_present": bool(getattr(args, "password", None)),
            "mqtt_timeout": getattr(args, "mqtt_timeout", None),
            "discovery_timeout": getattr(args, "discovery_timeout", None),
            "candidate_timeout": getattr(args, "candidate_timeout", None),
            "log_lines": getattr(args, "log_lines", None),
        },
        "collected_files": [],
    }


def append_mqtt_overrides(command, args):
    if getattr(args, "broker", None):
        command.extend(["--broker", args.broker])
    if getattr(args, "port", None) is not None:
        command.extend(["--port", str(args.port)])
    if getattr(args, "username", None):
        command.extend(["--username", args.username])
    if getattr(args, "password", None):
        command.extend(["--password", args.password])


def collect(args):
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    created_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    output_dir = Path(args.output_dir).resolve() / f"pesetech-diagnostics-{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    config_path = Path(args.config).resolve()
    store_path = Path(args.store).resolve()
    proof_log_path = Path(args.proof_log).resolve()
    ha_proof_log = getattr(args, "ha_proof_log", None)
    ha_proof_log_path = Path(ha_proof_log).resolve() if ha_proof_log else None
    final_audit_report = getattr(args, "final_audit_report", None)
    final_audit_report_path = Path(final_audit_report).resolve() if final_audit_report else None
    preflight_report = getattr(args, "preflight_report", None)
    preflight_report_path = Path(preflight_report).resolve() if preflight_report else None
    import_check_report = getattr(args, "import_check_report", None)
    import_check_report_path = Path(import_check_report).resolve() if import_check_report else None
    readiness_report = getattr(args, "readiness_report", None)
    readiness_report_path = Path(readiness_report).resolve() if readiness_report else None
    status_report = getattr(args, "status_report", None)
    status_report_path = Path(status_report).resolve() if status_report else None
    runtime_report = getattr(args, "runtime_report", None)
    runtime_report_path = Path(runtime_report).resolve() if runtime_report else None
    mesh_daemon_report = getattr(args, "mesh_daemon_report", None)
    mesh_daemon_report_path = Path(mesh_daemon_report).resolve() if mesh_daemon_report else None
    cloud_output = getattr(args, "cloud_output", None)
    cloud_output_path = Path(cloud_output).resolve() if cloud_output else None
    cloud_raw_output = getattr(args, "cloud_raw_output", None)
    cloud_raw_output_path = Path(cloud_raw_output).resolve() if cloud_raw_output else None
    cloud_report = getattr(args, "cloud_report", None)
    cloud_report_path = Path(cloud_report).resolve() if cloud_report else None
    cloud_token_file = getattr(args, "cloud_token_file", None)
    cloud_token_file_path = Path(cloud_token_file).resolve() if cloud_token_file else None
    cloud_username_file = getattr(args, "cloud_username_file", None)
    cloud_username_file_path = Path(cloud_username_file).resolve() if cloud_username_file else None
    cloud_password_file = getattr(args, "cloud_password_file", None)
    cloud_password_file_path = Path(cloud_password_file).resolve() if cloud_password_file else None
    compose_dir = Path(args.compose_dir).resolve()
    skip_docker = bool(getattr(args, "skip_docker", False))

    if config_path.exists():
        copy_redacted_yaml(config_path, output_dir / "config.redacted.json")
    if store_path.exists():
        copy_redacted_yaml(store_path, output_dir / "store.redacted.json")
    if proof_log_path.exists():
        write_text(output_dir / "pesetech-proof.jsonl", proof_log_path.read_text(encoding="utf-8", errors="replace"))
    else:
        write_text(output_dir / "proof-log.txt", f"Proof log not found: {proof_log_path}\n")
    if ha_proof_log_path and ha_proof_log_path.exists():
        write_text(output_dir / "pesetech-ha-service-proof.jsonl", ha_proof_log_path.read_text(encoding="utf-8", errors="replace"))
    elif ha_proof_log_path:
        write_text(output_dir / "ha-service-proof-log.txt", f"Home Assistant service proof log not found: {ha_proof_log_path}\n")
    if final_audit_report_path and final_audit_report_path.exists():
        write_text(output_dir / "pesetech-final-audit.json", final_audit_report_path.read_text(encoding="utf-8", errors="replace"))
    elif final_audit_report_path:
        write_text(output_dir / "final-audit-report.txt", f"Final audit report not found: {final_audit_report_path}\n")
    if preflight_report_path and preflight_report_path.exists():
        write_text(output_dir / "pesetech-preflight.json", preflight_report_path.read_text(encoding="utf-8", errors="replace"))
    elif preflight_report_path:
        write_text(output_dir / "preflight-report.txt", f"Preflight report not found: {preflight_report_path}\n")
    if import_check_report_path and import_check_report_path.exists():
        if not copy_redacted_json(import_check_report_path, output_dir / "pesetech-import-check.json"):
            write_text(output_dir / "import-check-report.txt", f"Import-check report was not valid JSON: {import_check_report_path}\n")
    elif import_check_report_path:
        write_text(output_dir / "import-check-report.txt", f"Import-check report not found: {import_check_report_path}\n")
    if readiness_report_path and readiness_report_path.exists():
        write_text(output_dir / "pesetech-readiness.json", readiness_report_path.read_text(encoding="utf-8", errors="replace"))
    elif readiness_report_path:
        write_text(output_dir / "readiness-report.txt", f"Readiness report not found: {readiness_report_path}\n")
    if status_report_path and status_report_path.exists():
        copy_redacted_json(status_report_path, output_dir / "pesetech-status.json")
    elif status_report_path:
        write_text(output_dir / "status-report.txt", f"Status report not found: {status_report_path}\n")
    if runtime_report_path and runtime_report_path.exists():
        write_text(
            output_dir / "pesetech-runtime-check.json",
            runtime_report_path.read_text(encoding="utf-8", errors="replace"),
        )
    elif runtime_report_path:
        write_text(output_dir / "runtime-report.txt", f"Runtime check report not found: {runtime_report_path}\n")
    if mesh_daemon_report_path and mesh_daemon_report_path.exists():
        write_text(
            output_dir / "pesetech-mesh-daemon-check.json",
            mesh_daemon_report_path.read_text(encoding="utf-8", errors="replace"),
        )
    elif mesh_daemon_report_path:
        write_text(output_dir / "mesh-daemon-report.txt", f"Mesh daemon check report not found: {mesh_daemon_report_path}\n")
    if cloud_output_path and cloud_output_path.exists():
        cloud_candidates_command = [
            "python3",
            str(script_path("pesetech_extract_mesh_json.py")),
            str(cloud_output_path),
            "--list",
        ]
        write_text(output_dir / "cloud-mesh-candidates.txt", run_command(cloud_candidates_command))
    elif cloud_output_path:
        write_text(output_dir / "cloud-mesh-candidates.txt", f"Cloud mesh output not found: {cloud_output_path}\n")
    if cloud_report_path and cloud_report_path.exists():
        if not copy_redacted_json(cloud_report_path, output_dir / "pesetech-cloud-fetch-report.json"):
            write_text(output_dir / "cloud-fetch-report.txt", f"Cloud fetch report was not valid JSON: {cloud_report_path}\n")
    elif cloud_report_path:
        write_text(output_dir / "cloud-fetch-report.txt", f"Cloud fetch report not found: {cloud_report_path}\n")

    write_text(
        output_dir / "bluetooth-hardware.json",
        json.dumps(bluetooth_hardware_snapshot(args), indent=2, sort_keys=True) + "\n",
    )

    preflight_command = [
        "python3",
        str(script_path("pesetech_preflight.py")),
        "--config",
        str(config_path),
        "--store",
        str(store_path),
        "--host",
    ]
    if skip_docker:
        preflight_command.append("--skip-docker")
    write_text(output_dir / "preflight.txt", run_command(preflight_command))

    runtime_check_command = [
        "python3",
        str(script_path("pesetech_runtime_check.py")),
    ]
    write_text(output_dir / "runtime-check.txt", run_command(runtime_check_command))

    discovery_dry_run_command = [
        "python3",
        str(script_path("pesetech_mqtt_discovery.py")),
        "--config",
        str(config_path),
        "--dry-run",
    ]
    if args.discovery_prefix:
        discovery_dry_run_command.extend(["--discovery-prefix", args.discovery_prefix])
    if args.mesh_topic:
        discovery_dry_run_command.extend(["--mesh-topic", args.mesh_topic])
    if args.device_id:
        discovery_dry_run_command.extend(["--device-id", args.device_id])
    append_mqtt_overrides(discovery_dry_run_command, args)
    write_text(output_dir / "discovery-dry-run.txt", run_command(discovery_dry_run_command))

    if getattr(args, "live_discovery", False):
        discovery_live_command = [
            "python3",
            str(script_path("pesetech_mqtt_discovery.py")),
            "--config",
            str(config_path),
            "--require-retained",
            "--dump-json",
            "--mqtt-timeout",
            str(getattr(args, "mqtt_timeout", 5.0)),
            "--discovery-timeout",
            str(getattr(args, "discovery_timeout", 5.0)),
        ]
        if getattr(args, "candidate_timeout", None) is not None:
            discovery_live_command.extend(["--candidate-timeout", str(args.candidate_timeout)])
        if args.discovery_prefix:
            discovery_live_command.extend(["--discovery-prefix", args.discovery_prefix])
        if args.mesh_topic:
            discovery_live_command.extend(["--mesh-topic", args.mesh_topic])
        if args.device_id:
            discovery_live_command.extend(["--device-id", args.device_id])
        append_mqtt_overrides(discovery_live_command, args)
        write_text(output_dir / "discovery-retained.txt", run_command(discovery_live_command))

    if getattr(args, "ha_api_context", False):
        ha_url = getattr(args, "ha_url", "http://homeassistant.local:8123")
        ha_entity_id = getattr(args, "ha_entity_id", "light.skylight")
        ha_token_file = getattr(args, "ha_token_file", None)

        ha_api_command = [
            "python3",
            str(script_path("pesetech_ha_service_smoke.py")),
            "--url",
            ha_url,
            "--check-api",
        ]
        if ha_token_file:
            ha_api_command.extend(["--token-file", ha_token_file])
        write_text(output_dir / "home-assistant-api-check.txt", run_command(ha_api_command))

        ha_entity_command = [
            "python3",
            str(script_path("pesetech_ha_service_smoke.py")),
            "--url",
            ha_url,
            "--entity-id",
            ha_entity_id,
            "--check-entity",
            "--candidate-search",
            getattr(args, "ha_candidate_search", "skylight"),
        ]
        if ha_token_file:
            ha_entity_command.extend(["--token-file", ha_token_file])
        write_text(output_dir / "home-assistant-entity-check.txt", run_command(ha_entity_command))

        ha_candidates_command = [
            "python3",
            str(script_path("pesetech_ha_service_smoke.py")),
            "--url",
            ha_url,
            "--entity-id",
            ha_entity_id,
            "--list-candidates",
            "--candidate-search",
            getattr(args, "ha_candidate_search", "skylight"),
        ]
        if ha_token_file:
            ha_candidates_command.extend(["--token-file", ha_token_file])
        write_text(output_dir / "home-assistant-light-candidates.txt", run_command(ha_candidates_command))

    if proof_log_path.exists():
        proof_command = [
            "python3",
            str(script_path("pesetech_verify_proof.py")),
            str(proof_log_path),
            "--config",
            str(config_path),
        ]
        if args.discovery_prefix:
            proof_command.extend(["--discovery-prefix", args.discovery_prefix])
        if args.mesh_topic:
            proof_command.extend(["--mesh-topic", args.mesh_topic])
        if args.device_id:
            proof_command.extend(["--device-id", args.device_id])
        if getattr(args, "proof_run_id", None):
            proof_command.extend(["--run-id", args.proof_run_id])
        write_text(output_dir / "proof-verification.txt", run_command(proof_command))

    if ha_proof_log_path and ha_proof_log_path.exists():
        ha_proof_command = [
            "python3",
            str(script_path("pesetech_verify_ha_service_proof.py")),
            str(ha_proof_log_path),
            "--url",
            getattr(args, "ha_url", "http://homeassistant.local:8123"),
            "--entity-id",
            getattr(args, "ha_entity_id", "light.skylight"),
        ]
        if getattr(args, "ha_require_attributes", False):
            ha_proof_command.append("--require-attributes")
        if getattr(args, "ha_require_mqtt_state", False):
            ha_proof_command.append("--require-mqtt-state")
        if getattr(args, "ha_require_mqtt_attributes", False):
            ha_proof_command.append("--require-mqtt-attributes")
        if getattr(args, "ha_mqtt_brightness_scale", None) is not None:
            ha_proof_command.extend(["--mqtt-brightness-scale", str(args.ha_mqtt_brightness_scale)])
        if getattr(args, "ha_mqtt_brightness_tolerance", None) is not None:
            ha_proof_command.extend(["--mqtt-brightness-tolerance", str(args.ha_mqtt_brightness_tolerance)])
        if getattr(args, "ha_mqtt_mired_tolerance", None) is not None:
            ha_proof_command.extend(["--mqtt-mired-tolerance", str(args.ha_mqtt_mired_tolerance)])
        if getattr(args, "proof_run_id", None):
            ha_proof_command.extend(["--run-id", args.proof_run_id])
        write_text(output_dir / "ha-service-proof-verification.txt", run_command(ha_proof_command))

    commands = {
        "system.txt": [
            ["uname", "-a"],
            ["python3", "--version"],
            ["id"],
        ],
        "bluetooth.txt": [
            ["ls", "-la", "/sys/class/bluetooth"],
            ["systemctl", "is-active", "bluetooth"],
            ["systemctl", "status", "bluetooth", "--no-pager"],
            ["hciconfig", "-a"],
            ["bluetoothctl", "list"],
        ],
    }

    if skip_docker:
        write_text(output_dir / "docker.txt", "Docker diagnostics skipped (--skip-docker).\n")
    else:
        commands["docker.txt"] = [
            ["docker", "version"],
            ["docker", "compose", "version"],
            ["docker", "compose", "ps"],
            ["docker", "compose", "logs", "--tail", str(args.log_lines), "app"],
        ]

    for filename, command_list in commands.items():
        content = []
        for command in command_list:
            cwd = compose_dir if command[:2] == ["docker", "compose"] else None
            if shutil.which(command[0]) is None:
                content.append(f"$ {' '.join(command)}\n<skipped: {command[0]} not found>\n")
                continue
            content.append(run_command(command, cwd=cwd))
        write_text(output_dir / filename, "\n".join(content))

    archive = output_dir.with_suffix(".tar.gz")
    manifest = diagnostics_manifest(
        args,
        created_at=created_at,
        output_dir=output_dir,
        archive=archive,
        config_path=config_path,
        store_path=store_path,
        proof_log_path=proof_log_path,
        ha_proof_log_path=ha_proof_log_path,
        final_audit_report_path=final_audit_report_path,
        preflight_report_path=preflight_report_path,
        import_check_report_path=import_check_report_path,
        readiness_report_path=readiness_report_path,
        status_report_path=status_report_path,
        runtime_report_path=runtime_report_path,
        mesh_daemon_report_path=mesh_daemon_report_path,
        cloud_output_path=cloud_output_path,
        cloud_raw_output_path=cloud_raw_output_path,
        cloud_report_path=cloud_report_path,
        cloud_token_file_path=cloud_token_file_path,
        cloud_username_file_path=cloud_username_file_path,
        cloud_password_file_path=cloud_password_file_path,
        compose_dir=compose_dir,
        skip_docker=skip_docker,
    )
    write_manifest(output_dir / "manifest.json", manifest, output_dir)

    with tarfile.open(archive, "w:gz") as tar:
        tar.add(output_dir, arcname=output_dir.name)

    print(f"Wrote {archive}")
    return archive


def main():
    parser = argparse.ArgumentParser(description="Collect redacted diagnostics for a Pesetech BLE Mesh gateway run.")
    parser.add_argument("--config", default="docker/config/config.yaml", help="Gateway config.yaml path.")
    parser.add_argument("--store", default="docker/config/store.yaml", help="Gateway store.yaml path.")
    parser.add_argument("--proof-log", default="docker/config/pesetech-proof.jsonl", help="Smoke-test proof log path.")
    parser.add_argument("--ha-proof-log", default="docker/config/pesetech-ha-service-proof.jsonl", help="Home Assistant service proof log path.")
    parser.add_argument("--final-audit-report", default="docker/config/pesetech-final-audit.json", help="Structured final audit JSON report path.")
    parser.add_argument("--preflight-report", default="docker/config/pesetech-preflight.json", help="Preflight JSON report path.")
    parser.add_argument("--import-check-report", default=None, help="Key-free import-check JSON report path.")
    parser.add_argument("--readiness-report", default="docker/config/pesetech-readiness.json", help="Readiness-test JSON report path.")
    parser.add_argument("--status-report", default=None, help="Read-only add-on status JSON report path.")
    parser.add_argument("--runtime-report", default=None, help="Runtime-check JSON report path.")
    parser.add_argument("--mesh-daemon-report", default="docker/config/pesetech-mesh-daemon-check.json", help="Mesh daemon check JSON report path.")
    parser.add_argument("--cloud-output", default=None, help="Cloud-fetched normalized mesh JSON path; diagnostics list candidates only.")
    parser.add_argument("--cloud-raw-output", default=None, help="Raw cloud response path; diagnostics records file status only because it can contain mesh keys.")
    parser.add_argument("--cloud-report", default=None, help="Key-free cloud fetch report path to include in diagnostics.")
    parser.add_argument("--cloud-token-file", default=None, help="Cloud token file path; diagnostics records file presence only.")
    parser.add_argument("--cloud-username-file", default=None, help="Cloud username file path; diagnostics records file presence only.")
    parser.add_argument("--cloud-password-file", default=None, help="Cloud password file path; diagnostics records file presence only.")
    parser.add_argument("--cloud-region", default=None, help="Cloud region used for cloud-fetch.")
    parser.add_argument("--cloud-candidate", default=None, help="Cloud mesh candidate selected for cloud-fetch.")
    parser.add_argument("--cloud-home-id", default=None, help="Cloud homeId selected for cloud-fetch.")
    parser.add_argument("--import-mesh-candidate", default=None, help="Import mesh candidate selected for import-check/import.")
    parser.add_argument("--ha-url", default="http://homeassistant.local:8123", help="Home Assistant URL used for HA service proof verification.")
    parser.add_argument("--ha-entity-id", default="light.skylight", help="Home Assistant light entity id used for HA service proof verification.")
    parser.add_argument("--ha-token-file", default=None, help="File containing a Home Assistant long-lived access token for HA API diagnostics.")
    parser.add_argument("--ha-api-context", action="store_true", help="Capture Home Assistant API reachability and candidate light entities.")
    parser.add_argument("--ha-candidate-search", default="skylight", help="Search term for candidate Home Assistant light entity diagnostics.")
    parser.add_argument("--ha-require-attributes", action="store_true", help="Require matching brightness/color_temp_kelvin attributes in HA service proof verification.")
    parser.add_argument("--ha-require-mqtt-state", action="store_true", help="Require matching MQTT bridge state in HA service proof verification.")
    parser.add_argument("--ha-require-mqtt-attributes", action="store_true", help="Require matching brightness/color_temp values in HA service proof MQTT bridge state verification.")
    parser.add_argument("--ha-mqtt-brightness-scale", type=int, default=None, help="MQTT bridge brightness scale expected for HA proof verification.")
    parser.add_argument("--ha-mqtt-brightness-tolerance", type=int, default=None, help="Allowed MQTT bridge brightness difference in HA proof verification.")
    parser.add_argument("--ha-mqtt-mired-tolerance", type=int, default=None, help="Allowed MQTT bridge color_temp mired difference in HA proof verification.")
    parser.add_argument("--discovery-prefix", default=None, help="Optional MQTT discovery prefix override for proof verification.")
    parser.add_argument("--mesh-topic", default=None, help="Optional MQTT mesh topic/node id override for proof verification.")
    parser.add_argument("--device-id", default=None, help="Optional configured mesh device id override for proof verification.")
    parser.add_argument("--proof-run-id", default=None, help="Optional proof run id to verify inside proof logs.")
    parser.add_argument("--live-discovery", action="store_true", help="Capture the retained MQTT discovery payload from the broker.")
    parser.add_argument("--broker", default=None, help="MQTT broker hostname or IP for live discovery diagnostics.")
    parser.add_argument("--port", type=int, default=None, help="MQTT broker port for live discovery diagnostics.")
    parser.add_argument("--username", default=None, help="MQTT username for live discovery diagnostics.")
    parser.add_argument("--password", default=None, help="MQTT password for live discovery diagnostics.")
    parser.add_argument("--mqtt-source", default=None, help="Non-secret MQTT config source label from the add-on.")
    parser.add_argument("--mqtt-timeout", type=float, default=5.0, help="Seconds to wait for MQTT connect/subscribe during live discovery capture.")
    parser.add_argument("--discovery-timeout", type=float, default=5.0, help="Seconds to wait for the retained discovery payload during live discovery capture.")
    parser.add_argument("--candidate-timeout", type=float, default=None, help="Seconds to scan nearby retained discovery configs when the exact live discovery topic is missing.")
    parser.add_argument("--compose-dir", default="docker", help="Directory containing docker-compose.yaml.")
    parser.add_argument("--bluetooth-sys-class", default="/sys/class/bluetooth", help=argparse.SUPPRESS)
    parser.add_argument("--bluetooth-meshd-candidate", dest="bluetooth_meshd_candidates", action="append", help=argparse.SUPPRESS)
    parser.add_argument("--output-dir", default=".", help="Directory for diagnostics output.")
    parser.add_argument("--log-lines", type=int, default=300, help="Docker log lines to include.")
    parser.add_argument("--skip-docker", action="store_true", help="Skip Docker checks/log collection, useful inside a Home Assistant app/add-on.")
    collect(parser.parse_args())


if __name__ == "__main__":
    main()
