#!/usr/bin/env python3
import argparse
import fnmatch
import posixpath
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path


DEFAULT_SLUG = "pesetech_ble_mesh"
MAX_TEXT_BYTES = 1024 * 1024


REQUIRED_ROOT_FILES = [
    "repository.yaml",
    "README.md",
    "INSTALL.md",
]


REQUIRED_APP_FILES = [
    "CHANGELOG.md",
    "DOCS.md",
    "README.md",
    "config.yaml",
    "run.sh",
]


REQUIRED_SOURCE_FILES = [
    "requirements.txt",
    "docker/scripts/install-bluez.sh",
    "docker/scripts/install-ell.sh",
    "docker/scripts/install-json-c.sh",
    "gateway/gateway.py",
    "scripts/pesetech_addon_config.py",
    "scripts/pesetech_addon_status.py",
    "scripts/pesetech_ble_scan.py",
    "scripts/pesetech_bluez_mesh_introspect.py",
    "scripts/pesetech_cloud_report_summary.py",
    "scripts/pesetech_diagnostics.py",
    "scripts/pesetech_extract_mesh_json.py",
    "scripts/pesetech_fetch_cloud_mesh.py",
    "scripts/pesetech_ha_service_smoke.py",
    "scripts/pesetech_import_telink_mesh.py",
    "scripts/pesetech_mqtt_discovery.py",
    "scripts/pesetech_mqtt_smoke.py",
    "scripts/pesetech_preflight.py",
    "scripts/pesetech_real_device_audit.py",
    "scripts/pesetech_review_diagnostics.py",
    "scripts/pesetech_runtime_check.py",
    "scripts/pesetech_status_report_summary.py",
    "scripts/pesetech_verify_addon_package.py",
    "scripts/pesetech_verify_ha_service_proof.py",
    "scripts/pesetech_verify_proof.py",
]


CONFIG_SNIPPETS = [
    'operation: "runtime-check"',
    "homeassistant_api: true",
    "host_network: true",
    "udev: true",
    "full_access: true",
    "privileged:\n  - NET_ADMIN\n  - NET_RAW\n  - SYS_ADMIN\n  - SYS_RAWIO",
    "apparmor: false",
    "services:\n  - mqtt:want",
    "mqtt_from_supervisor: true",
    'ha_url: "http://supervisor/core"',
    'ha_entity_id: "light.skylight"',
    'mesh_json_path: "/share/pesetech_mesh.json"',
    'mesh_io: ""',
    'mesh_startup_timeout: 5',
    "mesh_adapter_power_off: false",
    "import_mesh_candidate: 0",
    'import_mesh_candidate: "int(0,100)"',
    "  - aarch64",
    "  - amd64",
]


REQUIRED_OPERATIONS = [
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
    "move-test",
    "ha-api-check",
    "ha-service-test",
    "proof-test",
    "diagnostics",
    "list",
]


RUN_SH_SNIPPETS = [
    "pesetech_addon_config.py",
    "pesetech_next_operation.json",
    "Pesetech operation gate:",
    "--override",
    "pesetech_addon_status.py",
    "pesetech_bluez_mesh_introspect.py",
    "pesetech-runtime-check.json",
    "pesetech-mesh-daemon-check.json",
    "pesetech-ble-scan.json",
    "pesetech_ble_scan.py",
    "pesetech-import-check.json",
    "pesetech_cloud_report_summary.py",
    "pesetech-readiness.json",
    "pesetech-move-test.jsonl",
    "pesetech-ha-service-proof.jsonl",
    "pesetech-final-audit.json",
    "pesetech_real_device_audit.py",
    "gateway.py --basedir",
]


STATUS_SCRIPT_SNIPPETS = [
    "strict_proof",
    "import_check",
    "Strict host proof",
    "HOME_ASSISTANT_TOKEN",
    "prove-ha-addon",
    "--readiness-only",
    "--candidate-timeout",
    "next_operation",
    "configuration_snippet",
    "moves_real_light",
]


DOCKERFILE_SNIPPETS = [
    "FROM ",
    "ARG BUILD_VERSION=",
    "ARG BUILD_ARCH=",
    'io.hass.version="${BUILD_VERSION}"',
    'io.hass.type="app"',
    'io.hass.arch="${BUILD_ARCH}"',
    "COPY source/ .",
    "COPY run.sh /run.sh",
    'CMD [ "/run.sh" ]',
]


