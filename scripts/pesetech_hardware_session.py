#!/usr/bin/env python3
import argparse
import contextlib
import functools
import http.server
import io
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import UUID, uuid4

from pesetech_addon_config import DEFAULTS as ADDON_DEFAULT_OPTIONS


REDACTED = "<redacted>"
DEFAULT_CONFIG = "docker/config/config.yaml"
HA_ADDON_PROOF_CONFIG = "docker/config/ha-addon-proof.yaml"
SENSITIVE_FLAGS = {
    "--password",
    "--username",
    "--token",
    "--mqtt-password",
    "--mqtt-username",
}
SENSITIVE_ENV_MARKERS = ("PASSWORD", "TOKEN", "SECRET", "USERNAME")
RUNBOOK_DEFAULT_ADDON_ARCHIVE = "/tmp/pesetech-ha-local-addon.tar.gz"
HOST_MQTT_BROKER_PLACEHOLDER = "<externally_reachable_mqtt_host>"
DEFAULT_REMOTE_DIAGNOSTICS_GLOB = "/share/pesetech-diagnostics-*.tar.gz"
DEFAULT_DIAGNOSTICS_OUTPUT_DIR = "/tmp/pesetech-diagnostics"
DEFAULT_REMOTE_ADDONS_DIR = "/addons"
DEFAULT_ADDON_SLUG = "pesetech_ble_mesh"
DEFAULT_REMOTE_SHARE_DIR = "/share"
DEFAULT_CLOUD_TOKEN_NAME = "pesetech_cloud_token.txt"
DEFAULT_CLOUD_USERNAME_NAME = "pesetech_cloud_username.txt"
DEFAULT_CLOUD_PASSWORD_NAME = "pesetech_cloud_password.txt"
DEFAULT_MESH_JSON_NAME = "pesetech_mesh.json"
DEFAULT_OPERATION_OVERRIDE_NAME = "pesetech_next_operation.json"
DEFAULT_ADDON_REPORTS_OUTPUT_DIR = "/tmp/pesetech-addon-reports"
DEFAULT_SSH_CONNECT_TIMEOUT = 10
DEFAULT_ADDON_REPOSITORY_DIR = "/tmp/pesetech-ha-addon"
DEFAULT_ADDON_GIT_OUTPUT_DIR = "/tmp/pesetech-ha-addon-git"
DEFAULT_ADDON_GIT_REPO_NAME = "pesetech-ha-addon.git"
DEFAULT_ADDON_GIT_HTTP_PORT = 8766
DEFAULT_HA_URL = "http://homeassistant.local:8123"
DEFAULT_HASSIO_TOKEN_ENV = "HOME_ASSISTANT_TOKEN"
DEFAULT_ADDON_NAME = "Pesetech BLE Mesh Gateway"
SENSITIVE_OPTION_KEYS = {
    "cloud_password",
    "cloud_token",
    "cloud_username",
    "mqtt_password",
    "mqtt_username",
}
ADDON_SHARED_REPORTS = {
    "runtime-check": "/share/pesetech-runtime-check.json",
    "mesh-daemon-check": "/share/pesetech-mesh-daemon-check.json",
    "ble-scan": "/share/pesetech-ble-scan.json",
    "import-check": "/share/pesetech-import-check.json",
    "preflight": "/share/pesetech-preflight.json",
    "readiness-test": "/share/pesetech-readiness.json",
    "read-state": "/share/pesetech-state-read.json",
    "raw-command": "/share/pesetech-raw-command.json",
    "skylight-programs": "/share/pesetech-skylight-programs.json",
    "move-test": "/share/pesetech-move-test.jsonl",
    "ha-service-test": "/share/pesetech-ha-service-proof.jsonl",
    "proof-test-audit": "/share/pesetech-final-audit.json",
}
ADDON_OPERATIONS = (
    "service",
    "runtime-check",
    "mesh-daemon-check",
    "ble-scan",
    "status",
    "preflight",
    "scan",
    "provision",
    "configure",
    "cloud-fetch",
    "import-check",
    "import",
    "readiness-test",
    "read-state",
    "raw-command",
    "skylight-programs",
    "move-test",
    "ha-api-check",
    "ha-service-test",
    "proof-test",
    "diagnostics",
    "list",
)
ADDON_API_SEQUENCE = (
    "runtime-check",
    "mesh-daemon-check",
    "ble-scan",
    "status",
    "cloud-fetch",
    "import-check",
    "import",
    "preflight",
    "readiness-test",
    "move-test",
    "service",
    "ha-api-check",
    "ha-service-test",
    "proof-test",
)
ADDON_MOVEMENT_OPERATIONS = {"move-test", "ha-service-test", "proof-test"}
ADDON_HA_TARGET_OPERATIONS = {"readiness-test", "ha-api-check", "ha-service-test", "proof-test"}
ADDON_IMPORT_OPTION_OPERATIONS = {"import-check", "import"}
ADDON_LOG_GATE_PREFIX = "Pesetech operation gate:"
ADDON_LOG_GATE_MARKERS = {
    "runtime-check": (("Runtime check passed.",),),
    "mesh-daemon-check": (("Bluetooth Mesh daemon check passed.",),),
    "ble-scan": (("BLE scan completed:",),),
    "cloud-fetch": (("Cloud mesh fetch completed.",),),
    "import-check": (
        ("Dry run only; no files written.",),
        ("Existing imported gateway config and store found; import-check would not rewrite them",),
    ),
    "import": (
        ("Import files written.",),
        ("Existing imported gateway config and store found; skipping mesh.json import.",),
    ),
    "preflight": (("Config preflight passed.",),),
    "readiness-test": (("Readiness-test passed without publishing light-control commands.",),),
    "skylight-programs": (("Skylight programs completed.",),),
    "move-test": (("Move-test completed.",),),
    "ha-api-check": (("Home Assistant API check passed.", "Home Assistant entity check passed."),),
    "ha-service-test": (("Home Assistant service test completed.",),),
    "proof-test": (("Final audit passed.", "Proof-test completed."),),
}


def repo_root():
    return Path(__file__).resolve().parents[1]


def resolve_repo_path(path):
    path = Path(path)
    if path.is_absolute():
        return path
    return repo_root() / path


def should_redact_env(key):
    key = str(key).upper()
    return any(marker in key for marker in SENSITIVE_ENV_MARKERS)


def redact_command(command):
    redacted = []
    redact_next = False

    for item in command:
        item = str(item)
        if redact_next:
            redacted.append(REDACTED)
            redact_next = False
            continue

        if item in SENSITIVE_FLAGS:
            redacted.append(item)
            redact_next = True
            continue

        for flag in SENSITIVE_FLAGS:
            prefix = flag + "="
            if item.startswith(prefix):
                redacted.append(prefix + REDACTED)
                break
        else:
            redacted.append(item)

    return redacted


def env_command(command, env=None):
    prefix = []
    for key, value in sorted((env or {}).items()):
        display_value = REDACTED if should_redact_env(key) else value
        prefix.append(f"{key}={shlex.quote(str(display_value))}")
    return " ".join(prefix + [shlex.join(redact_command(command))])


def quote_runbook_arg(value):
    value = str(value)
    if value.startswith("$"):
        return f'"{value}"'
    if value.startswith("<") and value.endswith(">"):
        return value
    return shlex.quote(value)


def format_runbook_command(command, indent="  "):
    grouped = []
    index = 0
    while index < len(command):
        item = str(command[index])
        next_item = str(command[index + 1]) if index + 1 < len(command) else None
        if item.startswith("--") and next_item is not None and not next_item.startswith("--"):
            grouped.append(f"{quote_runbook_arg(item)} {quote_runbook_arg(next_item)}")
            index += 2
        else:
            grouped.append(quote_runbook_arg(item))
            index += 1

    quoted = grouped
    if len(quoted) <= 3:
        return indent + " ".join(quoted)
    lines = [indent + quoted[0] + " \\"]
    for item in quoted[1:-1]:
        lines.append(indent + "  " + item + " \\")
    lines.append(indent + "  " + quoted[-1])
    return "\n".join(lines)


def run_command(command, cwd=None, env=None, dry_run=False):
    cwd = Path(cwd or repo_root()).resolve()
    print(f"$ cd {shlex.quote(str(cwd))} && {env_command(command, env)}")
    if dry_run:
        return 0

    merged_env = os.environ.copy()
    if env:
        merged_env.update({key: str(value) for key, value in env.items()})
    return subprocess.run(command, cwd=cwd, env=merged_env, check=False).returncode


def run_capture_command(command, cwd=None, env=None, dry_run=False, placeholder=""):
    cwd = Path(cwd or repo_root()).resolve()
    print(f"$ cd {shlex.quote(str(cwd))} && {env_command(command, env)}")
    if dry_run:
        return 0, placeholder

    merged_env = os.environ.copy()
    if env:
        merged_env.update({key: str(value) for key, value in env.items()})
    result = subprocess.run(command, cwd=cwd, env=merged_env, check=False, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode, result.stdout


def check_http_reachable(url, timeout):
    if not url:
        return None, ""
    request = urllib.request.Request(url, headers={"User-Agent": "pesetech-ha-host-check"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return True, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return True, f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return False, str(exc.reason)
    except TimeoutError:
        return False, "timed out"


class HassioApiError(Exception):
    def __init__(self, method, url, status, detail):
        super().__init__(f"{method} {url} failed with HTTP {status}: {detail}")
        self.method = method
        self.url = url
        self.status = status
        self.detail = detail


def redacted_payload(value):
    if isinstance(value, dict):
        return {
            key: REDACTED if str(key) in SENSITIVE_OPTION_KEYS else redacted_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redacted_payload(item) for item in value]
    return value


def load_optional_secret(value=None, file_path=None, env_name=None):
    if value:
        return str(value)
    if file_path:
        return Path(file_path).expanduser().read_text(encoding="utf-8").strip()
    if env_name:
        return os.environ.get(env_name, "").strip()
    return ""


def hassio_base_url(args):
    hassio_url = getattr(args, "hassio_url", None)
    if hassio_url:
        return hassio_url.rstrip("/")
    ha_url = getattr(args, "ha_url", DEFAULT_HA_URL) or DEFAULT_HA_URL
    return ha_url.rstrip("/") + "/api/hassio"


def hassio_headers(args):
    token = load_optional_secret(
        getattr(args, "token", None),
        getattr(args, "token_file", None),
        getattr(args, "token_env", DEFAULT_HASSIO_TOKEN_ENV),
    )
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        if getattr(args, "auth_header", "authorization") == "x-supervisor-token":
            headers["X-Supervisor-Token"] = token
        else:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def hassio_api_request(args, method, path, payload=None, ok_statuses=(200, 201)):
    base_url = hassio_base_url(args)
    path = "/" + path.lstrip("/")
    url = base_url + path
    display = f"{method.upper()} {url}"
    if payload is not None:
        display += " " + json.dumps(redacted_payload(payload), sort_keys=True)
    print(display)

    token = load_optional_secret(
        getattr(args, "token", None),
        getattr(args, "token_file", None),
        getattr(args, "token_env", DEFAULT_HASSIO_TOKEN_ENV),
    )
    if not token and not getattr(args, "dry_run", False):
        token_source = getattr(args, "token_env", DEFAULT_HASSIO_TOKEN_ENV)
        print(f"Missing Home Assistant/Supervisor API token. Set {token_source} or pass --token-file.", file=sys.stderr)
        raise HassioApiError(method.upper(), url, 0, "missing token")

    if getattr(args, "dry_run", False):
        return {"dry_run": True}

    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=hassio_headers(args), method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=getattr(args, "timeout", 30.0)) as response:
            body = response.read().decode("utf-8", errors="replace")
            if response.status not in ok_statuses:
                raise HassioApiError(method.upper(), url, response.status, body)
            if not body.strip():
                return {}
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {"raw": body}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HassioApiError(method.upper(), url, exc.code, detail) from exc
    except urllib.error.URLError as exc:
        raise HassioApiError(method.upper(), url, 0, str(exc.reason)) from exc


def hassio_data(payload):
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def iter_hassio_addons(payload):
    data = hassio_data(payload)
    if isinstance(data, dict):
        addons = data.get("addons")
        if isinstance(addons, dict):
            return addons.values()
        if isinstance(addons, list):
            return addons
        if "slug" in data:
            return [data]
    if isinstance(data, list):
        return data
    return []


def find_hassio_addon_slug(payload, addon_slug=DEFAULT_ADDON_SLUG, addon_name=DEFAULT_ADDON_NAME):
    for addon in iter_hassio_addons(payload):
        if not isinstance(addon, dict):
            continue
        candidate_slug = str(addon.get("slug") or addon.get("name") or "")
        candidate_name = str(addon.get("name") or "")
        if candidate_slug == addon_slug or candidate_slug.endswith(f"_{addon_slug}"):
            return candidate_slug
        if addon_name and candidate_name == addon_name:
            return candidate_slug
    return ""


def parse_addon_options(items):
    options = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"Option override must be KEY=VALUE: {item}")
        key, raw_value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Option override must have a key: {item}")
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            value = raw_value
        options[key] = value
    return options


def addon_api_option_overrides(args):
    options = parse_addon_options(getattr(args, "option", []))
    field_map = {
        "cloud_region": "cloud_region",
        "cloud_home_id": "cloud_home_id",
        "cloud_candidate": "cloud_candidate",
        "import_mesh_candidate": "import_mesh_candidate",
        "import_node_uuid": "import_node_uuid",
        "import_node_unicast": "import_node_unicast",
        "import_local_address": "import_local_address",
        "ha_entity_id": "ha_entity_id",
        "addon_ha_url": "ha_url",
        "discovery_prefix": "discovery_prefix",
        "node_id": "node_id",
        "device_id": "device_id",
    }
    for attr, option_name in field_map.items():
        value = getattr(args, attr, None)
        if value is None:
            continue
        if isinstance(value, str) and value == "":
            continue
        options[option_name] = value

    if getattr(args, "import_force", False):
        options["import_force"] = True
    cloud_token = load_optional_secret(
        getattr(args, "cloud_token", None),
        getattr(args, "cloud_token_file", None),
        getattr(args, "cloud_token_env", "PESETECH_CLOUD_TOKEN"),
    )
    if cloud_token:
        options["cloud_token"] = cloud_token
    cloud_username = load_optional_secret(
        getattr(args, "cloud_username", None),
        getattr(args, "cloud_username_file", None),
        getattr(args, "cloud_username_env", "PESETECH_CLOUD_USERNAME"),
    )
    cloud_password = load_optional_secret(
        getattr(args, "cloud_password", None),
        getattr(args, "cloud_password_file", None),
        getattr(args, "cloud_password_env", "PESETECH_CLOUD_PASSWORD"),
    )
    if bool(cloud_username) != bool(cloud_password):
        raise ValueError("Cloud username and password must be set together.")
    if cloud_username:
        options["cloud_username"] = cloud_username
        options["cloud_password"] = cloud_password
    return options


def resolve_installed_addon_slug(args):
    explicit_slug = getattr(args, "installed_slug", "") or getattr(args, "slug", DEFAULT_ADDON_SLUG)
    if getattr(args, "dry_run", False) or getattr(args, "no_discover_slug", False):
        return explicit_slug
    payload = hassio_api_request(args, "GET", "/addons")
    discovered_slug = find_hassio_addon_slug(payload, getattr(args, "slug", DEFAULT_ADDON_SLUG), getattr(args, "name", DEFAULT_ADDON_NAME))
    return discovered_slug or explicit_slug


def addon_info_options(payload):
    data = hassio_data(payload)
    if isinstance(data, dict) and isinstance(data.get("options"), dict):
        return data["options"]
    return {}


def build_full_addon_options(args, addon_slug, operation, overrides=None):
    options = ADDON_DEFAULT_OPTIONS.copy()
    if not getattr(args, "dry_run", False):
        payload = hassio_api_request(args, "GET", f"/addons/{addon_slug}/info")
        options.update({key: value for key, value in addon_info_options(payload).items() if value is not None})
    options["operation"] = operation
    options.update(overrides or {})
    return options


def set_full_addon_options(args, addon_slug, operation, overrides=None):
    options = build_full_addon_options(args, addon_slug, operation, overrides)
    return hassio_api_request(args, "POST", f"/addons/{addon_slug}/options", {"options": options})


def hassio_addon_action(args, addon_slug, action):
    if action == "none":
        return 0
    if action == "restart":
        try:
            hassio_api_request(args, "POST", f"/addons/{addon_slug}/restart")
            return 0
        except HassioApiError as exc:
            if exc.status not in {400, 404, 405}:
                raise
            print(f"Restart endpoint returned {exc.status}; falling back to stop/start.", file=sys.stderr)
            try:
                hassio_api_request(args, "POST", f"/addons/{addon_slug}/stop")
            except HassioApiError as stop_exc:
                print(f"Stop during restart fallback returned {stop_exc.status}; continuing to start: {stop_exc.detail}", file=sys.stderr)
            hassio_api_request(args, "POST", f"/addons/{addon_slug}/start")
            return 0
    hassio_api_request(args, "POST", f"/addons/{addon_slug}/{action}")
    return 0


def hassio_fetch_addon_logs(args, addon_slug):
    payload = hassio_api_request(args, "GET", f"/addons/{addon_slug}/logs")
    raw = payload.get("raw") if isinstance(payload, dict) else None
    if raw is None:
        raw = json.dumps(payload, indent=2, sort_keys=True)
    return raw


def tail_addon_logs(raw, logs_tail=0):
    if logs_tail:
        lines = raw.splitlines()
        return "\n".join(lines[-logs_tail:])
    return raw


def hassio_print_addon_logs(args, addon_slug, raw=None):
    if raw is None:
        raw = hassio_fetch_addon_logs(args, addon_slug)
    if getattr(args, "logs_tail", 0):
        raw = tail_addon_logs(raw, args.logs_tail)
    if raw:
        print(raw)
    return raw


def addon_log_gate_scope(raw, operation):
    marker = f"{ADDON_LOG_GATE_PREFIX} {operation}"
    index = raw.rfind(marker)
    if index < 0:
        return raw, False
    return raw[index:], True


def addon_log_gate_match(raw, operation):
    marker_sets = ADDON_LOG_GATE_MARKERS.get(operation)
    if not marker_sets:
        return True, "no log marker is defined for this operation"

    scoped_raw, found_start_marker = addon_log_gate_scope(raw, operation)
    if not found_start_marker:
        return False, f"missing start marker: {ADDON_LOG_GATE_PREFIX} {operation}"

    for marker_set in marker_sets:
        missing = [marker for marker in marker_set if marker not in scoped_raw]
        if not missing:
            return True, "matched " + " + ".join(marker_set)

    expected = " OR ".join(" + ".join(marker_set) for marker_set in marker_sets)
    return False, f"missing success marker after latest start marker; expected {expected}"


def wait_for_addon_log_gate(args, addon_slug, operation):
    if operation not in ADDON_LOG_GATE_MARKERS:
        print(f"No log gate verification marker is defined for operation={operation}; skipping marker check.")
        return 0, ""
    if getattr(args, "dry_run", False):
        expected = " OR ".join(" + ".join(marker_set) for marker_set in ADDON_LOG_GATE_MARKERS[operation])
        print(f"Dry run log gate for operation={operation}: would wait for {expected}")
        return 0, ""

    timeout = max(0.0, float(getattr(args, "gate_timeout", 120.0) or 0.0))
    interval = max(0.25, float(getattr(args, "gate_poll_interval", 5.0) or 5.0))
    delay = max(0.0, float(getattr(args, "logs_delay", 0.0) or 0.0))
    if delay > 0:
        time.sleep(delay)

    deadline = time.monotonic() + timeout
    last_raw = ""
    last_detail = ""
    while True:
        last_raw = hassio_fetch_addon_logs(args, addon_slug)
        matched, detail = addon_log_gate_match(last_raw, operation)
        last_detail = detail
        if matched:
            print(f"Verified operation={operation} log gate: {detail}")
            return 0, last_raw
        if time.monotonic() >= deadline:
            print(f"Operation={operation} did not pass its log gate within {timeout:.1f}s: {last_detail}", file=sys.stderr)
            return 1, last_raw
        time.sleep(interval)


def local_lan_addresses(preferred_host=None, preferred_port=None):
    addresses = []

    def add(address):
        if not address or address.startswith("127.") or address == "0.0.0.0":
            return
        if address not in addresses:
            addresses.append(address)

    probes = []
    if preferred_host and preferred_port:
        probes.append((preferred_host, preferred_port))
    probes.append(("1.1.1.1", 80))
    for host, port in probes:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((host, int(port)))
                add(sock.getsockname()[0])
        except OSError:
            pass

    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_DGRAM):
            add(result[4][0])
    except OSError:
        pass

    return addresses


