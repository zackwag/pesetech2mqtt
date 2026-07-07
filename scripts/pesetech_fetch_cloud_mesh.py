#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pesetech_extract_mesh_json import print_candidates, select_candidate, summarize_candidate, write_storage
from pesetech_import_telink_mesh import ImportErrorWithDetail, find_mesh_storage_candidates


REGION_BASE_URLS = {
    "europe": "https://service.lepuiot.com",
    "asia": "http://test.lepuiot.com",
}
DEFAULT_REGION = "europe"
DEFAULT_BASE_URL = REGION_BASE_URLS[DEFAULT_REGION]
DEFAULT_USER_AGENT = "enPeseTech.android13.1.0.17-googleplay.Codex.generic.local"
LOGIN_PATH = "/app/customer-login/login"
ENDPOINTS = {
    "home-list": "/app/voice/homeList",
    "mesh-json-by-home-id": "/app/homeSource/getMeshJsonByHomeId",
    "sync-data": "/app/homeSource/syncData",
}
DEFAULT_ENDPOINTS = ["home-list", "mesh-json-by-home-id", "sync-data"]
HOME_MESH_ENDPOINT = "mesh-json-by-home-id"
HOME_ID_FIELD_NAMES = {"homeid"}
HOME_NAME_FIELD_NAMES = {"homename", "name"}


class CloudFetchError(RuntimeError):
    pass


SENSITIVE_ERROR_MARKERS = ("authorization", "bearer", "password", "secret", "token")


def normalize_base_url(value):
    return value.rstrip("/") + "/"


def resolve_base_url(args):
    base_url = getattr(args, "base_url", None)
    if base_url:
        return base_url
    region = (getattr(args, "region", None) or DEFAULT_REGION).lower()
    try:
        return REGION_BASE_URLS[region]
    except KeyError as exc:
        raise CloudFetchError(f"region must be one of {', '.join(sorted(REGION_BASE_URLS))}.") from exc