FORBIDDEN_PATTERNS = [
    ".git/*",
    "*/.git/*",
    "__pycache__/*",
    "*/__pycache__/*",
    "*.pyc",
    ".pytest_cache/*",
    "*/.pytest_cache/*",
    ".venv/*",
    "*/.venv/*",
    "docker/config/config.yaml",
    "docker/config/store.yaml",
    "*/docker/config/config.yaml",
    "*/docker/config/store.yaml",
    "*/docker/config/store.bak.yaml",
    "*/docker/config/.cache/*",
    "*/docker/config/mesh-storage/*",
    "*/docker/config/pesetech-proof.jsonl",
    "*/docker/config/pesetech-ha-service-proof.jsonl",
    "*/docker/config/pesetech-move-test.jsonl",
    "*/docker/config/pesetech-final-audit.json",
    "*/docker/config/pesetech-preflight.json",
    "*/docker/config/pesetech-import-check.json",
    "*/docker/config/pesetech-readiness.json",
    "*/docker/config/pesetech-status.json",
    "*/docker/config/pesetech-runtime-check.json",
    "*/docker/config/pesetech-mesh-daemon-check.json",
    "*/docker/config/pesetech-diagnostics-*.tar.gz",
    "*/pesetech_cloud_token.txt",
    "*/pesetech_cloud_username.txt",
    "*/pesetech_cloud_password.txt",
    "*/pesetech_cloud_raw*.json",
    "pesetech_mesh.json",
    "*/pesetech_mesh.json",
]


class PackageView:
    def __init__(self, names, texts, errors=None):
        self.names = set(names)
        self.texts = dict(texts)
        self.errors = list(errors or [])

    def text(self, name):
        return self.texts.get(name, "")


def normalize_name(name):
    raw = str(name).replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    normalized = posixpath.normpath(raw)
    if normalized in ("", "."):
        return ""
    return normalized


def is_dangerous_archive_name(name):
    raw = str(name).replace("\\", "/")
    normalized = normalize_name(raw)
    return (
        raw.startswith("/")
        or normalized == ".."
        or normalized.startswith("../")
        or "/../" in f"/{normalized}/"
    )


def read_member_text(archive, member):
    if member.size > MAX_TEXT_BYTES:
        return ""
    file_obj = archive.extractfile(member)
    if file_obj is None:
        return ""
    return file_obj.read().decode("utf-8", errors="replace")


def collect_archive(path):
    names = set()
    texts = {}
    errors = []
    with tarfile.open(path, "r:*") as archive:
        for member in archive.getmembers():
            if is_dangerous_archive_name(member.name):
                errors.append(f"dangerous archive path: {member.name}")
                continue
            name = normalize_name(member.name)
            if not name:
                continue
            if member.issym() or member.islnk():
                errors.append(f"archive contains link member: {name}")
                continue
            if not member.isfile():
                continue
            names.add(name)
            texts[name] = read_member_text(archive, member)
    return PackageView(names, texts, errors)


def collect_directory(path):
    names = set()
    texts = {}
    root = Path(path)
    errors = []
    for child in root.rglob("*"):
        if not child.is_file():
            continue
        name = child.relative_to(root).as_posix()
        names.add(name)
        try:
            if child.stat().st_size <= MAX_TEXT_BYTES:
                texts[name] = child.read_text(encoding="utf-8", errors="replace")
            else:
                texts[name] = ""
        except OSError as exc:
            errors.append(f"could not read {name}: {exc}")
    return PackageView(names, texts, errors)


def collect_package(path):
    path = Path(path)
    if path.is_dir():
        return collect_directory(path)
    if path.is_file():
        try:
            return collect_archive(path)
        except tarfile.TarError as exc:
            return PackageView(set(), {}, [f"could not read tar archive: {exc}"])
    return PackageView(set(), {}, [f"path does not exist: {path}"])


def has_forbidden_file(name):
    return any(fnmatch.fnmatch(name, pattern) for pattern in FORBIDDEN_PATTERNS)


def validate_required_files(view, slug, *, include_repository_root=True):
    errors = []
    required = list(REQUIRED_ROOT_FILES) if include_repository_root else []
    required.extend(f"{slug}/{name}" for name in REQUIRED_APP_FILES)
    required.extend(f"{slug}/source/{name}" for name in REQUIRED_SOURCE_FILES)
    for name in required:
        if name not in view.names:
            errors.append(f"missing required file: {name}")
    return errors


def validate_no_unexpected_config_yaml(view, slug):
    errors = []
    allowed = {f"{slug}/config.yaml"}
    config_files = {
        name for name in view.names if name == "config.yaml" or name.endswith("/config.yaml")
    }
    if allowed.isdisjoint(config_files):
        errors.append(f"missing Home Assistant app config.yaml at {slug}/config.yaml")
    for name in sorted(view.names):
        if name == "config.yaml" or name.endswith("/config.yaml"):
            if name not in allowed:
                errors.append(f"unexpected config.yaml that Home Assistant may scan: {name}")
    return errors


def validate_forbidden_files(view):
    errors = []
    for name in sorted(view.names):
        if has_forbidden_file(name):
            errors.append(f"forbidden packaged file: {name}")
    return errors


def validate_local_app_shape(view, slug):
    errors = []
    prefix = f"{slug}/"
    for name in sorted(view.names):
        if not name.startswith(prefix):
            errors.append(f"local app archive contains file outside {slug}/: {name}")
    return errors


def validate_repository_yaml(view):
    text = view.text("repository.yaml")
    errors = []
    for snippet in ("name:", "url:", "maintainer:"):
        if snippet not in text:
            errors.append(f"repository.yaml is missing {snippet}")
    return errors