def addon_git_repository_urls(args, repo_name):
    host_hint = getattr(args, "ha_host", None)
    addresses = local_lan_addresses(host_hint, 8123)
    urls = [f"http://{address}:{args.port}/{repo_name}" for address in addresses]
    urls.append(f"http://localhost:{args.port}/{repo_name}")
    return urls


def addon_git_repo_name(args):
    repo_name = args.repo_name
    return repo_name if repo_name.endswith(".git") else f"{repo_name}.git"


def addon_git_repo_paths(args):
    output_dir = Path(args.output_dir).expanduser().resolve()
    repo_name = addon_git_repo_name(args)
    return output_dir, repo_name, output_dir / repo_name


def addon_git_repo_missing_files(output_dir, repo_name):
    bare_repo = output_dir / repo_name
    checks = {
        "HEAD": bare_repo / "HEAD",
        "objects/": bare_repo / "objects",
        "refs/": bare_repo / "refs",
        "info/refs": bare_repo / "info" / "refs",
    }
    return [label for label, path in checks.items() if not path.exists()]


def print_addon_git_repo_urls(args, repo_name):
    print("Then add one of these repository URLs in Home Assistant Settings -> Add-ons -> Add-on Store -> Repositories:")
    for url in addon_git_repository_urls(args, repo_name):
        print(f"  {url}")


class AddonGitRepoRequestHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, message_format, *message_args):
        print(f"{self.client_address[0]} - {message_format % message_args}")


def create_addon_git_repo_server(args, output_dir):
    handler = functools.partial(AddonGitRepoRequestHandler, directory=str(output_dir))
    return http.server.ThreadingHTTPServer((args.bind, args.port), handler)


def addon_ssh_target(args):
    return f"{args.ssh_user}@{args.ha_host}" if args.ssh_user else args.ha_host


def addon_ssh_options(args):
    options = []
    timeout = getattr(args, "ssh_connect_timeout", None)
    if timeout:
        options.extend(["-o", f"ConnectTimeout={timeout}"])
    if bool(getattr(args, "ssh_batch_mode", False)):
        options.extend(["-o", "BatchMode=yes"])
    return options


def addon_ssh_command(args, remote_command):
    return ["ssh", *addon_ssh_options(args), addon_ssh_target(args), remote_command]


def addon_scp_command(args, source, target):
    return ["scp", *addon_ssh_options(args), source, target]


def addon_cli_action_shell(action, slug):
    slug_arg = shlex.quote(slug)
    if action == "restart":
        apps_action = f"ha apps restart {slug_arg} || (ha apps stop {slug_arg} || true; ha apps start {slug_arg})"
        addons_action = f"ha addons restart {slug_arg} || (ha addons stop {slug_arg} || true; ha addons start {slug_arg})"
    else:
        apps_action = f"ha apps {shlex.quote(action)} {slug_arg}"
        addons_action = f"ha addons {shlex.quote(action)} {slug_arg}"

    return (
        "if ha apps --help >/dev/null 2>&1; then "
        f"{apps_action}; "
        "elif ha addons --help >/dev/null 2>&1; then "
        f"{addons_action}; "
        "else "
        "echo 'Home Assistant CLI has neither apps nor addons namespace' >&2; "
        "exit 127; "
        "fi"
    )


def addon_host_cli_namespace_shell():
    return (
        "if ha apps --help >/dev/null 2>&1; then "
        "echo apps; "
        "elif ha addons --help >/dev/null 2>&1; then "
        "echo addons; "
        "else "
        "echo missing; "
        "exit 127; "
        "fi"
    )


def validate_uuid(value):
    return str(UUID(str(value)))


def replace_device_uuid(text, device_id, uuid):
    lines = text.splitlines(keepends=True)
    in_mesh = False
    mesh_indent = None
    in_device = False
    device_indent = None

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip(" "))
        if in_device and indent <= device_indent:
            in_device = False
        if in_mesh and indent <= mesh_indent and stripped != "mesh:":
            in_mesh = False

        if stripped == "mesh:":
            in_mesh = True
            mesh_indent = indent
            continue

        if in_mesh and stripped == f"{device_id}:":
            in_device = True
            device_indent = indent
            continue

        if in_device and stripped.startswith("uuid:"):
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = f"{' ' * indent}uuid: {uuid}{newline}"
            return "".join(lines)

    raise ValueError(f"Could not find mesh.{device_id}.uuid in config.")


def set_config_uuid(config_path, device_id, uuid):
    uuid = validate_uuid(uuid)
    config_path = resolve_repo_path(config_path)
    updated = replace_device_uuid(config_path.read_text(encoding="utf-8"), device_id, uuid)
    config_path.write_text(updated, encoding="utf-8")
    return uuid


def docker_compose_command(args, *command, mode=None):
    env = {}
    if mode:
        env["GATEWAY_MODE"] = mode
    return run_command(["docker", "compose", *command], cwd=resolve_repo_path(args.compose_dir), env=env, dry_run=args.dry_run)


def docker_compose_exec_command(args, *command):
    return docker_compose_command(args, "exec", "-T", "app", *command)


def copy_config(args):
    source = resolve_repo_path(args.sample)
    target = resolve_repo_path(args.config)
    print(f"$ cp {shlex.quote(str(source))} {shlex.quote(str(target))}")
    if args.dry_run:
        return 0
    if target.exists() and not args.force:
        print(f"{target} already exists; use --force to overwrite.", file=sys.stderr)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return 0


def preflight(args):
    command = [
        "python3",
        "scripts/pesetech_preflight.py",
        "--config",
        args.config,
        "--store",
        getattr(args, "store", "docker/config/store.yaml"),
    ]
    if args.host:
        command.append("--host")
    if not getattr(args, "skip_mqtt_connect_check", False):
        command.append("--check-mqtt")
        command.extend(["--mqtt-connect-timeout", str(getattr(args, "mqtt_connect_timeout", 3.0))])
    return run_command(command, dry_run=args.dry_run)


def build(args):
    return docker_compose_command(args, "build")


def shell(args):
    return docker_compose_command(args, "up", "-d", "--force-recreate", mode="shell")


def scan(args):
    return docker_compose_exec_command(args, "python3", "gateway.py", "--basedir", "/config", "scan")


def set_uuid(args):
    if args.dry_run:
        uuid = validate_uuid(args.uuid)
        print(f"Would set mesh.{args.device_id}.uuid in {args.config} to {uuid}")
        return 0
    uuid = set_config_uuid(args.config, args.device_id, args.uuid)
    print(f"Set mesh.{args.device_id}.uuid to {uuid}")
    return 0


def import_mesh(args):
    command = [
        "python3",
        "scripts/pesetech_import_telink_mesh.py",
        args.mesh_json,
        "--config",
        args.config,
        "--store",
        args.store,
        "--device-id",
        args.device_id,
        "--device-name",
        args.device_name,
    ]
    if args.default_entity_id:
        command.extend(["--default-entity-id", args.default_entity_id])
    if getattr(args, "mesh_candidate", None):
        command.extend(["--mesh-candidate", str(args.mesh_candidate)])
    if args.node_uuid:
        command.extend(["--node-uuid", args.node_uuid])
    if args.node_unicast:
        command.extend(["--node-unicast", args.node_unicast])
    if args.local_address:
        command.extend(["--local-address", args.local_address])
    if args.force:
        command.append("--force")
    return run_command(command, dry_run=args.dry_run)


def extract_mesh(args):
    command = [
        "python3",
        "scripts/pesetech_extract_mesh_json.py",
        *args.inputs,
    ]
    if args.output:
        command.extend(["--output", args.output])
    if args.candidate:
        command.extend(["--candidate", str(args.candidate)])
    if args.list:
        command.append("--list")
    if args.no_recursive:
        command.append("--no-recursive")
    if args.max_bytes:
        command.extend(["--max-bytes", str(args.max_bytes)])
    return run_command(command, dry_run=args.dry_run)


def fetch_cloud_mesh(args):
    command = [
        "python3",
        "scripts/pesetech_fetch_cloud_mesh.py",
    ]
    if args.output:
        command.extend(["--output", args.output])
    if args.candidate:
        command.extend(["--candidate", str(args.candidate)])
    if args.list:
        command.append("--list")
    if args.raw_output:
        command.extend(["--raw-output", args.raw_output])
    if getattr(args, "report_output", None):
        command.extend(["--report-output", args.report_output])
    if getattr(args, "region", None):
        command.extend(["--region", args.region])
    if args.base_url:
        command.extend(["--base-url", args.base_url])
    for endpoint in args.endpoint or []:
        command.extend(["--endpoint", endpoint])
    for home_id in getattr(args, "home_id", None) or []:
        command.extend(["--home-id", home_id])
    if args.token_file:
        command.extend(["--token-file", args.token_file])
    if args.token_env:
        command.extend(["--token-env", args.token_env])
    if args.username_file:
        command.extend(["--username-file", args.username_file])
    if args.username_env:
        command.extend(["--username-env", args.username_env])
    if args.password_file:
        command.extend(["--password-file", args.password_file])
    if args.password_env:
        command.extend(["--password-env", args.password_env])
    if args.user_origin:
        command.extend(["--user-origin", str(args.user_origin)])
    if args.timeout:
        command.extend(["--timeout", str(args.timeout)])
    if args.user_agent:
        command.extend(["--user-agent", args.user_agent])
    if args.accept_language:
        command.extend(["--accept-language", args.accept_language])
    return run_command(command, dry_run=args.dry_run)


def provision(args):
    uuid = validate_uuid(args.uuid)
    if args.update_config:
        set_config_uuid(args.config, args.device_id, uuid)
    commands = [
        ["python3", "gateway.py", "--basedir", "/config", "prov", "--uuid", uuid, "add"],
        ["python3", "gateway.py", "--basedir", "/config", "prov", "--uuid", uuid, "config"],
        ["python3", "gateway.py", "--basedir", "/config", "prov", "list"],
    ]
    for command in commands:
        exit_code = docker_compose_exec_command(args, *command)
        if exit_code:
            return exit_code
    return 0


def service(args):
    return docker_compose_command(args, "up", "-d", "--force-recreate", mode="service")


def proof_log_paths(args):
    paths = []
    for attribute in ("proof_log", "ha_proof_log", "final_audit_report"):
        path = getattr(args, attribute, None)
        if path and path not in paths:
            paths.append(path)
    return paths


def default_proof_run_id():
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"pesetech-{timestamp}-{uuid4().hex[:8]}"


def ensure_proof_run_id(args):
    if not getattr(args, "proof_run_id", None):
        args.proof_run_id = default_proof_run_id()
    return args.proof_run_id


def prepare_proof_logs(args):
    if getattr(args, "keep_proof_logs", False):
        return 0

    for path in proof_log_paths(args):
        target = resolve_repo_path(path)
        print(f"$ rm -f {shlex.quote(str(target))}")
        if getattr(args, "dry_run", False):
            continue
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f"Could not remove stale proof log {target}: {exc}", file=sys.stderr)
            return 1
    return 0


def wait_service_ready(args):
    timeout = float(getattr(args, "service_ready_timeout", 30.0))
    cwd = resolve_repo_path(args.compose_dir)
    command = ["docker", "compose", "exec", "-T", "app", "true"]
    print(f"$ cd {shlex.quote(str(cwd))} && wait up to {timeout:g}s for {env_command(command)}")
    if args.dry_run:
        return 0

    deadline = time.monotonic() + max(timeout, 0.0)
    last_result = None
    while True:
        last_result = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if last_result.returncode == 0:
            return 0
        if time.monotonic() >= deadline:
            break
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

    print("Docker app service did not become exec-ready before the timeout.", file=sys.stderr)
    for label, output in (("stdout", last_result.stdout), ("stderr", last_result.stderr)):
        output = (output or "").strip()
        if output:
            print(f"Last readiness {label}:\n{output}", file=sys.stderr)
    return last_result.returncode or 1


def logs(args):
    command = ["logs"]
    if args.follow:
        command.append("-f")
    command.append("app")
    return docker_compose_command(args, *command)


def runtime_check(args):
    return docker_compose_exec_command(
        args,
        "python3",
        "/opt/hass-ble-mesh/scripts/pesetech_runtime_check.py",
    )


def discovery(args):
    command = [
        "python3",
        "scripts/pesetech_mqtt_discovery.py",
        "--config",
        args.config,
        "--require-retained",
    ]
    if args.broker:
        command.extend(["--broker", args.broker])
    if getattr(args, "port", None) is not None:
        command.extend(["--port", str(args.port)])
    if args.username:
        command.extend(["--username", args.username])
    if args.password:
        command.extend(["--password", args.password])
    if args.discovery_prefix:
        command.extend(["--discovery-prefix", args.discovery_prefix])
    if args.mesh_topic:
        command.extend(["--mesh-topic", args.mesh_topic])
    if args.device_id:
        command.extend(["--device-id", args.device_id])
    if getattr(args, "default_entity_id", None):
        command.extend(["--default-entity-id", args.default_entity_id])
    if getattr(args, "discovery_timeout", None) is not None:
        command.extend(["--discovery-timeout", str(args.discovery_timeout)])
    if getattr(args, "candidate_timeout", None) is not None:
        command.extend(["--candidate-timeout", str(args.candidate_timeout)])
    if args.dump_json:
        command.append("--dump-json")
    return run_command(command, dry_run=args.dry_run)


def smoke(args):
    command = [
        "python3",
        "scripts/pesetech_mqtt_smoke.py",
        "--config",
        args.config,
        "--proof-log",
        args.proof_log,
        "--wait-state",
        "--observe",
    ]
    if args.broker:
        command.extend(["--broker", args.broker])
    if getattr(args, "port", None) is not None:
        command.extend(["--port", str(args.port)])
    if args.username:
        command.extend(["--username", args.username])
    if args.password:
        command.extend(["--password", args.password])
    if args.discovery_prefix:
        command.extend(["--discovery-prefix", args.discovery_prefix])
    if args.mesh_topic:
        command.extend(["--mesh-topic", args.mesh_topic])
    if args.device_id:
        command.extend(["--device-id", args.device_id])
    if getattr(args, "proof_run_id", None):
        command.extend(["--run-id", args.proof_run_id])
    if getattr(args, "precondition_visible_start", False):
        command.append("--precondition-visible-start")
    return run_command(command, dry_run=args.dry_run)


def verify(args):
    command = [
        "python3",
        "scripts/pesetech_verify_proof.py",
        args.proof_log,
        "--config",
        args.config,
    ]
    if args.discovery_prefix:
        command.extend(["--discovery-prefix", args.discovery_prefix])
    if args.mesh_topic:
        command.extend(["--mesh-topic", args.mesh_topic])
    if args.device_id:
        command.extend(["--device-id", args.device_id])
    if getattr(args, "proof_run_id", None):
        command.extend(["--run-id", args.proof_run_id])
    return run_command(command, dry_run=args.dry_run)


def append_optional_arg(command, flag, value):
    if value is not None:
        command.extend([flag, str(value)])


def append_ha_service_mqtt_overrides(command, args):
    mappings = (
        ("--mqtt-broker", "ha_mqtt_broker", "broker"),
        ("--mqtt-port", "ha_mqtt_port", "port"),
        ("--mqtt-username", "ha_mqtt_username", "username"),
        ("--mqtt-password", "ha_mqtt_password", "password"),
        ("--mqtt-discovery-prefix", "ha_mqtt_discovery_prefix", "discovery_prefix"),
        ("--mqtt-mesh-topic", "ha_mqtt_mesh_topic", "mesh_topic"),
        ("--mqtt-device-id", "ha_mqtt_device_id", "device_id"),
        ("--mqtt-brightness-scale", "ha_mqtt_brightness_scale", None),
        ("--mqtt-brightness-tolerance", "ha_mqtt_brightness_tolerance", None),
        ("--mqtt-mired-tolerance", "ha_mqtt_mired_tolerance", None),
    )
    for flag, ha_attr, prove_attr in mappings:
        value = getattr(args, ha_attr, None)
        if value is None and prove_attr is not None:
            value = getattr(args, prove_attr, None)
        append_optional_arg(command, flag, value)


def ha_service(args):
    command = [
        "python3",
        "scripts/pesetech_ha_service_smoke.py",
        "--url",
        args.ha_url,
        "--entity-id",
        args.ha_entity_id,
        "--proof-log",
        args.ha_proof_log,
    ]
    if getattr(args, "proof_run_id", None):
        command.extend(["--run-id", args.proof_run_id])
    if getattr(args, "ha_precondition_visible_start", False):
        command.append("--precondition-visible-start")
    if getattr(args, "ha_token_file", None):
        command.extend(["--token-file", args.ha_token_file])
    if getattr(args, "ha_list_candidates", False):
        command.append("--list-candidates")
    else:
        if not getattr(args, "ha_no_wait_state", False):
            command.append("--wait-state")
        if getattr(args, "ha_wait_attributes", False):
            command.append("--wait-attributes")
        if not getattr(args, "ha_no_observe", False):
            command.append("--observe")
    if getattr(args, "ha_candidate_search", None):
        command.extend(["--candidate-search", args.ha_candidate_search])
    if getattr(args, "ha_wait_mqtt_state", False) or getattr(args, "ha_wait_mqtt_attributes", False):
        command.extend(["--wait-mqtt-state", "--mqtt-config", args.config])
        append_ha_service_mqtt_overrides(command, args)
    if getattr(args, "ha_wait_mqtt_attributes", False):
        command.append("--wait-mqtt-attributes")
    return run_command(command, dry_run=args.dry_run)


def ha_api_check(args):
    command = [
        "python3",
        "scripts/pesetech_ha_service_smoke.py",
        "--url",
        args.ha_url,
        "--check-api",
    ]
    if getattr(args, "ha_token_file", None):
        command.extend(["--token-file", args.ha_token_file])
    return run_command(command, dry_run=args.dry_run)