def normalize_token(value):
    token = (value or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def dict_get_ci(value, *names):
    if not isinstance(value, dict):
        return None
    wanted = {str(name).lower() for name in names}
    for key, child in value.items():
        if str(key).lower() in wanted:
            return child
    return None


def base_response_code(payload):
    code = dict_get_ci(payload, "code")
    if isinstance(code, str):
        stripped = code.strip()
        if stripped.isdigit():
            return int(stripped)
    return code


def base_response_message(payload):
    return dict_get_ci(payload, "msg", "message", "error")


def base_response_data(payload):
    return dict_get_ci(payload, "data")


def read_secret(value=None, file_path=None, env_name=None):
    if value:
        return value.strip()
    if file_path:
        return Path(file_path).read_text(encoding="utf-8").strip()
    if env_name:
        return os.environ.get(env_name, "").strip()
    return ""


def read_token(args):
    if args.token:
        return normalize_token(args.token)
    if args.token_file:
        return normalize_token(Path(args.token_file).read_text(encoding="utf-8"))
    return normalize_token(os.environ.get(args.token_env, ""))


def read_login_credentials(args):
    username = read_secret(args.username, args.username_file, args.username_env)
    password = read_secret(args.password, args.password_file, args.password_env)
    if bool(username) != bool(password):
        raise CloudFetchError("provide both username and password, or provide neither.")
    return username, password


def fetch_json(base_url, path, token=None, body=None, timeout=20, user_agent=DEFAULT_USER_AGENT, accept_language="en"):
    url = urljoin(normalize_base_url(base_url), path.lstrip("/"))
    encoded_body = None if body is None else json.dumps(body).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Accept-Language": accept_language,
        "User-Agent": user_agent,
    }
    if encoded_body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        url,
        data=encoded_body,
        method="POST",
        headers=headers,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise CloudFetchError(f"{path} returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise CloudFetchError(f"{path} failed: {exc.reason}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise CloudFetchError(f"{path} did not return JSON.") from exc


def extract_login_token(payload):
    code = base_response_code(payload)
    if isinstance(payload, dict) and code not in (None, 0, 200):
        raise CloudFetchError(f"login failed: {base_response_message(payload) or code}")

    data = base_response_data(payload) if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        data = payload if isinstance(payload, dict) else {}

    token = normalize_token(dict_get_ci(data, "authorization", "token", "accessToken", "access_token") or "")
    if not token:
        raise CloudFetchError("login response did not contain an authorization token.")
    return token


def login_for_token(args, username, password):
    payload = fetch_json(
        resolve_base_url(args),
        LOGIN_PATH,
        body={
            "username": username,
            "password": password,
            "userOrigin": args.user_origin,
        },
        timeout=args.timeout,
        user_agent=args.user_agent,
        accept_language=args.accept_language,
    )
    return extract_login_token(payload)


def mesh_fingerprint(storage):
    return json.dumps(storage, sort_keys=True, separators=(",", ":"))


def collect_mesh_candidates(responses):
    candidates = []
    seen = set()
    for endpoint_name, payload in responses:
        for path, storage in find_mesh_storage_candidates(payload, endpoint_name):
            fingerprint = mesh_fingerprint(storage)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            candidates.append(
                {
                    "source": f"cloud:{endpoint_name}",
                    "location": path,
                    "storage": storage,
                }
            )
    return candidates


def collect_home_ids(payload):
    return [entry["home_id"] for entry in collect_home_entries(payload)]


def normalize_home_field_name(value):
    return str(value).replace("_", "").lower()


def clean_home_report_value(value):
    if value is None:
        return None
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        return text or None
    return None


def collect_home_entries(payload, source=None):
    homes = []
    seen = set()

    def append_home(value, path):
        home_id = None
        for key, child in value.items():
            if normalize_home_field_name(key) in HOME_ID_FIELD_NAMES:
                home_id = clean_home_report_value(child)
                if home_id:
                    break
        if not home_id or home_id in seen:
            return

        seen.add(home_id)
        entry = {"home_id": home_id, "path": path}
        if source:
            entry["source"] = source
        for key, child in value.items():
            if normalize_home_field_name(key) in HOME_NAME_FIELD_NAMES:
                name = clean_home_report_value(child)
                if name:
                    entry["name"] = name
                    break
        homes.append(entry)

    def visit(value, path="$"):
        if isinstance(value, dict):
            append_home(value, path)
            for key, child in value.items():
                visit(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")

    visit(payload)
    return homes


def response_home_entries(responses):
    homes = []
    seen = set()
    for endpoint_name, payload in responses:
        for entry in collect_home_entries(payload, endpoint_name):
            home_id = entry["home_id"]
            if home_id not in seen:
                seen.add(home_id)
                homes.append(entry)
    return homes


def response_home_ids(responses):
    return [entry["home_id"] for entry in response_home_entries(responses)]


def requested_home_ids(args):
    home_ids = []
    seen = set()
    for value in getattr(args, "home_id", None) or []:
        home_id = clean_home_report_value(value)
        if home_id and home_id not in seen:
            seen.add(home_id)
            home_ids.append(home_id)
    return home_ids


def write_raw_response(path, responses):
    if not path:
        return
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({name: payload for name, payload in responses}, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote raw cloud response JSON to {output}")


def candidate_report_entry(candidate, index):
    storage = candidate["storage"]
    return {
        "index": index,
        "source": str(candidate["source"]),
        "location": candidate["location"],
        "net_key_count": len(storage.get("netKeys") or []),
        "app_key_count": len(storage.get("appKeys") or []),
        "node_count": len(storage.get("nodes") or []),
        "summary": summarize_candidate(candidate, index).splitlines(),
    }


def safe_error_text(error):
    text = str(error)
    if any(marker in text.lower() for marker in SENSITIVE_ERROR_MARKERS):
        return "<redacted sensitive cloud error>"
    return text


def safe_endpoint_error(endpoint, error):
    return {
        "endpoint": str(endpoint),
        "error": safe_error_text(error),
    }


def join_endpoint_errors(endpoint_errors):
    return "; ".join(f"{entry['endpoint']}: {entry['error']}" for entry in endpoint_errors)


def write_cloud_report(
    path,
    args,
    *,
    status,
    base_url=None,
    endpoints=None,
    candidates=None,
    selected_index=None,
    error=None,
    home_entries=None,
    endpoint_errors=None,
):
    if not path:
        return
    candidates = candidates or []
    home_entries = home_entries or []
    endpoint_errors = endpoint_errors or []
    report = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "status": status,
        "region": getattr(args, "region", DEFAULT_REGION),
        "base_url": base_url,
        "endpoints": list(endpoints or []),
        "endpoint_error_count": len(endpoint_errors),
        "endpoint_errors": endpoint_errors,
        "requested_home_ids": requested_home_ids(args),
        "home_count": len(home_entries),
        "homes": home_entries,
        "candidate_count": len(candidates),
        "selected_candidate": selected_index,
        "output": getattr(args, "output", None),
        "raw_output": getattr(args, "raw_output", None),
        "candidates": [candidate_report_entry(candidate, index) for index, candidate in enumerate(candidates, start=1)],
    }
    if error:
        report["error"] = safe_error_text(error)

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote key-free cloud fetch report to {output}")


def safe_report_base_url(args):
    try:
        return resolve_base_url(args)
    except CloudFetchError:
        return getattr(args, "base_url", None)


def safe_write_cloud_report(path, args, **kwargs):
    try:
        write_cloud_report(path, args, **kwargs)
    except OSError as exc:
        print(f"Cloud fetch report failed: {exc}", file=sys.stderr)


def fetch_cloud_mesh(args):
    try:
        token = read_token(args)
        if not token:
            username, password = read_login_credentials(args)
            if username:
                token = login_for_token(args, username, password)
        if not token:
            message = (
                f"Cloud fetch failed: provide --token, --token-file, ${args.token_env}, "
                f"or login credentials via ${args.username_env}/${args.password_env}."
            )
            safe_write_cloud_report(
                getattr(args, "report_output", None),
                args,
                status="credentials-missing",
                base_url=safe_report_base_url(args),
                endpoints=args.endpoint or list(ENDPOINTS),
                error=message,
            )
            print(message, file=sys.stderr)
            return 2
    except (OSError, CloudFetchError) as exc:
        safe_write_cloud_report(
            getattr(args, "report_output", None),
            args,
            status="credential-or-login-failed",
            base_url=safe_report_base_url(args),
            endpoints=args.endpoint or list(ENDPOINTS),
            error=exc,
        )
        print(f"Cloud fetch failed: {exc}", file=sys.stderr)
        return 2

    try:
        base_url = resolve_base_url(args)
    except CloudFetchError as exc:
        safe_write_cloud_report(
            getattr(args, "report_output", None),
            args,
            status="region-resolution-failed",
            endpoints=args.endpoint or list(ENDPOINTS),
            error=exc,
        )
        print(f"Cloud fetch failed: {exc}", file=sys.stderr)
        return 2

    endpoint_names = args.endpoint or DEFAULT_ENDPOINTS
    responses = []
    endpoint_errors = []
    for endpoint_name in endpoint_names:
        path = ENDPOINTS[endpoint_name]
        if endpoint_name == HOME_MESH_ENDPOINT:
            home_ids = requested_home_ids(args) or response_home_ids(responses)
            if not home_ids:
                message = (
                    f"{HOME_MESH_ENDPOINT} needs --home-id or a previous home-list response "
                    "containing homeId."
                )
                endpoint_errors.append(safe_endpoint_error(endpoint_name, message))
                continue

            for home_id in home_ids:
                endpoint_label = f"{endpoint_name}:{home_id}"
                try:
                    payload = fetch_json(
                        base_url,
                        path,
                        token,
                        body={"homeId": home_id},
                        timeout=args.timeout,
                        user_agent=args.user_agent,
                        accept_language=args.accept_language,
                    )
                except CloudFetchError as exc:
                    endpoint_errors.append(safe_endpoint_error(endpoint_label, exc))
                    print(f"Cloud fetch warning: {endpoint_label}: {safe_error_text(exc)}", file=sys.stderr)
                    continue
                responses.append((endpoint_label, payload))
            continue

        try:
            payload = fetch_json(
                base_url,
                path,
                token,
                timeout=args.timeout,
                user_agent=args.user_agent,
                accept_language=args.accept_language,
            )
        except CloudFetchError as exc:
            endpoint_errors.append(safe_endpoint_error(endpoint_name, exc))
            print(f"Cloud fetch warning: {endpoint_name}: {safe_error_text(exc)}", file=sys.stderr)
            continue
        responses.append((endpoint_name, payload))

    write_raw_response(args.raw_output, responses)
    candidates = collect_mesh_candidates(responses)
    if args.list or args.output:
        print_candidates(candidates)
    if not candidates and endpoint_errors:
        error_text = join_endpoint_errors(endpoint_errors)
        safe_write_cloud_report(
            getattr(args, "report_output", None),
            args,
            status="endpoint-fetch-failed",
            base_url=base_url,
            endpoints=endpoint_names,
            candidates=candidates,
            home_entries=response_home_entries(responses),
            endpoint_errors=endpoint_errors,
            error=error_text,
        )
        print(f"Cloud fetch failed: {error_text}", file=sys.stderr)
        return 1
    if not args.output:
        safe_write_cloud_report(
            getattr(args, "report_output", None),
            args,
            status="candidates-found" if candidates else "no-candidates",
            base_url=base_url,
            endpoints=endpoint_names,
            candidates=candidates,
            home_entries=response_home_entries(responses),
            endpoint_errors=endpoint_errors,
        )
        return 0 if candidates else 1

    try:
        candidate = select_candidate(candidates, args.candidate)
    except ImportErrorWithDetail as exc:
        safe_write_cloud_report(
            getattr(args, "report_output", None),
            args,
            status="candidate-selection-failed",
            base_url=base_url,
            endpoints=endpoint_names,
            candidates=candidates,
            home_entries=response_home_entries(responses),
            endpoint_errors=endpoint_errors,
            error=exc,
        )
        print(f"Cloud fetch failed: {exc}", file=sys.stderr)
        return 2

    write_storage(args.output, candidate["storage"])
    selected_index = candidates.index(candidate) + 1
    safe_write_cloud_report(
        getattr(args, "report_output", None),
        args,
        status="written",
        base_url=base_url,
        endpoints=endpoint_names,
        candidates=candidates,
        selected_index=selected_index,
        home_entries=response_home_entries(responses),
        endpoint_errors=endpoint_errors,
    )
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Fetch Pesetech/Lepu cloud home mesh JSON with a captured bearer token."
    )
    parser.add_argument("-o", "--output", help="Write normalized mesh JSON to this path.")
    parser.add_argument("--candidate", type=int, help="1-based candidate number to write when multiple meshes are found.")
    parser.add_argument("--list", action="store_true", help="List cloud mesh candidates.")
    parser.add_argument("--raw-output", help="Write raw endpoint responses to this JSON file.")
    parser.add_argument("--report-output", help="Write a key-free cloud fetch report JSON to this path.")
    parser.add_argument("--region", choices=sorted(REGION_BASE_URLS), default=DEFAULT_REGION, help="Official app cloud region. Europe uses service.lepuiot.com; Asia uses test.lepuiot.com.")
    parser.add_argument("--base-url", default=None, help="Override the cloud base URL, mainly for captures/tests.")
    parser.add_argument("--endpoint", action="append", choices=sorted(ENDPOINTS), help="Endpoint to fetch. Default: home-list, mesh-json-by-home-id, and sync-data.")
    parser.add_argument("--home-id", action="append", help="Home ID to query with mesh-json-by-home-id; can be passed more than once.")
    parser.add_argument("--token", help="Captured TokenUtils token or full Authorization bearer value.")
    parser.add_argument("--token-file", help="File containing the captured token.")
    parser.add_argument("--token-env", default="PESETECH_CLOUD_TOKEN", help="Environment variable containing the token.")
    parser.add_argument("--username", help="Pesetech account username. Prefer --username-file or --username-env.")
    parser.add_argument("--username-file", help="File containing the Pesetech account username.")
    parser.add_argument("--username-env", default="PESETECH_CLOUD_USERNAME", help="Environment variable containing the username.")
    parser.add_argument("--password", help="Pesetech account password. Prefer --password-file or --password-env.")
    parser.add_argument("--password-file", help="File containing the Pesetech account password.")
    parser.add_argument("--password-env", default="PESETECH_CLOUD_PASSWORD", help="Environment variable containing the password.")
    parser.add_argument("--user-origin", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--accept-language", default="en")
    return fetch_cloud_mesh(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