def validate_config_yaml(view, slug):
    name = f"{slug}/config.yaml"
    text = view.text(name)
    errors = []
    if not text:
        return [f"{name} is missing or empty"]
    if f'slug: "{slug}"' not in text:
        errors.append(f"{name} does not declare slug {slug!r}")
    if f"{slug}/Dockerfile" not in view.names and "image:" not in text:
        errors.append(f"{name} must declare image: when no Dockerfile is packaged")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("image:"):
            image = stripped.split(":", 1)[1].strip().strip('"\'')
            slash = image.rfind("/")
            colon = image.rfind(":")
            if colon > slash:
                errors.append(f"{name} image must omit a tag; Supervisor tags it from version")
    for snippet in CONFIG_SNIPPETS:
        if snippet not in text:
            errors.append(f"{name} is missing expected setting: {snippet}")
    for operation in REQUIRED_OPERATIONS:
        if operation not in text:
            errors.append(f"{name} schema does not include operation {operation!r}")
    return errors


def validate_run_script(view, slug):
    name = f"{slug}/run.sh"
    text = view.text(name)
    errors = []
    if not text:
        return [f"{name} is missing or empty"]
    for snippet in RUN_SH_SNIPPETS:
        if snippet not in text:
            errors.append(f"{name} is missing expected runtime hook: {snippet}")
    for operation in REQUIRED_OPERATIONS:
        token = f'{operation})' if operation not in {"runtime-check", "mesh-daemon-check", "ble-scan", "status", "cloud-fetch", "preflight", "ha-api-check", "diagnostics", "import-check"} else operation
        if token not in text:
            errors.append(f"{name} does not appear to handle operation {operation!r}")
    bash = shutil.which("bash")
    if bash:
        try:
            result = subprocess.run(
                [bash, "-n"],
                input=text,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
        except subprocess.TimeoutExpired:
            errors.append(f"{name} bash syntax check timed out")
        else:
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip().splitlines()
                if detail:
                    errors.append(f"{name} failed bash syntax check: {detail[-1]}")
                else:
                    errors.append(f"{name} failed bash syntax check with exit code {result.returncode}")
    return errors


def validate_status_script(view, slug):
    name = f"{slug}/source/scripts/pesetech_addon_status.py"
    text = view.text(name)
    errors = []
    if not text:
        return [f"{name} is missing or empty"]
    for snippet in STATUS_SCRIPT_SNIPPETS:
        if snippet not in text:
            errors.append(f"{name} is missing expected status guidance: {snippet}")
    return errors


def validate_dockerfile(view, slug):
    name = f"{slug}/Dockerfile"
    if name not in view.names:
        return []
    text = view.text(name)
    errors = []
    if not text:
        return [f"{name} is missing or empty"]
    for snippet in DOCKERFILE_SNIPPETS:
        if snippet not in text:
            errors.append(f"{name} is missing expected Home Assistant app Dockerfile setting: {snippet}")
    if "FROM ${BUILD_FROM}" in text or "FROM $BUILD_FROM" in text:
        errors.append(f"{name} must use an explicit base image, not Supervisor BUILD_FROM fallback")
    return errors


def verify_addon_package(path, slug=DEFAULT_SLUG, *, local_app=False):
    view = collect_package(path)
    errors = list(view.errors)
    if local_app:
        errors.extend(validate_local_app_shape(view, slug))
    errors.extend(validate_required_files(view, slug, include_repository_root=not local_app))
    errors.extend(validate_no_unexpected_config_yaml(view, slug))
    errors.extend(validate_forbidden_files(view))
    if not local_app:
        errors.extend(validate_repository_yaml(view))
    errors.extend(validate_config_yaml(view, slug))
    errors.extend(validate_dockerfile(view, slug))
    errors.extend(validate_run_script(view, slug))
    errors.extend(validate_status_script(view, slug))
    return errors


def print_result(path, errors, *, local_app=False):
    package_kind = "local app folder" if local_app else "app repository"
    print(f"Pesetech Home Assistant {package_kind}: {path}")
    if errors:
        print("Package verification failed:")
        for error in errors:
            print(f"  - {error}")
        return
    print("Package verification passed.")
    if local_app:
        print("The archive/folder has the expected Home Assistant local app layout and no known runtime secret/state files.")
    else:
        print("The archive/folder has the expected Home Assistant repository layout and no known runtime secret/state files.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Verify the generated Pesetech Home Assistant app/add-on package.")
    parser.add_argument("path", help="Generated add-on repository folder or .tar.gz archive.")
    parser.add_argument("--slug", default=DEFAULT_SLUG, help="Expected Home Assistant app slug.")
    parser.add_argument("--local-app", action="store_true", help="Verify an archive/folder containing only the app slug folder for direct /addons install.")
    args = parser.parse_args(argv)

    errors = verify_addon_package(args.path, slug=args.slug, local_app=args.local_app)
    print_result(args.path, errors, local_app=args.local_app)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