def ha_entity_check(args):
    command = [
        "python3",
        "scripts/pesetech_ha_service_smoke.py",
        "--url",
        args.ha_url,
        "--entity-id",
        args.ha_entity_id,
        "--check-entity",
    ]
    candidate_search = getattr(args, "ha_candidate_search", None)
    if candidate_search is None and getattr(args, "ha_entity_id", None):
        candidate_search = args.ha_entity_id.split(".", 1)[-1]
    if candidate_search:
        command.extend(["--candidate-search", candidate_search])
    if getattr(args, "ha_entity_timeout", None) is not None:
        command.extend(["--entity-timeout", str(args.ha_entity_timeout)])
    if getattr(args, "ha_token_file", None):
        command.extend(["--token-file", args.ha_token_file])
    return run_command(command, dry_run=args.dry_run)


def ha_verify(args):
    command = [
        "python3",
        "scripts/pesetech_verify_ha_service_proof.py",
        args.ha_proof_log,
        "--url",
        args.ha_url,
        "--entity-id",
        args.ha_entity_id,
    ]
    if getattr(args, "proof_run_id", None):
        command.extend(["--run-id", args.proof_run_id])
    if getattr(args, "ha_wait_attributes", False):
        command.append("--require-attributes")
    if getattr(args, "ha_allow_missing_state", False):
        command.append("--allow-missing-state")
    if getattr(args, "ha_require_mqtt_state", False) or getattr(args, "ha_wait_mqtt_state", False):
        command.append("--require-mqtt-state")
    if getattr(args, "ha_require_mqtt_attributes", False) or getattr(args, "ha_wait_mqtt_attributes", False):
        command.append("--require-mqtt-attributes")
    if getattr(args, "ha_mqtt_brightness_scale", None) is not None:
        command.extend(["--mqtt-brightness-scale", str(args.ha_mqtt_brightness_scale)])
    if getattr(args, "ha_mqtt_brightness_tolerance", None) is not None:
        command.extend(["--mqtt-brightness-tolerance", str(args.ha_mqtt_brightness_tolerance)])
    if getattr(args, "ha_mqtt_mired_tolerance", None) is not None:
        command.extend(["--mqtt-mired-tolerance", str(args.ha_mqtt_mired_tolerance)])
    if getattr(args, "ha_allow_service_error", False):
        command.append("--allow-service-error")
    if getattr(args, "ha_allow_unobserved", False):
        command.append("--allow-unobserved")
    return run_command(command, dry_run=args.dry_run)


def final_audit(args):
    command = [
        "python3",
        "scripts/pesetech_real_device_audit.py",
        "--config",
        args.config,
        "--proof-log",
        args.proof_log,
        "--ha-proof-log",
        args.ha_proof_log,
        "--ha-url",
        args.ha_url,
        "--ha-entity-id",
        args.ha_entity_id,
    ]
    if getattr(args, "proof_run_id", None):
        command.extend(["--proof-run-id", args.proof_run_id])
    if getattr(args, "discovery_prefix", None):
        command.extend(["--discovery-prefix", args.discovery_prefix])
    if getattr(args, "mesh_topic", None):
        command.extend(["--mesh-topic", args.mesh_topic])
    if getattr(args, "device_id", None):
        command.extend(["--device-id", args.device_id])
    if getattr(args, "ha_mqtt_brightness_scale", None) is not None:
        command.extend(["--ha-mqtt-brightness-scale", str(args.ha_mqtt_brightness_scale)])
    if getattr(args, "ha_mqtt_brightness_tolerance", None) is not None:
        command.extend(["--ha-mqtt-brightness-tolerance", str(args.ha_mqtt_brightness_tolerance)])
    if getattr(args, "ha_mqtt_mired_tolerance", None) is not None:
        command.extend(["--ha-mqtt-mired-tolerance", str(args.ha_mqtt_mired_tolerance)])
    if getattr(args, "allow_unobserved", False):
        command.append("--allow-unobserved")
    if getattr(args, "allow_different_run_ids", False):
        command.append("--allow-different-run-ids")
    if getattr(args, "final_audit_report", None):
        command.extend(["--output-json", args.final_audit_report])
    return run_command(command, dry_run=args.dry_run)


def diagnostics(args):
    command = [
        "python3",
        "scripts/pesetech_diagnostics.py",
        "--config",
        args.config,
        "--store",
        args.store,
        "--proof-log",
        args.proof_log,
        "--compose-dir",
        args.compose_dir,
        "--live-discovery",
    ]
    if getattr(args, "ha_proof_log", None):
        command.extend(["--ha-proof-log", args.ha_proof_log])
    if getattr(args, "final_audit_report", None):
        command.extend(["--final-audit-report", args.final_audit_report])
    if getattr(args, "import_check_report", None):
        command.extend(["--import-check-report", args.import_check_report])
    if getattr(args, "readiness_report", None):
        command.extend(["--readiness-report", args.readiness_report])
    if getattr(args, "status_report", None):
        command.extend(["--status-report", args.status_report])
    if getattr(args, "ha_url", None):
        command.extend(["--ha-url", args.ha_url])
    if getattr(args, "ha_entity_id", None):
        command.extend(["--ha-entity-id", args.ha_entity_id])
    if getattr(args, "ha_token_file", None):
        command.extend(["--ha-token-file", args.ha_token_file])
    command.append("--ha-api-context")
    if getattr(args, "ha_candidate_search", None):
        command.extend(["--ha-candidate-search", args.ha_candidate_search])
    if getattr(args, "ha_require_attributes", False) or getattr(args, "ha_wait_attributes", False):
        command.append("--ha-require-attributes")
    if getattr(args, "ha_require_mqtt_state", False) or getattr(args, "ha_wait_mqtt_state", False):
        command.append("--ha-require-mqtt-state")
    if getattr(args, "ha_require_mqtt_attributes", False) or getattr(args, "ha_wait_mqtt_attributes", False):
        command.append("--ha-require-mqtt-attributes")
    if getattr(args, "ha_mqtt_brightness_scale", None) is not None:
        command.extend(["--ha-mqtt-brightness-scale", str(args.ha_mqtt_brightness_scale)])
    if getattr(args, "ha_mqtt_brightness_tolerance", None) is not None:
        command.extend(["--ha-mqtt-brightness-tolerance", str(args.ha_mqtt_brightness_tolerance)])
    if getattr(args, "ha_mqtt_mired_tolerance", None) is not None:
        command.extend(["--ha-mqtt-mired-tolerance", str(args.ha_mqtt_mired_tolerance)])
    if getattr(args, "broker", None):
        command.extend(["--broker", args.broker])
    if getattr(args, "port", None) is not None:
        command.extend(["--port", str(args.port)])
    if getattr(args, "username", None):
        command.extend(["--username", args.username])
    if getattr(args, "password", None):
        command.extend(["--password", args.password])
    if getattr(args, "discovery_prefix", None):
        command.extend(["--discovery-prefix", args.discovery_prefix])
    if getattr(args, "mesh_topic", None):
        command.extend(["--mesh-topic", args.mesh_topic])
    if getattr(args, "device_id", None):
        command.extend(["--device-id", args.device_id])
    if getattr(args, "candidate_timeout", None) is not None:
        command.extend(["--candidate-timeout", str(args.candidate_timeout)])
    if getattr(args, "cloud_output", None):
        command.extend(["--cloud-output", args.cloud_output])
    if getattr(args, "cloud_raw_output", None):
        command.extend(["--cloud-raw-output", args.cloud_raw_output])
    if getattr(args, "cloud_report", None):
        command.extend(["--cloud-report", args.cloud_report])
    if getattr(args, "cloud_token_file", None):
        command.extend(["--cloud-token-file", args.cloud_token_file])
    if getattr(args, "cloud_username_file", None):
        command.extend(["--cloud-username-file", args.cloud_username_file])
    if getattr(args, "cloud_password_file", None):
        command.extend(["--cloud-password-file", args.cloud_password_file])
    if getattr(args, "cloud_region", None):
        command.extend(["--cloud-region", args.cloud_region])
    if getattr(args, "cloud_candidate", None):
        command.extend(["--cloud-candidate", str(args.cloud_candidate)])
    if getattr(args, "cloud_home_id", None):
        command.extend(["--cloud-home-id", args.cloud_home_id])
    if getattr(args, "import_mesh_candidate", None):
        command.extend(["--import-mesh-candidate", str(args.import_mesh_candidate)])
    if getattr(args, "proof_run_id", None):
        command.extend(["--proof-run-id", args.proof_run_id])
    if getattr(args, "skip_docker", False):
        command.append("--skip-docker")
    return run_command(command, dry_run=args.dry_run)


def apply_ha_service_proof_defaults(args):
    if not getattr(args, "ha_service", False):
        return
    if getattr(args, "ha_relaxed_state_proof", False):
        return

    if not getattr(args, "ha_no_wait_state", False):
        args.ha_wait_attributes = True
    args.ha_wait_mqtt_state = True
    args.ha_wait_mqtt_attributes = True


def prove(args):
    apply_ha_service_proof_defaults(args)
    if getattr(args, "final_audit", False) and not getattr(args, "ha_service", False):
        print("--final-audit requires --ha-service so both proof logs are produced in the same run.", file=sys.stderr)
        return 2
    if proof_log_paths(args):
        ensure_proof_run_id(args)
    steps = []
    if proof_log_paths(args) and not getattr(args, "keep_proof_logs", False):
        steps.append(("prepare-proof-logs", prepare_proof_logs))
    if getattr(args, "ha_service", False):
        steps.append(("ha-api-check", ha_api_check))
    steps.append(("preflight", preflight))
    if getattr(args, "start_service", False):
        steps.append(("service", service))
        steps.append(("wait-service", wait_service_ready))
    steps.extend(
        [
            ("runtime-check", runtime_check),
            ("discovery", discovery),
        ]
    )
    if getattr(args, "ha_service", False):
        steps.append(("ha-entity-check", ha_entity_check))
    steps.extend(
        [
            ("smoke", smoke),
            ("verify", verify),
        ]
    )
    if getattr(args, "ha_service", False):
        steps.append(("ha-service", ha_service))
        steps.append(("ha-verify", ha_verify))
        if getattr(args, "final_audit", False):
            steps.append(("final-audit", final_audit))

    for name, handler in steps:
        print(f"\n== {name} ==")
        exit_code = handler(args)
        if exit_code:
            print(f"{name} failed with exit code {exit_code}", file=sys.stderr)
            if not args.no_diagnostics:
                print("\n== diagnostics ==")
                diagnostics(args)
            return exit_code

    if args.diagnostics_on_success:
        print("\n== diagnostics ==")
        return diagnostics(args)

    return 0


def prove_ha_addon(args):
    args.ha_service = True
    ha_entity_id = getattr(args, "ha_entity_id", None)
    if not getattr(args, "default_entity_id", None) and ha_entity_id:
        args.default_entity_id = ha_entity_id
    if not getattr(args, "ha_candidate_search", None) and ha_entity_id:
        args.ha_candidate_search = ha_entity_id.split(".", 1)[-1]
    apply_ha_service_proof_defaults(args)
    readiness_only = getattr(args, "readiness_only", False)
    if proof_log_paths(args) and not readiness_only:
        ensure_proof_run_id(args)

    steps = []
    if proof_log_paths(args) and not readiness_only and not getattr(args, "keep_proof_logs", False):
        steps.append(("prepare-proof-logs", prepare_proof_logs))
    steps.extend(
        [
            ("ha-api-check", ha_api_check),
            ("discovery", discovery),
            ("ha-entity-check", ha_entity_check),
        ]
    )
    if not readiness_only:
        steps.extend(
            [
                ("smoke", smoke),
                ("verify", verify),
                ("ha-service", ha_service),
                ("ha-verify", ha_verify),
            ]
        )
    if not readiness_only and not getattr(args, "no_final_audit", False):
        steps.append(("final-audit", final_audit))

    for name, handler in steps:
        print(f"\n== {name} ==")
        exit_code = handler(args)
        if exit_code:
            print(f"{name} failed with exit code {exit_code}", file=sys.stderr)
            if not args.no_diagnostics:
                print("\n== diagnostics ==")
                diagnostics(args)
            return exit_code

    if args.diagnostics_on_success:
        if readiness_only:
            print_readiness_success(args)
        print("\n== diagnostics ==")
        return diagnostics(args)

    if readiness_only:
        print_readiness_success(args)

    return 0


def print_readiness_success(args):
    entity = getattr(args, "ha_entity_id", None) or "the configured light entity"
    print("\nReadiness check passed without sending light-control commands.")
    print(f"Next: rerun the same prove-ha-addon command without --readiness-only while watching {entity}.")


def addon_proof_command(args, *, readiness_only):
    command = [
        "python3",
        "scripts/pesetech_hardware_session.py",
        "prove-ha-addon",
    ]
    if readiness_only:
        command.append("--readiness-only")
    command.extend(["--ha-url", args.ha_url, "--ha-entity-id", args.ha_entity_id])
    if args.broker:
        command.extend(["--broker", args.broker])
    if args.port:
        command.extend(["--port", str(args.port)])
    if args.mqtt_auth:
        command.extend(["--username", "$MQTT_USERNAME", "--password", "$MQTT_PASSWORD"])
    if args.discovery_prefix:
        command.extend(["--discovery-prefix", args.discovery_prefix])
    if args.mesh_topic:
        command.extend(["--mesh-topic", args.mesh_topic])
    if args.device_id:
        command.extend(["--device-id", args.device_id])
    if args.candidate_timeout:
        command.extend(["--candidate-timeout", str(args.candidate_timeout)])
    return command


def print_operation_sequence(title, operations):
    print(title)
    for operation, motion, detail in operations:
        motion_label = "moves real skylight" if motion else "no motion"
        print(f"  - operation: {operation} ({motion_label}) - {detail}")


def addon_runbook(args):
    output = getattr(args, "output", None)
    if output:
        output_path = resolve_repo_path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        render_args = argparse.Namespace(**vars(args))
        render_args.output = None
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            addon_runbook(render_args)
        output_path.write_text(buffer.getvalue(), encoding="utf-8")
        print(f"Wrote {output_path}")
        return 0

    ssh_target = addon_ssh_target(args)
    archive_name = Path(args.addon_archive).name
    remote_archive = f"/addons/{archive_name}"

    def operation_command(operation, run="restart", extra_args=None):
        command = [
            "python3",
            "scripts/pesetech_hardware_session.py",
            "addon-set-operation",
            "--ha-host",
            args.ha_host,
            operation,
            "--run",
            run,
        ]
        if extra_args:
            command.extend(extra_args)
        return command

    def target_args():
        return ["--ha-url", args.ha_url, "--ha-entity-id", args.ha_entity_id]

    cloud_fetch_args = ["--cloud-region", args.cloud_region]
    if args.cloud_home_id:
        cloud_fetch_args.extend(["--cloud-home-id", args.cloud_home_id])

    print("Pesetech Home Assistant add-on real-device runbook")
    print()
    print("Purpose:")
    print("  Install the generated add-on on the Home Assistant host with Bluetooth, import or provision the real skylight, then prove on/off, brightness, and color temperature.")
    print()
    print("Check the Home Assistant host before installing:")
    print(format_runbook_command(["python3", "scripts/pesetech_hardware_session.py", "addon-host-check", "--ha-host", args.ha_host, "--ha-url", args.ha_url]))
    print()
    print("Install the local add-on archive:")
    print(format_runbook_command(["python3", "scripts/pesetech_hardware_session.py", "addon-install", "--ha-host", args.ha_host, "--addon-archive", args.addon_archive]))
    print("  That helper runs the same SSH/SCP install shape as:")
    print(format_runbook_command(["ssh", ssh_target, "mkdir -p /addons"]))
    print(format_runbook_command(["scp", args.addon_archive, f"{ssh_target}:{remote_archive}"]))
    print(format_runbook_command(["ssh", ssh_target, f"tar -xzf {shlex.quote(remote_archive)} -C /addons"]))
    print("  Then in Home Assistant: Settings -> Add-ons -> Add-on Store -> Check for updates -> Local add-ons -> Pesetech BLE Mesh Gateway.")
    print("  If SSH is unavailable, serve a temporary Git repository URL for the Home Assistant add-on store:")
    print(format_runbook_command(["python3", "scripts/pesetech_hardware_session.py", "addon-serve-git-repo", "--repository-dir", "/tmp/pesetech-ha-addon", "--replace"]))
    print("  With a Home Assistant long-lived token, one command can serve the repository temporarily, install the add-on, start runtime-check, verify its pass marker, and stop the server:")
    print("  export HOME_ASSISTANT_TOKEN=<long-lived Home Assistant token>")
    print(format_runbook_command(["python3", "scripts/pesetech_hardware_session.py", "addon-ha-api-install-local-repo", "--repository-dir", "/tmp/pesetech-ha-addon", "--replace", "--ha-url", args.ha_url, "--operation", "runtime-check"]))
    print()
    print_operation_sequence(
        "No-motion setup gates:",
        [
            ("runtime-check", False, "verifies the bundled Python/Bluetooth Mesh API surface"),
            ("mesh-daemon-check", False, "proves D-Bus, BlueZ, bluetooth-meshd, and a visible hci* adapter"),
            ("ble-scan", False, "lists nearby BLE advertisements without Bluetooth Mesh, MQTT, provisioning, or light commands"),
            ("status", False, "prints the next safe operation and whether it can move the light"),
        ],
    )
    print("  Set the next add-on operation without opening the Configuration tab:")
    print(format_runbook_command(operation_command("runtime-check", run="start")))
    print(format_runbook_command(operation_command("mesh-daemon-check")))
    print(format_runbook_command(operation_command("ble-scan")))
    print(format_runbook_command(operation_command("status")))
    print("  The helper writes /share/pesetech_next_operation.json with non-secret operation fields only.")
    print("  Fetch status summary after operation=status:")
    print(format_runbook_command(["python3", "scripts/pesetech_hardware_session.py", "addon-fetch-status", "--ha-host", args.ha_host]))
    print("  Fetch any mirrored key-free operation report:")
    print(format_runbook_command(["python3", "scripts/pesetech_hardware_session.py", "addon-fetch-report", "import-check", "--ha-host", args.ha_host]))
    print()
    print_operation_sequence(
        "Preferred path for an already paired official-app skylight:",
        [
            ("cloud-fetch", False, "writes /share/pesetech_mesh.json from a token or credentials file"),
            ("import-check", False, "dry-runs the Telink/Pesetech mesh selection and writes /data/pesetech-import-check.json"),
            ("status", False, "must recommend import before you import"),
            ("import", False, "writes /data/config.yaml and /data/store.yaml, then starts the gateway"),
            ("preflight", False, "checks persisted files, Bluetooth host state, and MQTT reachability"),
            ("readiness-test", False, "starts the gateway and verifies MQTT discovery plus the HA entity without light-control commands"),
        ],
    )
    print("  Cloud token path: /share/pesetech_cloud_token.txt")
    print("  Cloud credentials paths: /share/pesetech_cloud_username.txt and /share/pesetech_cloud_password.txt")
    print("  Upload token file:")
    print(format_runbook_command(["python3", "scripts/pesetech_hardware_session.py", "addon-upload-cloud", "--ha-host", args.ha_host, "--token-file", "<local_token_file>"]))
    print("  Or upload username/password files:")
    print(format_runbook_command(["python3", "scripts/pesetech_hardware_session.py", "addon-upload-cloud", "--ha-host", args.ha_host, "--username-file", "<local_username_file>", "--password-file", "<local_password_file>"]))
    print("  Run cloud fetch after credentials are staged:")
    print(format_runbook_command(operation_command("cloud-fetch", extra_args=cloud_fetch_args)))
    print("  Fetch cloud report summary after operation=cloud-fetch:")
    print(format_runbook_command(["python3", "scripts/pesetech_hardware_session.py", "addon-fetch-cloud-report", "--ha-host", args.ha_host]))
    print(f"  Cloud region: {args.cloud_region}")
    if args.cloud_home_id:
        print(f"  Cloud home ID: set add-on cloud_home_id to {args.cloud_home_id}")
    else:
        print("  Cloud home ID: leave cloud_home_id blank to auto-discover home IDs from homeList")
        print("  If cloud-fetch finds homes but no mesh, run status or inspect /share/pesetech_cloud_fetch_report.json homes, then rerun with cloud_home_id set.")
    print("  If you already extracted mesh JSON yourself, upload it and start at import-check:")
    print(format_runbook_command(["python3", "scripts/pesetech_hardware_session.py", "addon-upload-mesh", "--ha-host", args.ha_host, "--mesh-json", "/tmp/pesetech_mesh.json"]))
    print("  Then run the no-motion import and readiness gates:")
    print(format_runbook_command(operation_command("import-check")))
    print(format_runbook_command(operation_command("status")))
    print(format_runbook_command(["python3", "scripts/pesetech_hardware_session.py", "addon-fetch-status", "--ha-host", args.ha_host]))
    print(format_runbook_command(operation_command("import")))
    print(format_runbook_command(operation_command("preflight")))
    print(format_runbook_command(operation_command("readiness-test", extra_args=target_args())))
    print()
    print_operation_sequence(
        "Watched movement proof gates:",
        [
            ("move-test", True, "direct MQTT on, brightness, warm, cool, and off proof"),
            ("service", False, "normal running mode after discovery exists"),
            ("ha-api-check", False, "checks the Home Assistant Core API/token and target entity before movement"),
            ("ha-service-test", True, "Home Assistant light service on, brightness, warm, cool, and off proof"),
        ],
    )
    print("  Run only after the no-motion gates pass:")
    print(format_runbook_command(operation_command("move-test")))
    print(format_runbook_command(operation_command("service")))
    print(format_runbook_command(operation_command("ha-api-check", extra_args=target_args())))
    print(format_runbook_command(operation_command("ha-service-test", extra_args=target_args())))
    print()
    print("Strict workstation proof after the add-on is running in service mode:")
    print("  export HOME_ASSISTANT_TOKEN=<long-lived Home Assistant token>")
    if args.mqtt_auth:
        print("  export MQTT_USERNAME=<mqtt username>")
        print("  export MQTT_PASSWORD=<mqtt password>")
    print("  No-motion readiness check:")
    print(format_runbook_command(addon_proof_command(args, readiness_only=True)))
    print("  Watched final proof:")
    print(format_runbook_command(addon_proof_command(args, readiness_only=False)))
    print()
    print("Token API operation shortcuts after the add-on is installed:")
    print("  export HOME_ASSISTANT_TOKEN=<long-lived Home Assistant token>")
    print(format_runbook_command(["python3", "scripts/pesetech_hardware_session.py", "addon-ha-api-operation", "runtime-check", "--run", "start", "--logs"]))
    print(format_runbook_command(["python3", "scripts/pesetech_hardware_session.py", "addon-ha-api-operation", "mesh-daemon-check", "--run", "restart", "--logs"]))
    print(format_runbook_command(["python3", "scripts/pesetech_hardware_session.py", "addon-ha-api-operation", "status", "--run", "restart", "--logs"]))
    print(format_runbook_command(["python3", "scripts/pesetech_hardware_session.py", "addon-ha-api-operation", "cloud-fetch", "--run", "restart", "--logs", "--cloud-region", args.cloud_region] + (["--cloud-home-id", args.cloud_home_id] if args.cloud_home_id else [])))
    print(format_runbook_command(["python3", "scripts/pesetech_hardware_session.py", "addon-ha-api-operation", "import-check", "--run", "restart", "--logs"]))
    print(format_runbook_command(["python3", "scripts/pesetech_hardware_session.py", "addon-ha-api-operation", "import", "--run", "restart", "--logs"]))
    print(format_runbook_command(["python3", "scripts/pesetech_hardware_session.py", "addon-ha-api-operation", "preflight", "--run", "restart", "--logs"]))
    print(format_runbook_command(["python3", "scripts/pesetech_hardware_session.py", "addon-ha-api-operation", "readiness-test", "--run", "restart", "--logs", "--addon-ha-url", args.ha_url, "--ha-entity-id", args.ha_entity_id]))
    print("  Or run the no-motion sequence through readiness-test; by default it verifies expected pass markers in the latest add-on log block:")
    print(format_runbook_command(["python3", "scripts/pesetech_hardware_session.py", "addon-ha-api-sequence", "--through", "readiness-test", "--cloud-region", args.cloud_region, "--addon-ha-url", args.ha_url, "--ha-entity-id", args.ha_entity_id] + (["--cloud-home-id", args.cloud_home_id] if args.cloud_home_id else [])))
    print()
    print("Failure path:")
    print("  Set add-on operation: diagnostics, start the add-on, retrieve /share/pesetech-diagnostics-*.tar.gz, then run:")
    print(format_runbook_command(["python3", "scripts/pesetech_hardware_session.py", "addon-fetch-diagnostics", "--ha-host", args.ha_host]))
    return 0


def addon_host_check(args):
    ssh_target = addon_ssh_target(args)
    remote_addons_dir = args.remote_addons_dir.rstrip("/") or DEFAULT_REMOTE_ADDONS_DIR
    remote_share_dir = args.remote_share_dir.rstrip("/") or DEFAULT_REMOTE_SHARE_DIR
    ha_url = getattr(args, "ha_url", "")
    ha_connect_timeout = getattr(args, "ha_connect_timeout", 5)
    ha_web_reachable = None
    required_failures = []
    warnings = []

    checks = [
        (
            "SSH login",
            "true",
            True,
            "ok",
        ),
        (
            "Home Assistant CLI add-on namespace",
            addon_host_cli_namespace_shell(),
            True,
            "apps",
        ),
        (
            f"{remote_addons_dir} directory is writable",
            f"mkdir -p {shlex.quote(remote_addons_dir)} && test -d {shlex.quote(remote_addons_dir)} && test -w {shlex.quote(remote_addons_dir)} && echo {shlex.quote(remote_addons_dir)}",
            True,
            remote_addons_dir,
        ),
        (
            f"{remote_share_dir} directory is writable",
            f"mkdir -p {shlex.quote(remote_share_dir)} && test -d {shlex.quote(remote_share_dir)} && test -w {shlex.quote(remote_share_dir)} && echo {shlex.quote(remote_share_dir)}",
            True,
            remote_share_dir,
        ),
        (
            "tar is available for extracting the local add-on archive",
            "command -v tar >/dev/null 2>&1 && echo tar",
            True,
            "tar",
        ),
        (
            "host Bluetooth adapter hint",
            "ls /sys/class/bluetooth/hci* 2>/dev/null | head -n 1",
            False,
            "/sys/class/bluetooth/hci0",
        ),
    ]

    print("Pesetech Home Assistant host check")
    print(f"Host: {ssh_target}")
    if ha_url:
        if args.dry_run:
            reachable, detail = True, "dry run"
        else:
            reachable, detail = check_http_reachable(ha_url, ha_connect_timeout)
        ha_web_reachable = bool(reachable)
        if reachable:
            print(f"PASS: Home Assistant web UI ({ha_url}; {detail})")
        else:
            warnings.append("Home Assistant web UI")
            print(f"WARN: Home Assistant web UI ({ha_url}) was not reachable: {detail}")
    for label, remote_command, required, placeholder in checks:
        code, output = run_capture_command(addon_ssh_command(args, remote_command), dry_run=args.dry_run, placeholder=placeholder)
        detail = output.strip().splitlines()[-1] if output.strip() else ""
        if code == 0:
            print(f"PASS: {label}" + (f" ({detail})" if detail else ""))
            continue
        if required:
            required_failures.append(label)
            print(f"FAIL: {label}")
            if label == "SSH login":
                break
        else:
            warnings.append(label)
            print(f"WARN: {label}; mesh-daemon-check inside the add-on will perform the authoritative Bluetooth test.")

    if required_failures:
        if required_failures == ["SSH login"] and ha_web_reachable:
            print(
                "Home Assistant web UI is reachable, but SSH is unavailable. Enable a Home Assistant SSH/SCP or file-share path to /addons and /share, or use the Git repository install path.",
                file=sys.stderr,
            )
        print("Host check failed. Fix the failed SSH, CLI, /addons, /share, or tar check before installing the add-on.", file=sys.stderr)
        return 1
    if warnings:
        print("Host check passed with warnings. Continue to install, then run add-on operation=mesh-daemon-check before scan/import or movement.")
    else:
        print("Host check passed. Continue with addon-install, then runtime-check and mesh-daemon-check.")
    return 0


def addon_prepare_git_repo(args):
    source_dir = Path(args.repository_dir).expanduser()
    output_dir, repo_name, bare_repo = addon_git_repo_paths(args)

    if not args.dry_run and not (source_dir / "repository.yaml").is_file():
        print(f"Generated add-on repository not found or missing repository.yaml: {source_dir}", file=sys.stderr)
        return 1
    if not args.dry_run and not shutil.which("git"):
        print("git is required to prepare a Home Assistant repository URL.", file=sys.stderr)
        return 1
    if bare_repo.exists():
        if not args.replace:
            print(f"Git repository already exists: {bare_repo}. Pass --replace to recreate it.", file=sys.stderr)
            return 1
        if not args.dry_run:
            shutil.rmtree(bare_repo)

    if args.dry_run:
        print(f"Would prepare bare Git repository from {source_dir} at {bare_repo}.")
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="pesetech-addon-git-") as temp_dir:
            worktree = Path(temp_dir) / "worktree"
            shutil.copytree(source_dir, worktree, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            commands = [
                (["git", "init", "--bare", str(bare_repo)], None),
                (["git", "init"], worktree),
                (["git", "config", "user.email", "pesetech-local@example.invalid"], worktree),
                (["git", "config", "user.name", "Pesetech Local Add-on Builder"], worktree),
                (["git", "add", "."], worktree),
                (["git", "commit", "-m", "Build Pesetech Home Assistant add-on repository"], worktree),
                (["git", "branch", "-M", "main"], worktree),
                (["git", "remote", "add", "origin", str(bare_repo)], worktree),
                (["git", "push", "-u", "origin", "main"], worktree),
                (["git", "-C", str(bare_repo), "symbolic-ref", "HEAD", "refs/heads/main"], None),
                (["git", "-C", str(bare_repo), "update-server-info"], None),
            ]
            for command, cwd in commands:
                exit_code = run_command(command, cwd=cwd or repo_root(), dry_run=False)
                if exit_code:
                    return exit_code

    print("Prepared Home Assistant Git repository:")
    print(f"  Bare repo:       {bare_repo}")
    print(f"  HTTP root:       {output_dir}")
    print("Serve it from this workstation with:")
    print(format_runbook_command(["python3", "scripts/pesetech_hardware_session.py", "addon-serve-git-repo", "--output-dir", str(output_dir), "--repo-name", repo_name, "--port", str(args.port), "--bind", args.bind]))
    print_addon_git_repo_urls(args, repo_name)
    return 0


def addon_serve_git_repo(args):
    output_dir, repo_name, bare_repo = addon_git_repo_paths(args)
    if args.prepare and (args.replace or not bare_repo.exists()):
        prepare_args = argparse.Namespace(
            repository_dir=args.repository_dir,
            output_dir=args.output_dir,
            repo_name=args.repo_name,
            replace=args.replace,
            port=args.port,
            bind=args.bind,
            ha_host=args.ha_host,
            dry_run=args.dry_run,
        )
        prepare_exit = addon_prepare_git_repo(prepare_args)
        if prepare_exit:
            return prepare_exit
    elif not args.dry_run:
        missing = addon_git_repo_missing_files(output_dir, repo_name)
        if missing:
            print(
                f"Prepared Git repository is missing {', '.join(missing)} under {bare_repo}. "
                "Run addon-prepare-git-repo --replace or rerun addon-serve-git-repo with --replace.",
                file=sys.stderr,
            )
            return 1

    print("Pesetech Home Assistant add-on repository server")
    print(f"  HTTP root:       {output_dir}")
    print(f"  Bare repo:       {bare_repo}")
    print(f"  Bind:            {args.bind}:{args.port}")
    print_addon_git_repo_urls(args, repo_name)
    print("Keep this command running while Home Assistant adds or refreshes the repository.")
    if args.dry_run:
        print("Dry run only; not starting HTTP server.")
        return 0

    try:
        server = create_addon_git_repo_server(args, output_dir)
    except OSError as exc:
        print(f"Could not listen on {args.bind}:{args.port}: {exc}", file=sys.stderr)
        return 1

    try:
        print("Serving. Press Ctrl-C after Home Assistant has installed or refreshed the add-on repository.")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping repository server.")
    finally:
        server.server_close()
    return 0


def default_addon_repository_url(args):
    repo_name = addon_git_repo_name(args)
    urls = addon_git_repository_urls(args, repo_name)
    return urls[0]


def addon_ha_api_install_local_repo(args):
    output_dir, repo_name, bare_repo = addon_git_repo_paths(args)
    if getattr(args, "prepare", True):
        prepare_args = argparse.Namespace(
            repository_dir=args.repository_dir,
            output_dir=args.output_dir,
            repo_name=args.repo_name,
            replace=args.replace,
            port=args.port,
            bind=args.bind,
            ha_host=args.ha_host,
            dry_run=args.dry_run,
        )
        prepare_exit = addon_prepare_git_repo(prepare_args)
        if prepare_exit:
            return prepare_exit
    elif not getattr(args, "dry_run", False):
        missing = addon_git_repo_missing_files(output_dir, repo_name)
        if missing:
            print(
                f"Prepared Git repository is missing {', '.join(missing)} under {bare_repo}. "
                "Run addon-prepare-git-repo --replace or rerun this command with --replace.",
                file=sys.stderr,
            )
            return 1

    repository_url = getattr(args, "repository_url", "") or default_addon_repository_url(args)
    install_args = argparse.Namespace(**vars(args))
    install_args.repository_url = repository_url

    print("Home Assistant API local-repository install")
    print(f"  Repository URL: {repository_url}")
    print(f"  HTTP root:      {output_dir}")
    print(f"  Bind:           {args.bind}:{args.port}")

    if getattr(args, "dry_run", False):
        print("Dry run only; not starting the temporary repository server.")
        return addon_ha_api_install(install_args)

    try:
        server = create_addon_git_repo_server(args, output_dir)
    except OSError as exc:
        print(f"Could not listen on {args.bind}:{args.port}: {exc}", file=sys.stderr)
        return 1

    thread = threading.Thread(target=server.serve_forever, name="pesetech-addon-git-server", daemon=True)
    thread.start()
    print("Serving temporary repository while Home Assistant installs or refreshes it.")
    try:
        return addon_ha_api_install(install_args)
    finally:
        print("Stopping temporary repository server.")
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)


def addon_ha_api_install(args):
    repository_url = getattr(args, "repository_url", "") or default_addon_repository_url(args)
    store_slug = getattr(args, "store_slug", "") or getattr(args, "slug", DEFAULT_ADDON_SLUG)
    installed_slug = getattr(args, "installed_slug", "")

    if not getattr(args, "skip_repository", False):
        try:
            hassio_api_request(args, "POST", "/store/repositories", {"repository": repository_url})
        except HassioApiError as exc:
            if not getattr(args, "repository_exists_ok", True):
                print(str(exc), file=sys.stderr)
                return 1
            print(f"Repository add returned {exc.status}; continuing so the store can be queried: {exc.detail}", file=sys.stderr)

    if not getattr(args, "dry_run", False):
        try:
            store_payload = hassio_api_request(args, "GET", "/store/addons")
        except HassioApiError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        discovered_slug = find_hassio_addon_slug(store_payload, args.slug, args.name)
        if discovered_slug:
            store_slug = discovered_slug
        elif not getattr(args, "store_slug", ""):
            print(
                f"Could not find {args.name!r} in the Supervisor store response after adding {repository_url}. "
                "Pass --store-slug if Home Assistant reports a different add-on slug.",
                file=sys.stderr,
            )
            return 1
    else:
        print(f"Dry run store slug: {store_slug}")

    if not installed_slug:
        installed_slug = store_slug

    if not getattr(args, "skip_install", False):
        try:
            hassio_api_request(args, "POST", f"/store/addons/{store_slug}/install")
        except HassioApiError as exc:
            if not getattr(args, "install_exists_ok", True):
                print(str(exc), file=sys.stderr)
                return 1
            print(f"Install returned {exc.status}; continuing so options can be set: {exc.detail}", file=sys.stderr)

    if not getattr(args, "skip_options", False):
        try:
            options = parse_addon_options(getattr(args, "option", []))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        try:
            set_full_addon_options(args, installed_slug, args.operation, options)
        except HassioApiError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    if getattr(args, "start", False):
        try:
            hassio_api_request(args, "POST", f"/addons/{installed_slug}/start")
        except HassioApiError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        log_text = None
        if getattr(args, "require_log_gate", False):
            try:
                gate_exit, log_text = wait_for_addon_log_gate(args, installed_slug, args.operation)
            except HassioApiError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            if gate_exit:
                if getattr(args, "logs", False) and log_text:
                    hassio_print_addon_logs(args, installed_slug, raw=log_text)
                return gate_exit
        if getattr(args, "logs", False):
            delay = getattr(args, "logs_delay", 0.0) or 0.0
            if delay > 0 and not getattr(args, "dry_run", False) and log_text is None:
                time.sleep(delay)
            try:
                hassio_print_addon_logs(args, installed_slug, raw=log_text)
            except HassioApiError as exc:
                print(str(exc), file=sys.stderr)
                return 1
    elif getattr(args, "require_log_gate", False):
        print("--require-log-gate needs --start so there is an add-on run to verify.", file=sys.stderr)
        return 2

    print("Home Assistant API install flow completed.")
    print(f"  Repository URL: {repository_url}")
    print(f"  Store slug:      {store_slug}")
    print(f"  Add-on slug:     {installed_slug}")
    if getattr(args, "start", False):
        print(f"  Started with operation={args.operation}.")
    else:
        print(f"  Options set to operation={args.operation}; start the add-on when ready.")
    return 0


def addon_ha_api_operation(args):
    try:
        addon_slug = resolve_installed_addon_slug(args)
        options = {"operation": args.operation}
        options.update(addon_api_option_overrides(args))
    except (HassioApiError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1 if isinstance(exc, HassioApiError) else 2

    if not getattr(args, "skip_options", False):
        try:
            set_full_addon_options(args, addon_slug, args.operation, options)
        except HassioApiError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    try:
        hassio_addon_action(args, addon_slug, getattr(args, "run", "none"))
    except HassioApiError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    log_text = None
    if getattr(args, "require_log_gate", False):
        try:
            gate_exit, log_text = wait_for_addon_log_gate(args, addon_slug, args.operation)
        except HassioApiError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if gate_exit:
            if getattr(args, "logs", False) and log_text:
                hassio_print_addon_logs(args, addon_slug, raw=log_text)
            return gate_exit

    if getattr(args, "logs", False):
        delay = getattr(args, "logs_delay", 0.0) or 0.0
        if delay > 0 and not getattr(args, "dry_run", False) and log_text is None:
            time.sleep(delay)
        try:
            hassio_print_addon_logs(args, addon_slug, raw=log_text)
        except HassioApiError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    print("Home Assistant API operation flow completed.")
    print(f"  Add-on slug: {addon_slug}")
    print(f"  Operation:   {args.operation}")
    if getattr(args, "run", "none") != "none":
        print(f"  Run action:  {args.run}")
    return 0


def addon_api_sequence_operations(args):
    through = getattr(args, "through", "readiness-test")
    operations = list(ADDON_API_SEQUENCE[: ADDON_API_SEQUENCE.index(through) + 1])
    if getattr(args, "skip_cloud_fetch", False):
        operations = [operation for operation in operations if operation != "cloud-fetch"]
    return operations


def addon_api_sequence_child_args(args, addon_slug, operation):
    values = vars(args).copy()
    values["operation"] = operation
    values["installed_slug"] = addon_slug
    values["no_discover_slug"] = True
    values["run"] = "start" if operation == "runtime-check" else "restart"
    values["logs"] = getattr(args, "logs", True)
    values["skip_options"] = False
    values["require_log_gate"] = getattr(args, "verify_log_gates", True)

    if operation != "cloud-fetch":
        values["cloud_token"] = ""
        values["cloud_token_file"] = ""
        values["cloud_username"] = ""
        values["cloud_username_file"] = ""
        values["cloud_password"] = ""
        values["cloud_password_file"] = ""
        values["cloud_region"] = None
        values["cloud_home_id"] = None
        values["cloud_candidate"] = None

    if operation not in ADDON_IMPORT_OPTION_OPERATIONS:
        values["import_mesh_candidate"] = None
        values["import_node_uuid"] = None
        values["import_node_unicast"] = None
        values["import_local_address"] = None
        values["import_force"] = False

    if operation not in ADDON_HA_TARGET_OPERATIONS:
        values["addon_ha_url"] = None
        values["ha_entity_id"] = None

    return argparse.Namespace(**values)


def addon_ha_api_sequence(args):
    operations = addon_api_sequence_operations(args)
    movement_steps = [operation for operation in operations if operation in ADDON_MOVEMENT_OPERATIONS]
    if movement_steps and not getattr(args, "allow_movement", False):
        print(
            "Refusing to include movement operations without --allow-movement: "
            + ", ".join(movement_steps),
            file=sys.stderr,
        )
        return 2

    try:
        addon_slug = resolve_installed_addon_slug(args)
    except HassioApiError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("Home Assistant API add-on gate sequence")
    print(f"  Add-on slug: {addon_slug}")
    print(f"  Through:     {args.through}")
    if movement_steps:
        print("  Movement:    enabled for " + ", ".join(movement_steps))
    else:
        print("  Movement:    disabled; no real-light movement operations are included")
    if getattr(args, "verify_log_gates", True):
        print("  Log gates:   required for operations with pass markers")
    else:
        print("  Log gates:   disabled")

    for operation in operations:
        print(f"\n== {operation} ==")
        child_args = addon_api_sequence_child_args(args, addon_slug, operation)
        exit_code = addon_ha_api_operation(child_args)
        if exit_code:
            print(f"Sequence stopped at operation={operation} with exit code {exit_code}.", file=sys.stderr)
            return exit_code

    print("\nHome Assistant API gate sequence completed.")
    if not movement_steps:
        print("No movement operations were run. Continue to move-test only after reviewing the no-motion gate logs.")
    return 0


def addon_install(args):
    archive = Path(args.addon_archive).expanduser()
    if not args.dry_run and not archive.exists():
        print(f"Add-on archive not found: {archive}", file=sys.stderr)
        return 1

    archive_arg = str(archive if archive.is_absolute() else archive)
    if not getattr(args, "skip_verify", False):
        verify_exit = run_command(
            ["python3", "scripts/pesetech_verify_addon_package.py", "--local-app", archive_arg],
            dry_run=args.dry_run,
        )
        if verify_exit:
            return verify_exit

    ssh_target = addon_ssh_target(args)
    remote_addons_dir = args.remote_addons_dir.rstrip("/") or DEFAULT_REMOTE_ADDONS_DIR
    remote_archive = f"{remote_addons_dir}/{archive.name}"
    remote_addon_dir = f"{remote_addons_dir}/{args.slug}"

    commands = [
        addon_ssh_command(args, f"mkdir -p {shlex.quote(remote_addons_dir)}"),
        addon_scp_command(args, archive_arg, f"{ssh_target}:{remote_archive}"),
    ]
    if args.replace:
        commands.append(addon_ssh_command(args, f"rm -rf {shlex.quote(remote_addon_dir)}"))
    commands.extend(
        [
            addon_ssh_command(args, f"tar -xzf {shlex.quote(remote_archive)} -C {shlex.quote(remote_addons_dir)}"),
            addon_ssh_command(
                args,
                " && ".join(
                    [
                        f"test -f {shlex.quote(remote_addon_dir + '/config.yaml')}",
                        f"test -f {shlex.quote(remote_addon_dir + '/run.sh')}",
                        f"test -d {shlex.quote(remote_addon_dir + '/source')}",
                    ]
                ),
            ),
        ]
    )

    for command in commands:
        exit_code = run_command(command, dry_run=args.dry_run)
        if exit_code:
            return exit_code

    print("Local add-on files are installed on the Home Assistant host.")
    print("Next in Home Assistant: Settings -> Add-ons -> Add-on Store -> Check for updates -> Local add-ons -> Pesetech BLE Mesh Gateway.")
    print("Start with operation=runtime-check, then operation=mesh-daemon-check.")
    return 0


def addon_upload_cloud_credentials(args):
    ssh_target = addon_ssh_target(args)
    remote_share_dir = args.remote_share_dir.rstrip("/") or DEFAULT_REMOTE_SHARE_DIR
    uploads = []

    for label, local_path, remote_name in (
        ("token", args.token_file, args.remote_token_name),
        ("username", args.username_file, args.remote_username_name),
        ("password", args.password_file, args.remote_password_name),
    ):
        if not local_path:
            continue
        source = Path(local_path).expanduser()
        if not args.dry_run and not source.exists():
            print(f"Cloud {label} file not found: {source}", file=sys.stderr)
            return 1
        uploads.append((label, str(source if source.is_absolute() else source), f"{remote_share_dir}/{remote_name}"))

    labels = {label for label, source, remote in uploads}
    has_token = "token" in labels
    has_credentials = {"username", "password"}.issubset(labels)
    if not has_token and not has_credentials:
        print("Provide --token-file, or provide both --username-file and --password-file.", file=sys.stderr)
        return 2
    if ("username" in labels) != ("password" in labels):
        print("--username-file and --password-file must be provided together.", file=sys.stderr)
        return 2

    commands = [addon_ssh_command(args, f"mkdir -p {shlex.quote(remote_share_dir)}")]
    for label, source, remote in uploads:
        commands.append(addon_scp_command(args, source, f"{ssh_target}:{remote}"))
    commands.append(
        addon_ssh_command(
            args,
            " && ".join(f"test -s {shlex.quote(remote)}" for label, source, remote in uploads),
        )
    )

    for command in commands:
        exit_code = run_command(command, dry_run=args.dry_run)
        if exit_code:
            return exit_code

    if has_token:
        print(f"Cloud token file installed at {remote_share_dir}/{args.remote_token_name}.")
    if has_credentials:
        print(f"Cloud username/password files installed at {remote_share_dir}/{args.remote_username_name} and {remote_share_dir}/{args.remote_password_name}.")
    print("Next in the add-on: set operation=cloud-fetch, confirm cloud_region/cloud_home_id if needed, then start the add-on.")
    return 0


def addon_upload_mesh(args):
    source = Path(args.mesh_json).expanduser()
    if not args.dry_run and not source.exists():
        print(f"Mesh JSON file not found: {source}", file=sys.stderr)
        return 1

    source_arg = str(source if source.is_absolute() else source)
    if not getattr(args, "skip_validate", False):
        validate_exit = run_command(
            ["python3", "scripts/pesetech_extract_mesh_json.py", "--list", source_arg],
            dry_run=args.dry_run,
        )
        if validate_exit:
            return validate_exit

    ssh_target = addon_ssh_target(args)
    remote_share_dir = args.remote_share_dir.rstrip("/") or DEFAULT_REMOTE_SHARE_DIR
    remote_mesh = f"{remote_share_dir}/{args.remote_name}"
    commands = [
        addon_ssh_command(args, f"mkdir -p {shlex.quote(remote_share_dir)}"),
        addon_scp_command(args, source_arg, f"{ssh_target}:{remote_mesh}"),
        addon_ssh_command(args, f"test -s {shlex.quote(remote_mesh)}"),
    ]

    for command in commands:
        exit_code = run_command(command, dry_run=args.dry_run)
        if exit_code:
            return exit_code

    print(f"Mesh JSON installed at {remote_mesh}.")
    print("Next in the add-on: set operation=import-check, confirm mesh_json_path/import_mesh_candidate if needed, then start the add-on.")
    print("If import-check passes, run status, then set operation=import.")
    return 0


def addon_operation_override(args):
    override = {"operation": args.operation}
    option_fields = (
        "discovery_prefix",
        "node_id",
        "device_id",
        "skylight_name",
        "skylight_uuid",
        "mesh_json_path",
        "import_mesh_candidate",
        "import_node_uuid",
        "import_node_unicast",
        "import_local_address",
        "cloud_region",
        "cloud_base_url",
        "cloud_output_path",
        "cloud_raw_output_path",
        "cloud_report_path",
        "cloud_candidate",
        "cloud_home_id",
        "ha_url",
        "ha_entity_id",
    )
    for field in option_fields:
        value = getattr(args, field, None)
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        override[field] = value

    for field in ("import_force", "relay"):
        if bool(getattr(args, field, False)):
            override[field] = True

    return override


def addon_set_operation(args):
    ssh_target = addon_ssh_target(args)
    remote_share_dir = args.remote_share_dir.rstrip("/") or DEFAULT_REMOTE_SHARE_DIR
    remote_override = f"{remote_share_dir}/{args.remote_name}"
    override = addon_operation_override(args)
    action = getattr(args, "run", "none")

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as override_file:
            json.dump(override, override_file, indent=2, sort_keys=True)
            override_file.write("\n")
            temp_path = override_file.name

        print("Writing non-secret add-on operation override:")
        print(json.dumps(override, indent=2, sort_keys=True))
        commands = [
            addon_ssh_command(args, f"mkdir -p {shlex.quote(remote_share_dir)}"),
            addon_scp_command(args, temp_path, f"{ssh_target}:{remote_override}"),
            addon_ssh_command(args, f"test -s {shlex.quote(remote_override)}"),
        ]
        if action in {"start", "restart"}:
            commands.append(addon_ssh_command(args, addon_cli_action_shell(action, args.slug)))

        for command in commands:
            exit_code = run_command(command, dry_run=args.dry_run)
            if exit_code:
                return exit_code
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink()
            except FileNotFoundError:
                pass

    print(f"Operation override installed at {remote_override}.")
    if action == "none":
        print(f"Next in Home Assistant: start or restart the add-on; it will run operation={args.operation}.")
        print(f"Or run over SSH: ha apps start {args.slug} / ha addons start {args.slug}")
    else:
        print(f"Requested Home Assistant to {action} add-on {args.slug}.")
    return 0


def addon_fetch_cloud_report(args):
    ssh_target = addon_ssh_target(args)
    output_dir = Path(args.output_dir).expanduser()
    if args.dry_run:
        output_dir_display = output_dir
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_dir_display = output_dir.resolve()

    remote_report = args.remote_report
    local_report = output_dir_display / Path(remote_report).name
    test_code = run_command(addon_ssh_command(args, f"test -s {shlex.quote(remote_report)}"), dry_run=args.dry_run)
    if test_code:
        print("Cloud fetch report was not found on Home Assistant. Run add-on operation=cloud-fetch first.", file=sys.stderr)
        return test_code

    copy_code = run_command(addon_scp_command(args, f"{ssh_target}:{remote_report}", str(local_report)), dry_run=args.dry_run)
    if copy_code:
        return copy_code

    if args.no_summary:
        print(f"Cloud fetch report copied to {local_report}")
        return 0

    return run_command(["python3", "scripts/pesetech_cloud_report_summary.py", str(local_report)], dry_run=args.dry_run)


def addon_fetch_status_report(args):
    ssh_target = addon_ssh_target(args)
    output_dir = Path(args.output_dir).expanduser()
    if args.dry_run:
        output_dir_display = output_dir
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_dir_display = output_dir.resolve()

    remote_report = args.remote_report
    local_report = output_dir_display / Path(remote_report).name
    test_code = run_command(addon_ssh_command(args, f"test -s {shlex.quote(remote_report)}"), dry_run=args.dry_run)
    if test_code:
        print("Status report was not found on Home Assistant. Run add-on operation=status first.", file=sys.stderr)
        return test_code

    copy_code = run_command(addon_scp_command(args, f"{ssh_target}:{remote_report}", str(local_report)), dry_run=args.dry_run)
    if copy_code:
        return copy_code

    if args.no_summary:
        print(f"Status report copied to {local_report}")
        return 0

    return run_command(["python3", "scripts/pesetech_status_report_summary.py", str(local_report)], dry_run=args.dry_run)


def addon_fetch_report(args):
    ssh_target = addon_ssh_target(args)
    output_dir = Path(args.output_dir).expanduser()
    if args.dry_run:
        output_dir_display = output_dir
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_dir_display = output_dir.resolve()

    remote_report = args.remote_report or ADDON_SHARED_REPORTS[args.report]
    local_report = output_dir_display / Path(remote_report).name
    test_code = run_command(addon_ssh_command(args, f"test -s {shlex.quote(remote_report)}"), dry_run=args.dry_run)
    if test_code:
        print(f"Report {remote_report} was not found on Home Assistant. Run add-on operation={args.report.replace('-audit', '')} first.", file=sys.stderr)
        return test_code

    copy_code = run_command(addon_scp_command(args, f"{ssh_target}:{remote_report}", str(local_report)), dry_run=args.dry_run)
    if copy_code:
        return copy_code

    print(f"Add-on report copied to {local_report}")
    return 0


def addon_fetch_diagnostics(args):
    ssh_target = addon_ssh_target(args)
    output_dir = Path(args.output_dir).expanduser()
    if args.dry_run:
        output_dir_display = output_dir
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_dir_display = output_dir.resolve()

    remote_find = f"ls -t {args.remote_glob} 2>/dev/null | head -n 1"
    exit_code, stdout = run_capture_command(
        addon_ssh_command(args, remote_find),
        dry_run=args.dry_run,
        placeholder=args.remote_glob.replace("*", "latest"),
    )
    if exit_code:
        print("Could not list Home Assistant diagnostics bundles. Run add-on operation=diagnostics first.", file=sys.stderr)
        return exit_code

    remote_bundle = stdout.strip().splitlines()[0] if stdout.strip() else ""
    if not remote_bundle:
        print("No diagnostics bundle found on Home Assistant. Run add-on operation=diagnostics first.", file=sys.stderr)
        return 1

    local_bundle = output_dir_display / Path(remote_bundle).name
    copy_code = run_command(addon_scp_command(args, f"{ssh_target}:{remote_bundle}", str(local_bundle)), dry_run=args.dry_run)
    if copy_code:
        return copy_code

    if args.no_review:
        print(f"Diagnostics copied to {local_bundle}")
        return 0

    return run_command(["python3", "scripts/pesetech_review_diagnostics.py", str(local_bundle)], dry_run=args.dry_run)


def add_common(parser, config_default=DEFAULT_CONFIG, config_help="Gateway config path."):
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running them.")
    parser.add_argument("--config", default=config_default, help=config_help)
    parser.add_argument("--compose-dir", default="docker", help="Directory containing docker-compose.yaml.")


def add_addon_ssh_args(parser):
    parser.add_argument(
        "--ssh-connect-timeout",
        type=int,
        default=DEFAULT_SSH_CONNECT_TIMEOUT,
        help="SSH/SCP connection timeout in seconds for Home Assistant helper commands; use 0 to omit the option.",
    )
    parser.add_argument(
        "--ssh-batch-mode",
        action="store_true",
        help="Pass BatchMode=yes to SSH/SCP so missing keys fail quickly instead of prompting.",
    )


def main():
    parser = argparse.ArgumentParser(description="Host-side helper for the Pesetech real-device hardware session.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    copy_parser = subparsers.add_parser("copy-config", help="Copy the Pesetech sample config to config.yaml.")
    add_common(copy_parser)
    copy_parser.add_argument("--sample", default="docker/config/pesetech-skylight.yaml.sample")
    copy_parser.add_argument("--force", action="store_true", help="Overwrite an existing config.")
    copy_parser.set_defaults(handler=copy_config)

    preflight_parser = subparsers.add_parser("preflight", help="Run config and optional host checks.")
    add_common(preflight_parser)
    preflight_parser.add_argument("--store", default="docker/config/store.yaml")
    preflight_parser.add_argument("--host", action="store_true", default=True)
    preflight_parser.add_argument("--skip-mqtt-connect-check", action="store_true")
    preflight_parser.add_argument("--mqtt-connect-timeout", type=float, default=3.0)
    preflight_parser.set_defaults(handler=preflight)

    build_parser = subparsers.add_parser("build", help="Build the Docker image.")
    add_common(build_parser)
    build_parser.set_defaults(handler=build)

    shell_parser = subparsers.add_parser("shell", help="Start D-Bus/bluetooth-meshd in shell mode.")
    add_common(shell_parser)
    shell_parser.set_defaults(handler=shell)

    scan_parser = subparsers.add_parser("scan", help="Scan for unprovisioned mesh devices.")
    add_common(scan_parser)
    scan_parser.set_defaults(handler=scan)

    uuid_parser = subparsers.add_parser("set-uuid", help="Write the scanned skylight UUID into config.yaml.")
    add_common(uuid_parser)
    uuid_parser.add_argument("--device-id", default="skylight")
    uuid_parser.add_argument("--uuid", required=True)
    uuid_parser.set_defaults(handler=set_uuid)

    import_parser = subparsers.add_parser("import-mesh", help="Import an existing Telink/Pesetech mesh.json.")
    add_common(import_parser)
    import_parser.add_argument("mesh_json")
    import_parser.add_argument("--store", default="docker/config/store.yaml")
    import_parser.add_argument("--device-id", default="skylight")
    import_parser.add_argument("--device-name", default="Pesetech Skylight")
    import_parser.add_argument("--default-entity-id")
    import_parser.add_argument("--mesh-candidate", type=int, default=0)
    import_parser.add_argument("--node-uuid")
    import_parser.add_argument("--node-unicast")
    import_parser.add_argument("--local-address")
    import_parser.add_argument("--force", action="store_true")
    import_parser.set_defaults(handler=import_mesh)

    extract_parser = subparsers.add_parser("extract-mesh", help="Extract mesh.json from a HAR, log, raw JSON, or directory.")
    add_common(extract_parser)
    extract_parser.add_argument("inputs", nargs="+")
    extract_parser.add_argument("-o", "--output")
    extract_parser.add_argument("--candidate", type=int)
    extract_parser.add_argument("--list", action="store_true")
    extract_parser.add_argument("--no-recursive", action="store_true")
    extract_parser.add_argument("--max-bytes", type=int)
    extract_parser.set_defaults(handler=extract_mesh)

    cloud_parser = subparsers.add_parser("fetch-cloud-mesh", help="Fetch cloud mesh JSON with a captured Pesetech bearer token.")
    add_common(cloud_parser)
    cloud_parser.add_argument("-o", "--output")
    cloud_parser.add_argument("--candidate", type=int)
    cloud_parser.add_argument("--list", action="store_true")
    cloud_parser.add_argument("--raw-output")
    cloud_parser.add_argument("--report-output")
    cloud_parser.add_argument("--region", choices=["asia", "europe"], default="europe")
    cloud_parser.add_argument("--base-url", default=None)
    cloud_parser.add_argument("--endpoint", action="append", choices=["home-list", "mesh-json-by-home-id", "sync-data"])
    cloud_parser.add_argument("--home-id", action="append")
    cloud_parser.add_argument("--token-file")
    cloud_parser.add_argument("--token-env")
    cloud_parser.add_argument("--username-file")
    cloud_parser.add_argument("--username-env")
    cloud_parser.add_argument("--password-file")
    cloud_parser.add_argument("--password-env")
    cloud_parser.add_argument("--user-origin", type=int)
    cloud_parser.add_argument("--timeout", type=float)
    cloud_parser.add_argument("--user-agent")
    cloud_parser.add_argument("--accept-language")
    cloud_parser.set_defaults(handler=fetch_cloud_mesh)

    provision_parser = subparsers.add_parser("provision", help="Provision, configure, and list the skylight.")
    add_common(provision_parser)
    provision_parser.add_argument("--device-id", default="skylight")
    provision_parser.add_argument("--uuid", required=True)
    provision_parser.add_argument("--update-config", action="store_true")
    provision_parser.set_defaults(handler=provision)

    service_parser = subparsers.add_parser("service", help="Run the MQTT gateway service.")
    add_common(service_parser)
    service_parser.set_defaults(handler=service)

    logs_parser = subparsers.add_parser("logs", help="Show Docker logs for the gateway.")
    add_common(logs_parser)
    logs_parser.add_argument("--follow", action="store_true")
    logs_parser.set_defaults(handler=logs)

    runtime_parser = subparsers.add_parser("runtime-check", help="Verify installed Bluetooth Mesh runtime APIs.")
    add_common(runtime_parser)
    runtime_parser.set_defaults(handler=runtime_check)

    discovery_parser = subparsers.add_parser("discovery", help="Verify the retained Home Assistant MQTT discovery payload.")
    add_common(discovery_parser)
    discovery_parser.add_argument("--broker")
    discovery_parser.add_argument("--port", type=int)
    discovery_parser.add_argument("--username")
    discovery_parser.add_argument("--password")
    discovery_parser.add_argument("--discovery-prefix")
    discovery_parser.add_argument("--mesh-topic")
    discovery_parser.add_argument("--device-id")
    discovery_parser.add_argument("--discovery-timeout", type=float, default=30.0)
    discovery_parser.add_argument("--candidate-timeout", type=float, default=2.0)
    discovery_parser.add_argument("--dump-json", action="store_true")
    discovery_parser.set_defaults(handler=discovery)

    smoke_parser = subparsers.add_parser("smoke", help="Run the MQTT smoke test and create the proof log.")
    add_common(smoke_parser)
    smoke_parser.add_argument("--broker")
    smoke_parser.add_argument("--port", type=int)
    smoke_parser.add_argument("--username")
    smoke_parser.add_argument("--password")
    smoke_parser.add_argument("--discovery-prefix")
    smoke_parser.add_argument("--mesh-topic")
    smoke_parser.add_argument("--device-id")
    smoke_parser.add_argument("--proof-log", default="docker/config/pesetech-proof.jsonl")
    smoke_parser.add_argument("--proof-run-id")
    smoke_parser.add_argument("--precondition-visible-start", action="store_true")
    smoke_parser.set_defaults(handler=smoke)

    verify_parser = subparsers.add_parser("verify", help="Verify the proof log.")
    add_common(verify_parser)
    verify_parser.add_argument("--discovery-prefix")
    verify_parser.add_argument("--mesh-topic")
    verify_parser.add_argument("--device-id")
    verify_parser.add_argument("--proof-log", default="docker/config/pesetech-proof.jsonl")
    verify_parser.add_argument("--proof-run-id")
    verify_parser.set_defaults(handler=verify)

    ha_service_parser = subparsers.add_parser("ha-service", help="Run the Home Assistant light service proof.")
    add_common(ha_service_parser)
    ha_service_parser.add_argument("--url", dest="ha_url", default="http://homeassistant.local:8123")
    ha_service_parser.add_argument("--entity-id", dest="ha_entity_id", default="light.skylight")
    ha_service_parser.add_argument("--token-file", dest="ha_token_file")
    ha_service_parser.add_argument("--proof-log", dest="ha_proof_log", default="docker/config/pesetech-ha-service-proof.jsonl")
    ha_service_parser.add_argument("--proof-run-id")
    ha_service_parser.add_argument("--precondition-visible-start", dest="ha_precondition_visible_start", action="store_true")
    ha_service_parser.add_argument("--wait-attributes", dest="ha_wait_attributes", action="store_true")
    ha_service_parser.add_argument("--no-wait-state", dest="ha_no_wait_state", action="store_true")
    ha_service_parser.add_argument("--no-observe", dest="ha_no_observe", action="store_true")
    ha_service_parser.add_argument("--wait-mqtt-state", dest="ha_wait_mqtt_state", action="store_true")
    ha_service_parser.add_argument("--wait-mqtt-attributes", dest="ha_wait_mqtt_attributes", action="store_true")
    ha_service_parser.add_argument("--mqtt-broker", dest="ha_mqtt_broker")
    ha_service_parser.add_argument("--mqtt-port", dest="ha_mqtt_port", type=int)
    ha_service_parser.add_argument("--mqtt-username", dest="ha_mqtt_username")
    ha_service_parser.add_argument("--mqtt-password", dest="ha_mqtt_password")
    ha_service_parser.add_argument("--mqtt-discovery-prefix", dest="ha_mqtt_discovery_prefix")
    ha_service_parser.add_argument("--mqtt-mesh-topic", dest="ha_mqtt_mesh_topic")
    ha_service_parser.add_argument("--mqtt-device-id", dest="ha_mqtt_device_id")
    ha_service_parser.add_argument("--mqtt-brightness-scale", dest="ha_mqtt_brightness_scale", type=int)
    ha_service_parser.add_argument("--mqtt-brightness-tolerance", dest="ha_mqtt_brightness_tolerance", type=int)
    ha_service_parser.add_argument("--mqtt-mired-tolerance", dest="ha_mqtt_mired_tolerance", type=int)
    ha_service_parser.add_argument("--list-candidates", dest="ha_list_candidates", action="store_true")
    ha_service_parser.add_argument("--candidate-search", dest="ha_candidate_search", default="skylight")
    ha_service_parser.set_defaults(handler=ha_service)

    ha_verify_parser = subparsers.add_parser("ha-verify", help="Verify the Home Assistant light service proof log.")
    add_common(ha_verify_parser)
    ha_verify_parser.add_argument("--url", dest="ha_url", default="http://homeassistant.local:8123")
    ha_verify_parser.add_argument("--entity-id", dest="ha_entity_id", default="light.skylight")
    ha_verify_parser.add_argument("--proof-log", dest="ha_proof_log", default="docker/config/pesetech-ha-service-proof.jsonl")
    ha_verify_parser.add_argument("--proof-run-id")
    ha_verify_parser.add_argument("--require-attributes", dest="ha_wait_attributes", action="store_true")
    ha_verify_parser.add_argument("--require-mqtt-state", dest="ha_require_mqtt_state", action="store_true")
    ha_verify_parser.add_argument("--require-mqtt-attributes", dest="ha_require_mqtt_attributes", action="store_true")
    ha_verify_parser.add_argument("--mqtt-brightness-scale", dest="ha_mqtt_brightness_scale", type=int)
    ha_verify_parser.add_argument("--mqtt-brightness-tolerance", dest="ha_mqtt_brightness_tolerance", type=int)
    ha_verify_parser.add_argument("--mqtt-mired-tolerance", dest="ha_mqtt_mired_tolerance", type=int)
    ha_verify_parser.add_argument("--allow-missing-state", dest="ha_allow_missing_state", action="store_true")
    ha_verify_parser.add_argument("--allow-service-error", dest="ha_allow_service_error", action="store_true")
    ha_verify_parser.add_argument("--allow-unobserved", dest="ha_allow_unobserved", action="store_true")
    ha_verify_parser.set_defaults(handler=ha_verify)

    audit_parser = subparsers.add_parser("final-audit", help="Audit MQTT and Home Assistant proof logs against the full real-device objective.")
    add_common(audit_parser)
    audit_parser.add_argument("--proof-log", default="docker/config/pesetech-proof.jsonl")
    audit_parser.add_argument("--ha-proof-log", default="docker/config/pesetech-ha-service-proof.jsonl")
    audit_parser.add_argument("--ha-url", default="http://homeassistant.local:8123")
    audit_parser.add_argument("--ha-entity-id", default="light.skylight")
    audit_parser.add_argument("--discovery-prefix")
    audit_parser.add_argument("--mesh-topic")
    audit_parser.add_argument("--device-id")
    audit_parser.add_argument("--proof-run-id")
    audit_parser.add_argument("--ha-mqtt-brightness-scale", type=int)
    audit_parser.add_argument("--ha-mqtt-brightness-tolerance", type=int)
    audit_parser.add_argument("--ha-mqtt-mired-tolerance", type=int)
    audit_parser.add_argument("--allow-unobserved", action="store_true")
    audit_parser.add_argument("--allow-different-run-ids", action="store_true")
    audit_parser.add_argument("--final-audit-report", default="docker/config/pesetech-final-audit.json")
    audit_parser.set_defaults(handler=final_audit)

    diagnostics_parser = subparsers.add_parser("diagnostics", help="Collect redacted diagnostics.")
    add_common(diagnostics_parser)
    diagnostics_parser.add_argument("--broker")
    diagnostics_parser.add_argument("--port", type=int)
    diagnostics_parser.add_argument("--username")
    diagnostics_parser.add_argument("--password")
    diagnostics_parser.add_argument("--discovery-prefix")
    diagnostics_parser.add_argument("--mesh-topic")
    diagnostics_parser.add_argument("--device-id")
    diagnostics_parser.add_argument("--candidate-timeout", type=float)
    diagnostics_parser.add_argument("--cloud-output")
    diagnostics_parser.add_argument("--cloud-raw-output")
    diagnostics_parser.add_argument("--cloud-report")
    diagnostics_parser.add_argument("--cloud-token-file")
    diagnostics_parser.add_argument("--cloud-username-file")
    diagnostics_parser.add_argument("--cloud-password-file")
    diagnostics_parser.add_argument("--cloud-region")
    diagnostics_parser.add_argument("--cloud-candidate")
    diagnostics_parser.add_argument("--cloud-home-id")
    diagnostics_parser.add_argument("--import-mesh-candidate")
    diagnostics_parser.add_argument("--store", default="docker/config/store.yaml")
    diagnostics_parser.add_argument("--proof-log", default="docker/config/pesetech-proof.jsonl")
    diagnostics_parser.add_argument("--ha-proof-log", default="docker/config/pesetech-ha-service-proof.jsonl")
    diagnostics_parser.add_argument("--final-audit-report", default="docker/config/pesetech-final-audit.json")
    diagnostics_parser.add_argument("--import-check-report", default="docker/config/pesetech-import-check.json")
    diagnostics_parser.add_argument("--readiness-report", default="docker/config/pesetech-readiness.json")
    diagnostics_parser.add_argument("--status-report", default="docker/config/pesetech-status.json")
    diagnostics_parser.add_argument("--ha-url", default="http://homeassistant.local:8123")
    diagnostics_parser.add_argument("--ha-entity-id", default="light.skylight")
    diagnostics_parser.add_argument("--ha-token-file")
    diagnostics_parser.add_argument("--ha-candidate-search", default="skylight")
    diagnostics_parser.add_argument("--ha-require-attributes", action="store_true")
    diagnostics_parser.add_argument("--ha-require-mqtt-state", action="store_true")
    diagnostics_parser.add_argument("--ha-require-mqtt-attributes", action="store_true")
    diagnostics_parser.add_argument("--ha-mqtt-brightness-scale", type=int)
    diagnostics_parser.add_argument("--ha-mqtt-brightness-tolerance", type=int)
    diagnostics_parser.add_argument("--ha-mqtt-mired-tolerance", type=int)
    diagnostics_parser.add_argument("--proof-run-id")
    diagnostics_parser.add_argument("--skip-docker", action="store_true")
    diagnostics_parser.set_defaults(handler=diagnostics)

    prove_parser = subparsers.add_parser("prove", help="Run preflight, discovery, smoke, verify, and diagnostics on failure.")
    add_common(prove_parser)
    prove_parser.add_argument("--broker")
    prove_parser.add_argument("--port", type=int)
    prove_parser.add_argument("--username")
    prove_parser.add_argument("--password")
    prove_parser.add_argument("--discovery-prefix")
    prove_parser.add_argument("--mesh-topic")
    prove_parser.add_argument("--device-id")
    prove_parser.add_argument("--discovery-timeout", type=float, default=30.0)
    prove_parser.add_argument("--candidate-timeout", type=float, default=2.0, help="Seconds to scan nearby retained light discovery configs when the exact discovery topic is missing.")
    prove_parser.add_argument("--skip-mqtt-connect-check", action="store_true")
    prove_parser.add_argument("--mqtt-connect-timeout", type=float, default=3.0)
    prove_parser.add_argument("--proof-log", default="docker/config/pesetech-proof.jsonl")
    prove_parser.add_argument("--store", default="docker/config/store.yaml")
    prove_parser.add_argument("--host", action="store_true", default=True)
    prove_parser.add_argument("--dump-json", action="store_true")
    prove_parser.add_argument(
        "--no-precondition-visible-start",
        dest="precondition_visible_start",
        action="store_false",
        default=True,
        help="Skip the setup pulse that makes the MQTT proof steps easier to observe.",
    )
    prove_parser.add_argument("--start-service", action="store_true", help="Start/recreate Docker service mode after preflight before running proof gates.")
    prove_parser.add_argument("--service-ready-timeout", type=float, default=30.0, help="Seconds to wait for Docker app exec readiness after --start-service.")
    prove_parser.add_argument("--ha-service", action="store_true", help="After MQTT proof, also call Home Assistant light services.")
    prove_parser.add_argument("--final-audit", action="store_true", help="After --ha-service, audit both proof logs against the full real-device objective.")
    prove_parser.add_argument("--ha-url", default="http://homeassistant.local:8123")
    prove_parser.add_argument("--ha-entity-id", default="light.skylight")
    prove_parser.add_argument("--ha-entity-timeout", type=float, default=30.0)
    prove_parser.add_argument("--ha-token-file")
    prove_parser.add_argument("--ha-proof-log", default="docker/config/pesetech-ha-service-proof.jsonl")
    prove_parser.add_argument("--final-audit-report", default="docker/config/pesetech-final-audit.json")
    prove_parser.add_argument("--import-check-report", default="docker/config/pesetech-import-check.json")
    prove_parser.add_argument("--status-report", default="docker/config/pesetech-status.json")
    prove_parser.add_argument(
        "--no-ha-precondition-visible-start",
        dest="ha_precondition_visible_start",
        action="store_false",
        default=True,
        help="Skip the setup pulse that makes the Home Assistant service proof steps easier to observe.",
    )
    prove_parser.add_argument("--ha-wait-attributes", action="store_true")
    prove_parser.add_argument("--ha-wait-mqtt-state", action="store_true")
    prove_parser.add_argument("--ha-wait-mqtt-attributes", action="store_true")
    prove_parser.add_argument("--ha-mqtt-brightness-scale", type=int)
    prove_parser.add_argument("--ha-mqtt-brightness-tolerance", type=int)
    prove_parser.add_argument("--ha-mqtt-mired-tolerance", type=int)
    prove_parser.add_argument("--ha-no-wait-state", action="store_true")
    prove_parser.add_argument("--ha-no-observe", action="store_true")
    prove_parser.add_argument("--ha-candidate-search", default="skylight")
    prove_parser.add_argument(
        "--ha-relaxed-state-proof",
        action="store_true",
        help="Do not auto-require HA brightness/CCT attributes and MQTT bridge attribute fields when --ha-service is used.",
    )
    prove_parser.add_argument("--ha-allow-missing-state", action="store_true")
    prove_parser.add_argument("--ha-allow-service-error", action="store_true")
    prove_parser.add_argument("--ha-allow-unobserved", action="store_true")
    prove_parser.set_defaults(ha_list_candidates=False)
    prove_parser.add_argument("--proof-run-id", default=None, help="Identifier shared by MQTT and Home Assistant proof events for this prove run.")
    prove_parser.add_argument("--keep-proof-logs", action="store_true", help="Append to existing proof logs instead of starting this prove run with fresh logs.")
    prove_parser.add_argument("--no-diagnostics", action="store_true", help="Do not collect diagnostics if a proof step fails.")
    prove_parser.add_argument("--diagnostics-on-success", action="store_true", help="Collect diagnostics after a successful proof run.")
    prove_parser.set_defaults(handler=prove)

    addon_prove_parser = subparsers.add_parser(
        "prove-ha-addon",
        help="Prove an already-running Home Assistant add-on/gateway from this host.",
    )
    add_common(
        addon_prove_parser,
        config_default=HA_ADDON_PROOF_CONFIG,
        config_help=(
            "Gateway config path for MQTT topic defaults. Defaults to a deliberately missing add-on proof path "
            "so a running Home Assistant add-on proof is not affected by stale docker/config/config.yaml."
        ),
    )
    addon_prove_parser.add_argument("--broker")
    addon_prove_parser.add_argument("--port", type=int)
    addon_prove_parser.add_argument("--username")
    addon_prove_parser.add_argument("--password")
    addon_prove_parser.add_argument("--discovery-prefix")
    addon_prove_parser.add_argument("--mesh-topic")
    addon_prove_parser.add_argument("--device-id")
    addon_prove_parser.add_argument("--default-entity-id", help="Expected MQTT discovery default_entity_id; defaults to --ha-entity-id.")
    addon_prove_parser.add_argument("--discovery-timeout", type=float, default=30.0)
    addon_prove_parser.add_argument("--candidate-timeout", type=float, default=2.0, help="Seconds to scan nearby retained light discovery configs when the exact discovery topic is missing.")
    addon_prove_parser.add_argument("--dump-json", action="store_true")
    addon_prove_parser.add_argument("--proof-log", default="docker/config/pesetech-proof.jsonl")
    addon_prove_parser.add_argument("--store", default="docker/config/store.yaml")
    addon_prove_parser.add_argument(
        "--no-precondition-visible-start",
        dest="precondition_visible_start",
        action="store_false",
        default=True,
        help="Skip the setup pulse that makes the direct MQTT proof steps easier to observe.",
    )
    addon_prove_parser.add_argument("--ha-url", default="http://homeassistant.local:8123")
    addon_prove_parser.add_argument("--ha-entity-id", default="light.skylight")
    addon_prove_parser.add_argument("--ha-entity-timeout", type=float, default=30.0)
    addon_prove_parser.add_argument("--ha-token-file")
    addon_prove_parser.add_argument("--ha-proof-log", default="docker/config/pesetech-ha-service-proof.jsonl")
    addon_prove_parser.add_argument("--final-audit-report", default="docker/config/pesetech-final-audit.json")
    addon_prove_parser.add_argument("--import-check-report", default="docker/config/pesetech-import-check.json")
    addon_prove_parser.add_argument("--status-report", default="docker/config/pesetech-status.json")
    addon_prove_parser.add_argument(
        "--no-ha-precondition-visible-start",
        dest="ha_precondition_visible_start",
        action="store_false",
        default=True,
        help="Skip the setup pulse that makes the Home Assistant service proof steps easier to observe.",
    )
    addon_prove_parser.add_argument("--ha-mqtt-broker")
    addon_prove_parser.add_argument("--ha-mqtt-port", type=int)
    addon_prove_parser.add_argument("--ha-mqtt-username")
    addon_prove_parser.add_argument("--ha-mqtt-password")
    addon_prove_parser.add_argument("--ha-mqtt-discovery-prefix")
    addon_prove_parser.add_argument("--ha-mqtt-mesh-topic")
    addon_prove_parser.add_argument("--ha-mqtt-device-id")
    addon_prove_parser.add_argument("--ha-mqtt-brightness-scale", type=int)
    addon_prove_parser.add_argument("--ha-mqtt-brightness-tolerance", type=int)
    addon_prove_parser.add_argument("--ha-mqtt-mired-tolerance", type=int)
    addon_prove_parser.add_argument("--ha-no-wait-state", action="store_true")
    addon_prove_parser.add_argument("--ha-no-observe", action="store_true")
    addon_prove_parser.add_argument("--ha-candidate-search", default=None, help="Search term for Home Assistant light candidate hints; defaults to the --ha-entity-id object id.")
    addon_prove_parser.add_argument(
        "--ha-relaxed-state-proof",
        action="store_true",
        help="Do not auto-require HA brightness/CCT attributes and MQTT bridge attribute fields.",
    )
    addon_prove_parser.add_argument("--ha-allow-missing-state", action="store_true")
    addon_prove_parser.add_argument("--ha-allow-service-error", action="store_true")
    addon_prove_parser.add_argument("--proof-run-id", default=None, help="Identifier shared by MQTT and Home Assistant proof events.")
    addon_prove_parser.add_argument(
        "--readiness-only",
        action="store_true",
        help="Only check Home Assistant API, retained MQTT discovery, and entity creation; do not move the light.",
    )
    addon_prove_parser.add_argument("--keep-proof-logs", action="store_true", help="Append to existing proof logs instead of starting with fresh logs.")
    addon_prove_parser.add_argument("--no-final-audit", action="store_true", help="Skip the combined final audit after both proof logs verify.")
    addon_prove_parser.add_argument("--allow-different-run-ids", action="store_true")
    addon_prove_parser.add_argument("--no-diagnostics", action="store_true", help="Do not collect diagnostics if a proof step fails.")
    addon_prove_parser.add_argument("--diagnostics-on-success", action="store_true", help="Collect diagnostics after a successful proof run.")
    addon_prove_parser.add_argument(
        "--docker-diagnostics",
        dest="skip_docker",
        action="store_false",
        default=True,
        help="Include local Docker checks/logs in diagnostics; off by default for Home Assistant add-on proof.",
    )
    addon_prove_parser.set_defaults(
        ha_list_candidates=False,
        ha_wait_attributes=False,
        ha_wait_mqtt_state=False,
        ha_wait_mqtt_attributes=False,
        skip_mqtt_connect_check=True,
        allow_unobserved=False,
        handler=prove_ha_addon,
    )

    runbook_parser = subparsers.add_parser(
        "addon-runbook",
        help="Print the Home Assistant add-on install and real-device proof runbook.",
    )
    runbook_parser.add_argument("--addon-archive", default=RUNBOOK_DEFAULT_ADDON_ARCHIVE)
    runbook_parser.add_argument("--ha-host", default="homeassistant.local")
    runbook_parser.add_argument("--ssh-user", default="root")
    runbook_parser.add_argument("--ha-url", default="http://homeassistant.local:8123")
    runbook_parser.add_argument("--ha-entity-id", default="light.skylight")
    runbook_parser.add_argument("--broker", default=HOST_MQTT_BROKER_PLACEHOLDER)
    runbook_parser.add_argument("--port", type=int, default=1883)
    runbook_parser.add_argument("--no-mqtt-auth", dest="mqtt_auth", action="store_false", default=True)
    runbook_parser.add_argument("--discovery-prefix")
    runbook_parser.add_argument("--mesh-topic")
    runbook_parser.add_argument("--device-id")
    runbook_parser.add_argument("--candidate-timeout", type=float, default=10.0)
    runbook_parser.add_argument("--cloud-region", choices=["asia", "europe"], default="europe")
    runbook_parser.add_argument("--cloud-home-id", default="")
    runbook_parser.add_argument("--output", help="Optional path to write the rendered runbook instead of printing it.")
    runbook_parser.set_defaults(handler=addon_runbook)

    host_check_parser = subparsers.add_parser(
        "addon-host-check",
        help="Check SSH, Home Assistant CLI, /addons, and /share before installing the add-on.",
    )
    host_check_parser.add_argument("--dry-run", action="store_true", help="Print commands without running them.")
    host_check_parser.add_argument("--ha-host", default="homeassistant.local")
    host_check_parser.add_argument("--ha-url", default="http://homeassistant.local:8123")
    host_check_parser.add_argument("--ha-connect-timeout", type=float, default=5.0)
    host_check_parser.add_argument("--ssh-user", default="root")
    add_addon_ssh_args(host_check_parser)
    host_check_parser.add_argument("--remote-addons-dir", default=DEFAULT_REMOTE_ADDONS_DIR)
    host_check_parser.add_argument("--remote-share-dir", default=DEFAULT_REMOTE_SHARE_DIR)
    host_check_parser.set_defaults(handler=addon_host_check)

    git_repo_parser = subparsers.add_parser(
        "addon-prepare-git-repo",
        help="Prepare a local bare Git repository URL for Home Assistant add-on repository install.",
    )
    git_repo_parser.add_argument("--dry-run", action="store_true", help="Print planned repository and serve commands without writing files.")
    git_repo_parser.add_argument("--repository-dir", default=DEFAULT_ADDON_REPOSITORY_DIR, help="Generated add-on repository folder containing repository.yaml.")
    git_repo_parser.add_argument("--output-dir", default=DEFAULT_ADDON_GIT_OUTPUT_DIR, help="Directory where the bare Git repository will be created.")
    git_repo_parser.add_argument("--repo-name", default=DEFAULT_ADDON_GIT_REPO_NAME)
    git_repo_parser.add_argument("--replace", action="store_true", help="Replace an existing prepared bare repository.")
    git_repo_parser.add_argument("--port", type=int, default=DEFAULT_ADDON_GIT_HTTP_PORT)
    git_repo_parser.add_argument("--bind", default="0.0.0.0")
    git_repo_parser.add_argument("--ha-host", default="homeassistant.local", help="Host hint used only for LAN URL detection.")
    git_repo_parser.set_defaults(handler=addon_prepare_git_repo)

    git_serve_parser = subparsers.add_parser(
        "addon-serve-git-repo",
        help="Prepare if needed and serve a temporary Home Assistant add-on repository over HTTP.",
    )
    git_serve_parser.add_argument("--dry-run", action="store_true", help="Print repository URLs without starting the HTTP server.")
    git_serve_parser.add_argument("--repository-dir", default=DEFAULT_ADDON_REPOSITORY_DIR, help="Generated add-on repository folder containing repository.yaml.")
    git_serve_parser.add_argument("--output-dir", default=DEFAULT_ADDON_GIT_OUTPUT_DIR, help="Directory containing or receiving the bare Git repository.")
    git_serve_parser.add_argument("--repo-name", default=DEFAULT_ADDON_GIT_REPO_NAME)
    git_serve_parser.add_argument("--replace", action="store_true", help="Recreate the prepared bare repository before serving.")
    git_serve_parser.add_argument("--no-prepare", dest="prepare", action="store_false", default=True, help="Do not prepare the bare repository if it is missing.")
    git_serve_parser.add_argument("--port", type=int, default=DEFAULT_ADDON_GIT_HTTP_PORT)
    git_serve_parser.add_argument("--bind", default="0.0.0.0")
    git_serve_parser.add_argument("--ha-host", default="homeassistant.local", help="Host hint used only for LAN URL detection.")
    git_serve_parser.set_defaults(handler=addon_serve_git_repo)

    ha_api_local_install_parser = subparsers.add_parser(
        "addon-ha-api-install-local-repo",
        help="Serve the generated repository temporarily, install the add-on through /api/hassio, and stop the server afterward.",
    )
    ha_api_local_install_parser.add_argument("--dry-run", action="store_true", help="Print repository and API actions without starting the HTTP server.")
    ha_api_local_install_parser.add_argument("--repository-dir", default=DEFAULT_ADDON_REPOSITORY_DIR, help="Generated add-on repository folder containing repository.yaml.")
    ha_api_local_install_parser.add_argument("--output-dir", default=DEFAULT_ADDON_GIT_OUTPUT_DIR, help="Directory containing or receiving the bare Git repository.")
    ha_api_local_install_parser.add_argument("--repo-name", default=DEFAULT_ADDON_GIT_REPO_NAME)
    ha_api_local_install_parser.add_argument("--replace", action="store_true", help="Recreate the prepared bare repository before serving.")
    ha_api_local_install_parser.add_argument("--no-prepare", dest="prepare", action="store_false", default=True, help="Do not prepare the bare repository if it is missing.")
    ha_api_local_install_parser.add_argument("--port", type=int, default=DEFAULT_ADDON_GIT_HTTP_PORT)
    ha_api_local_install_parser.add_argument("--bind", default="0.0.0.0")
    ha_api_local_install_parser.add_argument("--ha-host", default="homeassistant.local", help="Host hint used only for LAN URL detection and host checks.")
    ha_api_local_install_parser.add_argument("--ha-url", default=DEFAULT_HA_URL, help="Home Assistant Core URL; used to build /api/hassio when --hassio-url is omitted.")
    ha_api_local_install_parser.add_argument("--hassio-url", default="", help="Explicit Supervisor API base URL, for example http://supervisor or http://homeassistant.local:8123/api/hassio.")
    ha_api_local_install_parser.add_argument("--token", default="", help="Home Assistant/Supervisor API token. Prefer --token-file or HOME_ASSISTANT_TOKEN.")
    ha_api_local_install_parser.add_argument("--token-file", default="", help="File containing the Home Assistant/Supervisor API token.")
    ha_api_local_install_parser.add_argument("--token-env", default=DEFAULT_HASSIO_TOKEN_ENV, help="Environment variable containing the API token.")
    ha_api_local_install_parser.add_argument("--auth-header", choices=["authorization", "x-supervisor-token"], default="authorization")
    ha_api_local_install_parser.add_argument("--timeout", type=float, default=30.0)
    ha_api_local_install_parser.add_argument("--repository-url", default="", help="Override the repository URL Home Assistant should add; defaults to the first detected LAN URL.")
    ha_api_local_install_parser.add_argument("--slug", default=DEFAULT_ADDON_SLUG, help="Add-on config slug to find in the store response.")
    ha_api_local_install_parser.add_argument("--name", default=DEFAULT_ADDON_NAME, help="Add-on name to find in the store response.")
    ha_api_local_install_parser.add_argument("--store-slug", default="", help="Explicit store add-on slug if discovery cannot find it.")
    ha_api_local_install_parser.add_argument("--installed-slug", default="", help="Explicit installed add-on slug if it differs from the store slug.")
    ha_api_local_install_parser.add_argument("--operation", choices=ADDON_OPERATIONS, default="runtime-check")
    ha_api_local_install_parser.add_argument("--option", action="append", default=[], help="Additional add-on option override as KEY=VALUE. JSON values are accepted.")
    ha_api_local_install_parser.add_argument("--start", dest="start", action="store_true", default=True, help="Start the add-on after setting options. Enabled by default.")
    ha_api_local_install_parser.add_argument("--no-start", dest="start", action="store_false", help="Install and set options without starting the add-on.")
    ha_api_local_install_parser.add_argument("--logs", dest="logs", action="store_true", default=True, help="Fetch add-on logs after starting. Enabled by default.")
    ha_api_local_install_parser.add_argument("--no-logs", dest="logs", action="store_false", help="Do not fetch add-on logs after starting.")
    ha_api_local_install_parser.add_argument("--logs-delay", type=float, default=0.0, help="Seconds to wait before fetching logs after start when no log gate is required.")
    ha_api_local_install_parser.add_argument("--logs-tail", type=int, default=120, help="Print only the last N log lines; use 0 for all.")
    ha_api_local_install_parser.add_argument("--require-log-gate", dest="require_log_gate", action="store_true", default=True, help="After start, wait for the operation's latest add-on log block to contain its expected pass marker. Enabled by default.")
    ha_api_local_install_parser.add_argument("--no-require-log-gate", dest="require_log_gate", action="store_false", help="Do not require a log pass marker after starting.")
    ha_api_local_install_parser.add_argument("--gate-timeout", type=float, default=120.0, help="Seconds to wait for --require-log-gate.")
    ha_api_local_install_parser.add_argument("--gate-poll-interval", type=float, default=5.0, help="Seconds between add-on log polls for --require-log-gate.")
    ha_api_local_install_parser.add_argument("--skip-repository", action="store_true", help="Do not add the repository before installing.")
    ha_api_local_install_parser.add_argument("--skip-install", action="store_true", help="Do not call the install endpoint.")
    ha_api_local_install_parser.add_argument("--skip-options", action="store_true", help="Do not set add-on options.")
    ha_api_local_install_parser.add_argument("--no-repository-exists-ok", dest="repository_exists_ok", action="store_false", default=True)
    ha_api_local_install_parser.add_argument("--no-install-exists-ok", dest="install_exists_ok", action="store_false", default=True)
    ha_api_local_install_parser.set_defaults(handler=addon_ha_api_install_local_repo)

    ha_api_install_parser = subparsers.add_parser(
        "addon-ha-api-install",
        help="Use the Home Assistant/Supervisor API to add the repository, install the add-on, and set safe options.",
    )
    ha_api_install_parser.add_argument("--dry-run", action="store_true", help="Print API calls without sending them.")
    ha_api_install_parser.add_argument("--ha-url", default=DEFAULT_HA_URL, help="Home Assistant Core URL; used to build /api/hassio when --hassio-url is omitted.")
    ha_api_install_parser.add_argument("--hassio-url", default="", help="Explicit Supervisor API base URL, for example http://supervisor or http://homeassistant.local:8123/api/hassio.")
    ha_api_install_parser.add_argument("--token", default="", help="Home Assistant/Supervisor API token. Prefer --token-file or HOME_ASSISTANT_TOKEN.")
    ha_api_install_parser.add_argument("--token-file", default="", help="File containing the Home Assistant/Supervisor API token.")
    ha_api_install_parser.add_argument("--token-env", default=DEFAULT_HASSIO_TOKEN_ENV, help="Environment variable containing the API token.")
    ha_api_install_parser.add_argument("--auth-header", choices=["authorization", "x-supervisor-token"], default="authorization")
    ha_api_install_parser.add_argument("--timeout", type=float, default=30.0)
    ha_api_install_parser.add_argument("--repository-url", default="", help="Repository URL to add. Defaults to the first detected LAN URL for the prepared add-on repo.")
    ha_api_install_parser.add_argument("--repo-name", default=DEFAULT_ADDON_GIT_REPO_NAME, help="Repository name used only when deriving the default repository URL.")
    ha_api_install_parser.add_argument("--port", type=int, default=DEFAULT_ADDON_GIT_HTTP_PORT, help="Repository server port used only when deriving the default repository URL.")
    ha_api_install_parser.add_argument("--ha-host", default="homeassistant.local", help="Host hint used only when deriving the default repository URL.")
    ha_api_install_parser.add_argument("--slug", default=DEFAULT_ADDON_SLUG, help="Add-on config slug to find in the store response.")
    ha_api_install_parser.add_argument("--name", default=DEFAULT_ADDON_NAME, help="Add-on name to find in the store response.")
    ha_api_install_parser.add_argument("--store-slug", default="", help="Explicit store add-on slug if discovery cannot find it.")
    ha_api_install_parser.add_argument("--installed-slug", default="", help="Explicit installed add-on slug if it differs from the store slug.")
    ha_api_install_parser.add_argument("--operation", choices=ADDON_OPERATIONS, default="runtime-check")
    ha_api_install_parser.add_argument("--option", action="append", default=[], help="Additional add-on option override as KEY=VALUE. JSON values are accepted.")
    ha_api_install_parser.add_argument("--start", action="store_true", help="Start the add-on after setting options. Keep operation at a safe no-motion value for first runs.")
    ha_api_install_parser.add_argument("--logs", action="store_true", help="Fetch add-on logs after starting.")
    ha_api_install_parser.add_argument("--logs-delay", type=float, default=0.0, help="Seconds to wait before fetching logs after start when no log gate is required.")
    ha_api_install_parser.add_argument("--logs-tail", type=int, default=120, help="Print only the last N log lines; use 0 for all.")
    ha_api_install_parser.add_argument("--require-log-gate", action="store_true", help="After --start, wait for the operation's latest add-on log block to contain its expected pass marker.")
    ha_api_install_parser.add_argument("--gate-timeout", type=float, default=120.0, help="Seconds to wait for --require-log-gate.")
    ha_api_install_parser.add_argument("--gate-poll-interval", type=float, default=5.0, help="Seconds between add-on log polls for --require-log-gate.")
    ha_api_install_parser.add_argument("--skip-repository", action="store_true", help="Do not add the repository before installing.")
    ha_api_install_parser.add_argument("--skip-install", action="store_true", help="Do not call the install endpoint.")
    ha_api_install_parser.add_argument("--skip-options", action="store_true", help="Do not set add-on options.")
    ha_api_install_parser.add_argument("--no-repository-exists-ok", dest="repository_exists_ok", action="store_false", default=True)
    ha_api_install_parser.add_argument("--no-install-exists-ok", dest="install_exists_ok", action="store_false", default=True)
    ha_api_install_parser.set_defaults(handler=addon_ha_api_install)

    ha_api_operation_parser = subparsers.add_parser(
        "addon-ha-api-operation",
        help="Use the Home Assistant/Supervisor API to set an installed add-on operation, run it, and optionally fetch logs.",
    )
    ha_api_operation_parser.add_argument("operation", choices=ADDON_OPERATIONS)
    ha_api_operation_parser.add_argument("--dry-run", action="store_true", help="Print API calls without sending them.")
    ha_api_operation_parser.add_argument("--ha-url", default=DEFAULT_HA_URL, help="Home Assistant Core URL; used to build /api/hassio when --hassio-url is omitted.")
    ha_api_operation_parser.add_argument("--hassio-url", default="", help="Explicit Supervisor API base URL, for example http://supervisor or http://homeassistant.local:8123/api/hassio.")
    ha_api_operation_parser.add_argument("--token", default="", help="Home Assistant/Supervisor API token. Prefer --token-file or HOME_ASSISTANT_TOKEN.")
    ha_api_operation_parser.add_argument("--token-file", default="", help="File containing the Home Assistant/Supervisor API token.")
    ha_api_operation_parser.add_argument("--token-env", default=DEFAULT_HASSIO_TOKEN_ENV, help="Environment variable containing the API token.")
    ha_api_operation_parser.add_argument("--auth-header", choices=["authorization", "x-supervisor-token"], default="authorization")
    ha_api_operation_parser.add_argument("--timeout", type=float, default=30.0)
    ha_api_operation_parser.add_argument("--slug", default=DEFAULT_ADDON_SLUG, help="Configured add-on slug to find in the installed add-ons response.")
    ha_api_operation_parser.add_argument("--name", default=DEFAULT_ADDON_NAME, help="Add-on name to find in the installed add-ons response.")
    ha_api_operation_parser.add_argument("--installed-slug", default="", help="Explicit installed add-on slug if discovery cannot find it.")
    ha_api_operation_parser.add_argument("--no-discover-slug", action="store_true", help="Do not query /addons to find the installed slug.")
    ha_api_operation_parser.add_argument("--option", action="append", default=[], help="Additional add-on option override as KEY=VALUE. JSON values are accepted.")
    ha_api_operation_parser.add_argument("--run", choices=["none", "start", "restart", "stop"], default="none", help="Optionally start/restart/stop after setting options.")
    ha_api_operation_parser.add_argument("--skip-options", action="store_true", help="Do not set add-on options before running/logging.")
    ha_api_operation_parser.add_argument("--logs", action="store_true", help="Fetch add-on logs after setting options/running.")
    ha_api_operation_parser.add_argument("--logs-delay", type=float, default=0.0, help="Seconds to wait before fetching logs after a run action.")
    ha_api_operation_parser.add_argument("--logs-tail", type=int, default=120, help="Print only the last N log lines; use 0 for all.")
    ha_api_operation_parser.add_argument("--require-log-gate", action="store_true", help="Wait for the operation's latest add-on log block to contain its expected pass marker.")
    ha_api_operation_parser.add_argument("--gate-timeout", type=float, default=120.0, help="Seconds to wait for --require-log-gate.")
    ha_api_operation_parser.add_argument("--gate-poll-interval", type=float, default=5.0, help="Seconds between add-on log polls for --require-log-gate.")
    ha_api_operation_parser.add_argument("--cloud-token", default="")
    ha_api_operation_parser.add_argument("--cloud-token-file", default="")
    ha_api_operation_parser.add_argument("--cloud-token-env", default="PESETECH_CLOUD_TOKEN")
    ha_api_operation_parser.add_argument("--cloud-username", default="")
    ha_api_operation_parser.add_argument("--cloud-username-file", default="")
    ha_api_operation_parser.add_argument("--cloud-username-env", default="PESETECH_CLOUD_USERNAME")
    ha_api_operation_parser.add_argument("--cloud-password", default="")
    ha_api_operation_parser.add_argument("--cloud-password-file", default="")
    ha_api_operation_parser.add_argument("--cloud-password-env", default="PESETECH_CLOUD_PASSWORD")
    ha_api_operation_parser.add_argument("--cloud-region", choices=["asia", "europe"])
    ha_api_operation_parser.add_argument("--cloud-home-id")
    ha_api_operation_parser.add_argument("--cloud-candidate", type=int)
    ha_api_operation_parser.add_argument("--import-mesh-candidate", type=int)
    ha_api_operation_parser.add_argument("--import-node-uuid")
    ha_api_operation_parser.add_argument("--import-node-unicast")
    ha_api_operation_parser.add_argument("--import-local-address")
    ha_api_operation_parser.add_argument("--import-force", action="store_true")
    ha_api_operation_parser.add_argument("--addon-ha-url", help="ha_url option passed into the add-on for HA-target operations.")
    ha_api_operation_parser.add_argument("--ha-entity-id")
    ha_api_operation_parser.add_argument("--discovery-prefix")
    ha_api_operation_parser.add_argument("--node-id")
    ha_api_operation_parser.add_argument("--device-id")
    ha_api_operation_parser.set_defaults(handler=addon_ha_api_operation)

    ha_api_sequence_parser = subparsers.add_parser(
        "addon-ha-api-sequence",
        help="Use the Home Assistant/Supervisor API to run ordered add-on setup gates, stopping before movement by default.",
    )
    ha_api_sequence_parser.add_argument("--dry-run", action="store_true", help="Print API calls without sending them.")
    ha_api_sequence_parser.add_argument("--ha-url", default=DEFAULT_HA_URL, help="Home Assistant Core URL; used to build /api/hassio when --hassio-url is omitted.")
    ha_api_sequence_parser.add_argument("--hassio-url", default="", help="Explicit Supervisor API base URL, for example http://supervisor or http://homeassistant.local:8123/api/hassio.")
    ha_api_sequence_parser.add_argument("--token", default="", help="Home Assistant/Supervisor API token. Prefer --token-file or HOME_ASSISTANT_TOKEN.")
    ha_api_sequence_parser.add_argument("--token-file", default="", help="File containing the Home Assistant/Supervisor API token.")
    ha_api_sequence_parser.add_argument("--token-env", default=DEFAULT_HASSIO_TOKEN_ENV, help="Environment variable containing the API token.")
    ha_api_sequence_parser.add_argument("--auth-header", choices=["authorization", "x-supervisor-token"], default="authorization")
    ha_api_sequence_parser.add_argument("--timeout", type=float, default=30.0)
    ha_api_sequence_parser.add_argument("--slug", default=DEFAULT_ADDON_SLUG, help="Configured add-on slug to find in the installed add-ons response.")
    ha_api_sequence_parser.add_argument("--name", default=DEFAULT_ADDON_NAME, help="Add-on name to find in the installed add-ons response.")
    ha_api_sequence_parser.add_argument("--installed-slug", default="", help="Explicit installed add-on slug if discovery cannot find it.")
    ha_api_sequence_parser.add_argument("--no-discover-slug", action="store_true", help="Do not query /addons to find the installed slug.")
    ha_api_sequence_parser.add_argument("--through", choices=ADDON_API_SEQUENCE, default="readiness-test", help="Last operation to run in the ordered sequence.")
    ha_api_sequence_parser.add_argument("--allow-movement", action="store_true", help="Allow movement operations when --through includes move-test, ha-service-test, or proof-test.")
    ha_api_sequence_parser.add_argument("--skip-cloud-fetch", action="store_true", help="Skip cloud-fetch when mesh JSON is already staged or imported.")
    ha_api_sequence_parser.add_argument("--option", action="append", default=[], help="Additional add-on option override as KEY=VALUE. JSON values are accepted.")
    ha_api_sequence_parser.add_argument("--logs", dest="logs", action="store_true", default=True, help="Fetch logs after every operation.")
    ha_api_sequence_parser.add_argument("--no-logs", dest="logs", action="store_false", help="Do not fetch logs after each operation.")
    ha_api_sequence_parser.add_argument("--logs-delay", type=float, default=0.0, help="Seconds to wait before fetching logs after each run action.")
    ha_api_sequence_parser.add_argument("--logs-tail", type=int, default=120, help="Print only the last N log lines; use 0 for all.")
    ha_api_sequence_parser.add_argument("--verify-log-gates", dest="verify_log_gates", action="store_true", default=True, help="Require pass markers in the latest add-on log block for operations that have gate markers.")
    ha_api_sequence_parser.add_argument("--no-verify-log-gates", dest="verify_log_gates", action="store_false", help="Do not require log pass markers during the sequence.")
    ha_api_sequence_parser.add_argument("--gate-timeout", type=float, default=120.0, help="Seconds to wait for each operation log gate.")
    ha_api_sequence_parser.add_argument("--gate-poll-interval", type=float, default=5.0, help="Seconds between add-on log polls while waiting for a gate marker.")
    ha_api_sequence_parser.add_argument("--cloud-token", default="")
    ha_api_sequence_parser.add_argument("--cloud-token-file", default="")
    ha_api_sequence_parser.add_argument("--cloud-token-env", default="PESETECH_CLOUD_TOKEN")
    ha_api_sequence_parser.add_argument("--cloud-username", default="")
    ha_api_sequence_parser.add_argument("--cloud-username-file", default="")
    ha_api_sequence_parser.add_argument("--cloud-username-env", default="PESETECH_CLOUD_USERNAME")
    ha_api_sequence_parser.add_argument("--cloud-password", default="")
    ha_api_sequence_parser.add_argument("--cloud-password-file", default="")
    ha_api_sequence_parser.add_argument("--cloud-password-env", default="PESETECH_CLOUD_PASSWORD")
    ha_api_sequence_parser.add_argument("--cloud-region", choices=["asia", "europe"])
    ha_api_sequence_parser.add_argument("--cloud-home-id")
    ha_api_sequence_parser.add_argument("--cloud-candidate", type=int)
    ha_api_sequence_parser.add_argument("--import-mesh-candidate", type=int)
    ha_api_sequence_parser.add_argument("--import-node-uuid")
    ha_api_sequence_parser.add_argument("--import-node-unicast")
    ha_api_sequence_parser.add_argument("--import-local-address")
    ha_api_sequence_parser.add_argument("--import-force", action="store_true")
    ha_api_sequence_parser.add_argument("--addon-ha-url", help="ha_url option passed into the add-on for HA-target operations.")
    ha_api_sequence_parser.add_argument("--ha-entity-id")
    ha_api_sequence_parser.add_argument("--discovery-prefix")
    ha_api_sequence_parser.add_argument("--node-id")
    ha_api_sequence_parser.add_argument("--device-id")
    ha_api_sequence_parser.set_defaults(handler=addon_ha_api_sequence)

    install_parser = subparsers.add_parser(
        "addon-install",
        help="Copy and extract the generated local Home Assistant add-on archive over SSH.",
    )
    install_parser.add_argument("--dry-run", action="store_true", help="Print commands without running them.")
    install_parser.add_argument("--addon-archive", default=RUNBOOK_DEFAULT_ADDON_ARCHIVE)
    install_parser.add_argument("--ha-host", default="homeassistant.local")
    install_parser.add_argument("--ssh-user", default="root")
    add_addon_ssh_args(install_parser)
    install_parser.add_argument("--remote-addons-dir", default=DEFAULT_REMOTE_ADDONS_DIR)
    install_parser.add_argument("--slug", default=DEFAULT_ADDON_SLUG)
    install_parser.add_argument("--replace", action="store_true", help="Remove the remote add-on folder before extracting the archive.")
    install_parser.add_argument("--skip-verify", action="store_true", help="Skip local archive verification before copying.")
    install_parser.set_defaults(handler=addon_install)

    cloud_upload_parser = subparsers.add_parser(
        "addon-upload-cloud",
        help="Upload Pesetech cloud token or username/password files to Home Assistant /share for add-on cloud-fetch.",
    )
    cloud_upload_parser.add_argument("--dry-run", action="store_true", help="Print commands without running them.")
    cloud_upload_parser.add_argument("--ha-host", default="homeassistant.local")
    cloud_upload_parser.add_argument("--ssh-user", default="root")
    add_addon_ssh_args(cloud_upload_parser)
    cloud_upload_parser.add_argument("--remote-share-dir", default=DEFAULT_REMOTE_SHARE_DIR)
    cloud_upload_parser.add_argument("--token-file")
    cloud_upload_parser.add_argument("--username-file")
    cloud_upload_parser.add_argument("--password-file")
    cloud_upload_parser.add_argument("--remote-token-name", default=DEFAULT_CLOUD_TOKEN_NAME)
    cloud_upload_parser.add_argument("--remote-username-name", default=DEFAULT_CLOUD_USERNAME_NAME)
    cloud_upload_parser.add_argument("--remote-password-name", default=DEFAULT_CLOUD_PASSWORD_NAME)
    cloud_upload_parser.set_defaults(handler=addon_upload_cloud_credentials)

    mesh_upload_parser = subparsers.add_parser(
        "addon-upload-mesh",
        help="Upload an extracted Telink/Pesetech mesh JSON to Home Assistant /share for add-on import-check.",
    )
    mesh_upload_parser.add_argument("--dry-run", action="store_true", help="Print commands without running them.")
    mesh_upload_parser.add_argument("--ha-host", default="homeassistant.local")
    mesh_upload_parser.add_argument("--ssh-user", default="root")
    add_addon_ssh_args(mesh_upload_parser)
    mesh_upload_parser.add_argument("--remote-share-dir", default=DEFAULT_REMOTE_SHARE_DIR)
    mesh_upload_parser.add_argument("--mesh-json", required=True)
    mesh_upload_parser.add_argument("--remote-name", default=DEFAULT_MESH_JSON_NAME)
    mesh_upload_parser.add_argument("--skip-validate", action="store_true", help="Skip local key-free mesh candidate listing before upload.")
    mesh_upload_parser.set_defaults(handler=addon_upload_mesh)

    set_operation_parser = subparsers.add_parser(
        "addon-set-operation",
        help="Upload a non-secret /share operation override for the next Home Assistant add-on run.",
    )
    set_operation_parser.add_argument("operation", choices=ADDON_OPERATIONS)
    set_operation_parser.add_argument("--dry-run", action="store_true", help="Print commands without running them.")
    set_operation_parser.add_argument("--ha-host", default="homeassistant.local")
    set_operation_parser.add_argument("--ssh-user", default="root")
    add_addon_ssh_args(set_operation_parser)
    set_operation_parser.add_argument("--remote-share-dir", default=DEFAULT_REMOTE_SHARE_DIR)
    set_operation_parser.add_argument("--remote-name", default=DEFAULT_OPERATION_OVERRIDE_NAME)
    set_operation_parser.add_argument("--slug", default=DEFAULT_ADDON_SLUG)
    set_operation_parser.add_argument("--run", choices=["none", "start", "restart"], default="none", help="Optionally run Home Assistant CLI apps/addons start/restart after uploading the override.")
    set_operation_parser.add_argument("--discovery-prefix")
    set_operation_parser.add_argument("--node-id")
    set_operation_parser.add_argument("--device-id")
    set_operation_parser.add_argument("--skylight-name")
    set_operation_parser.add_argument("--skylight-uuid")
    set_operation_parser.add_argument("--mesh-json-path")
    set_operation_parser.add_argument("--import-mesh-candidate", type=int)
    set_operation_parser.add_argument("--import-node-uuid")
    set_operation_parser.add_argument("--import-node-unicast")
    set_operation_parser.add_argument("--import-local-address")
    set_operation_parser.add_argument("--import-force", action="store_true")
    set_operation_parser.add_argument("--cloud-region", choices=["asia", "europe"])
    set_operation_parser.add_argument("--cloud-base-url")
    set_operation_parser.add_argument("--cloud-output-path")
    set_operation_parser.add_argument("--cloud-raw-output-path")
    set_operation_parser.add_argument("--cloud-report-path")
    set_operation_parser.add_argument("--cloud-candidate", type=int)
    set_operation_parser.add_argument("--cloud-home-id")
    set_operation_parser.add_argument("--ha-url")
    set_operation_parser.add_argument("--ha-entity-id")
    set_operation_parser.add_argument("--relay", action="store_true")
    set_operation_parser.set_defaults(handler=addon_set_operation)

    fetch_cloud_report_parser = subparsers.add_parser(
        "addon-fetch-cloud-report",
        help="Copy /share/pesetech_cloud_fetch_report.json from Home Assistant and print a key-free summary.",
    )
    fetch_cloud_report_parser.add_argument("--dry-run", action="store_true", help="Print commands without running them.")
    fetch_cloud_report_parser.add_argument("--ha-host", default="homeassistant.local")
    fetch_cloud_report_parser.add_argument("--ssh-user", default="root")
    add_addon_ssh_args(fetch_cloud_report_parser)
    fetch_cloud_report_parser.add_argument("--remote-report", default="/share/pesetech_cloud_fetch_report.json")
    fetch_cloud_report_parser.add_argument("--output-dir", default="/tmp/pesetech-cloud-reports")
    fetch_cloud_report_parser.add_argument("--no-summary", action="store_true", help="Only copy the report; skip the local key-free summary.")
    fetch_cloud_report_parser.set_defaults(handler=addon_fetch_cloud_report)

    fetch_status_parser = subparsers.add_parser(
        "addon-fetch-status",
        help="Copy /share/pesetech-status.json from Home Assistant and print the next-step summary.",
    )
    fetch_status_parser.add_argument("--dry-run", action="store_true", help="Print commands without running them.")
    fetch_status_parser.add_argument("--ha-host", default="homeassistant.local")
    fetch_status_parser.add_argument("--ssh-user", default="root")
    add_addon_ssh_args(fetch_status_parser)
    fetch_status_parser.add_argument("--remote-report", default="/share/pesetech-status.json")
    fetch_status_parser.add_argument("--output-dir", default="/tmp/pesetech-status-reports")
    fetch_status_parser.add_argument("--no-summary", action="store_true", help="Only copy the report; skip the local next-step summary.")
    fetch_status_parser.set_defaults(handler=addon_fetch_status_report)

    fetch_report_parser = subparsers.add_parser(
        "addon-fetch-report",
        help="Copy a key-free add-on report mirrored to Home Assistant /share.",
    )
    fetch_report_parser.add_argument("report", choices=sorted(ADDON_SHARED_REPORTS))
    fetch_report_parser.add_argument("--dry-run", action="store_true", help="Print commands without running them.")
    fetch_report_parser.add_argument("--ha-host", default="homeassistant.local")
    fetch_report_parser.add_argument("--ssh-user", default="root")
    add_addon_ssh_args(fetch_report_parser)
    fetch_report_parser.add_argument("--remote-report", help="Override the remote report path.")
    fetch_report_parser.add_argument("--output-dir", default=DEFAULT_ADDON_REPORTS_OUTPUT_DIR)
    fetch_report_parser.set_defaults(handler=addon_fetch_report)

    fetch_diag_parser = subparsers.add_parser(
        "addon-fetch-diagnostics",
        help="Copy the latest Home Assistant add-on diagnostics bundle from /share and review it locally.",
    )
    fetch_diag_parser.add_argument("--dry-run", action="store_true", help="Print commands without running them.")
    fetch_diag_parser.add_argument("--ha-host", default="homeassistant.local")
    fetch_diag_parser.add_argument("--ssh-user", default="root")
    add_addon_ssh_args(fetch_diag_parser)
    fetch_diag_parser.add_argument("--remote-glob", default=DEFAULT_REMOTE_DIAGNOSTICS_GLOB)
    fetch_diag_parser.add_argument("--output-dir", default=DEFAULT_DIAGNOSTICS_OUTPUT_DIR)
    fetch_diag_parser.add_argument("--no-review", action="store_true", help="Only copy the archive; skip local diagnostics review.")
    fetch_diag_parser.set_defaults(handler=addon_fetch_diagnostics)

    args = parser.parse_args()
    raise SystemExit(args.handler(args))


if __name__ == "__main__":
    main()
