#!/usr/bin/env python3
import argparse
import json
import os
import queue
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pesetech_mqtt_smoke import (
    DEFAULT_CONFIG,
    apply_config_defaults as apply_mqtt_config_defaults,
    clean_optional,
    connect_client as connect_mqtt_client,
    configured_device_id,
    default_run_id,
    load_config,
    state_topic as mqtt_state_topic,
    subscribe_and_wait as mqtt_subscribe_and_wait,
)


DEFAULT_HA_URL = "http://homeassistant.local:8123"
DEFAULT_ENTITY_ID = "light.skylight"
DEFAULT_BRIGHTNESS = 192
DEFAULT_MQTT_BRIGHTNESS_SCALE = 65280
DEFAULT_MQTT_BRIGHTNESS_TOLERANCE = 2
DEFAULT_MQTT_MIRED_TOLERANCE = 2


class HomeAssistantError(Exception):
    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


def normalize_base_url(url):
    return str(url).rstrip("/")


def api_path(path):
    if not path.startswith("/"):
        path = "/" + path
    return "/api" + path


def state_path(entity_id):
    return api_path("/states/" + urllib.parse.quote(entity_id, safe=""))


def service_path(domain, service):
    return api_path(f"/services/{domain}/{service}")


def smoke_steps(entity_id, brightness, warm_kelvin, cool_kelvin):
    return [
        {
            "name": "on",
            "description": "turned on through Home Assistant",
            "service": "turn_on",
            "payload": {"entity_id": entity_id},
            "expected_state": {"state": "on"},
        },
        {
            "name": "brightness",
            "description": "changed brightness through Home Assistant",
            "service": "turn_on",
            "payload": {"entity_id": entity_id, "brightness": brightness},
            "expected_state": {"state": "on", "attributes": {"brightness": brightness}},
        },
        {
            "name": "warm",
            "description": "moved warm through Home Assistant",
            "service": "turn_on",
            "payload": {"entity_id": entity_id, "color_temp_kelvin": warm_kelvin},
            "expected_state": {"state": "on", "attributes": {"color_temp_kelvin": warm_kelvin}},
        },
        {
            "name": "cool",
            "description": "moved cool through Home Assistant",
            "service": "turn_on",
            "payload": {"entity_id": entity_id, "color_temp_kelvin": cool_kelvin},
            "expected_state": {"state": "on", "attributes": {"color_temp_kelvin": cool_kelvin}},
        },
        {
            "name": "off",
            "description": "turned off through Home Assistant",
            "service": "turn_off",
            "payload": {"entity_id": entity_id},
            "expected_state": {"state": "off"},
        },
    ]


def baseline_brightness(brightness):
    brightness = int(brightness)
    if brightness <= 1:
        return brightness
    return max(1, brightness // 4)


def precondition_steps(entity_id, brightness, cool_kelvin):
    baseline = baseline_brightness(brightness)
    return [
        {
            "name": "precondition_baseline",
            "description": "set to a dim cool baseline through Home Assistant before proof",
            "service": "turn_on",
            "payload": {
                "entity_id": entity_id,
                "brightness": baseline,
                "color_temp_kelvin": cool_kelvin,
            },
            "expected_state": {
                "state": "on",
                "attributes": {
                    "brightness": baseline,
                    "color_temp_kelvin": cool_kelvin,
                },
            },
        },
        {
            "name": "precondition_off",
            "description": "turned off through Home Assistant before proof",
            "service": "turn_off",
            "payload": {"entity_id": entity_id},
            "expected_state": {"state": "off"},
        },
    ]


def resolve_token_with_source(args):
    if args.token:
        return args.token.strip(), "argument"
    if args.token_file:
        return Path(args.token_file).read_text(encoding="utf-8").strip(), "token_file"
    for name in ("HOME_ASSISTANT_TOKEN", "HA_TOKEN", "SUPERVISOR_TOKEN"):
        token = (os.environ.get(name) or "").strip()
        if token:
            return token, name
    return "", "missing"


def resolve_token(args):
    token, _source = resolve_token_with_source(args)
    return token


class HomeAssistantClient:
    def __init__(self, base_url, token, timeout=10.0):
        self.base_url = normalize_base_url(base_url)
        self.token = token
        self.timeout = timeout

    def request(self, method, path, payload=None):
        data = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
                status = response.getcode()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise HomeAssistantError(
                f"Home Assistant API returned HTTP {exc.code} for {method} {path}",
                status=exc.code,
                body=body,
            ) from exc
        except urllib.error.URLError as exc:
            raise HomeAssistantError(f"Could not reach Home Assistant at {self.base_url}: {exc.reason}") from exc

        if not body:
            return status, None

        try:
            return status, json.loads(body)
        except json.JSONDecodeError as exc:
            raise HomeAssistantError(f"Home Assistant returned non-JSON data for {method} {path}", status=status, body=body) from exc

    def get_state(self, entity_id):
        status, payload = self.request("GET", state_path(entity_id))
        return payload

    def get_config(self):
        status, payload = self.request("GET", api_path("/config"))
        return payload

    def list_states(self):
        status, payload = self.request("GET", api_path("/states"))
        return payload if isinstance(payload, list) else []

    def call_light_service(self, service, payload):
        status, response = self.request("POST", service_path("light", service), payload)
        return {"status": status, "response": response}


def compact_state(state):
    if not isinstance(state, dict):
        return state

    attributes = state.get("attributes")
    compact = {
        "entity_id": state.get("entity_id"),
        "state": state.get("state"),
    }
    if isinstance(attributes, dict):
        compact["attributes"] = {
            key: attributes.get(key)
            for key in (
                "brightness",
                "color_mode",
                "color_temp",
                "color_temp_kelvin",
                "friendly_name",
            )
            if key in attributes
        }
    return compact


def values_match(actual, expected, tolerance):
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return abs(actual - expected) <= tolerance
    return actual == expected


def state_matches(state, expected, *, wait_attributes=False, brightness_tolerance=2, kelvin_tolerance=150):
    if not isinstance(state, dict):
        return False
    if state.get("state") != expected.get("state"):
        return False
    if not wait_attributes:
        return True

    expected_attributes = expected.get("attributes") or {}
    attributes = state.get("attributes") or {}
    for key, expected_value in expected_attributes.items():
        tolerance = kelvin_tolerance if key == "color_temp_kelvin" else brightness_tolerance
        if not values_match(attributes.get(key), expected_value, tolerance):
            return False
    return True


def wait_for_state(client, entity_id, expected, timeout, poll_interval, **match_options):
    deadline = time.monotonic() + timeout
    seen = []

    while True:
        state = client.get_state(entity_id)
        compact = compact_state(state)
        seen.append(compact)
        if state_matches(state, expected, **match_options):
            return compact, seen
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_interval)

    return None, seen[-5:]


def candidate_lights(states, search):
    search = (search or "").lower()
    candidates = []
    for state in states:
        entity_id = state.get("entity_id", "")
        if not entity_id.startswith("light."):
            continue
        attributes = state.get("attributes") or {}
        friendly_name = str(attributes.get("friendly_name", ""))
        haystack = f"{entity_id} {friendly_name}".lower()
        if not search or search in haystack or "skylight" in haystack or "pesetech" in haystack:
            candidates.append({"entity_id": entity_id, "friendly_name": friendly_name, "state": state.get("state")})
    return candidates


def observe_step(step):
    answer = input(f"Did the real skylight {step['description']}? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def write_proof_event(path, event):
    if not path:
        return
    with open(path, "a", encoding="utf-8") as proof_file:
        proof_file.write(json.dumps(event, sort_keys=True) + "\n")


def mqtt_args_from(args):
    return SimpleNamespace(
        config=args.mqtt_config,
        broker=args.mqtt_broker,
        port=args.mqtt_port,
        username=args.mqtt_username,
        password=args.mqtt_password,
        discovery_prefix=args.mqtt_discovery_prefix,
        component="light",
        mesh_topic=args.mqtt_mesh_topic,
        device_id=args.mqtt_device_id,
        mqtt_timeout=args.mqtt_timeout,
    )


def step_name(step):
    return step.get("name") or step.get("step")


def expected_mqtt_state(step):
    return {"state": "OFF" if step["service"] == "turn_off" else "ON"}


def required_mqtt_fields(step):
    name = step_name(step)
    if name == "brightness":
        return ["brightness"]
    if name == "precondition_baseline":
        return ["brightness", "color_temp"]
    if name in {"warm", "cool"}:
        return ["color_temp"]
    return []


def kelvin_to_mired(kelvin):
    return int(round(1000000 / int(kelvin)))


def ha_brightness_to_mqtt_scale(brightness, brightness_scale=DEFAULT_MQTT_BRIGHTNESS_SCALE):
    return int(round(int(brightness) * int(brightness_scale) / 255))


def expected_mqtt_attributes(step, brightness_scale=DEFAULT_MQTT_BRIGHTNESS_SCALE):
    name = step_name(step)
    payload = step.get("payload") or {}
    if name == "brightness":
        return {
            "brightness": ha_brightness_to_mqtt_scale(
                payload["brightness"],
                brightness_scale,
            )
        }
    if name == "precondition_baseline":
        return {
            "brightness": ha_brightness_to_mqtt_scale(
                payload["brightness"],
                brightness_scale,
            ),
            "color_temp": kelvin_to_mired(payload["color_temp_kelvin"]),
        }
    if name in {"warm", "cool"}:
        return {"color_temp": kelvin_to_mired(payload["color_temp_kelvin"])}
    return {}


def configured_mqtt_brightness_scale(args):
    if getattr(args, "mqtt_brightness_scale", None) is not None:
        return int(args.mqtt_brightness_scale)

    config = load_config(getattr(args, "mqtt_config", None))
    mesh = config.get("mesh") or {}
    try:
        device_id = configured_device_id(mesh, clean_optional(getattr(args, "mqtt_device_id", None)))
    except SystemExit:
        return DEFAULT_MQTT_BRIGHTNESS_SCALE

    node = mesh.get(device_id)
    if isinstance(node, dict):
        value = clean_optional(node.get("brightness_scale"))
        if value is not None:
            return int(value)

    return DEFAULT_MQTT_BRIGHTNESS_SCALE


def mqtt_attribute_matches(actual, expected, field, *, brightness_tolerance, mired_tolerance):
    tolerance = brightness_tolerance if field == "brightness" else mired_tolerance
    return values_match(actual, expected, tolerance)


def mqtt_state_matches_step(
    message,
    step,
    require_attributes=False,
    expected_attributes=None,
    brightness_tolerance=DEFAULT_MQTT_BRIGHTNESS_TOLERANCE,
    mired_tolerance=DEFAULT_MQTT_MIRED_TOLERANCE,
):
    if not isinstance(message, dict):
        return False
    expected = expected_mqtt_state(step)
    if any(message.get(key) != value for key, value in expected.items()):
        return False
    if require_attributes:
        if not all(field in message for field in required_mqtt_fields(step)):
            return False
        for field, expected_value in (expected_attributes or {}).items():
            if not mqtt_attribute_matches(
                message.get(field),
                expected_value,
                field,
                brightness_tolerance=brightness_tolerance,
                mired_tolerance=mired_tolerance,
            ):
                return False
    return True


def wait_for_mqtt_step_state(
    messages,
    step,
    timeout,
    require_attributes=False,
    expected_attributes=None,
    brightness_tolerance=DEFAULT_MQTT_BRIGHTNESS_TOLERANCE,
    mired_tolerance=DEFAULT_MQTT_MIRED_TOLERANCE,
):
    deadline = time.monotonic() + timeout
    seen = []

    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            message = messages.get(timeout=min(0.25, remaining))
        except queue.Empty:
            continue

        seen.append(message)
        if mqtt_state_matches_step(
            message,
            step,
            require_attributes=require_attributes,
            expected_attributes=expected_attributes,
            brightness_tolerance=brightness_tolerance,
            mired_tolerance=mired_tolerance,
        ):
            return message, seen

    return None, seen


def drain_queue(messages):
    while not messages.empty():
        try:
            messages.get_nowait()
        except queue.Empty:
            return


def run_precondition_step(args, client, mqtt_runtime, mqtt_messages, step, mqtt_brightness_scale):
    endpoint = service_path("light", step["service"])
    print(f"{step['name']}: POST {endpoint} {json.dumps(step['payload'], separators=(',', ':'))}")
    failures = []

    if mqtt_runtime is not None:
        drain_queue(mqtt_messages)

    response_error = None
    try:
        if client is not None:
            client.call_light_service(step["service"], step["payload"])
    except Exception as exc:
        response_error = str(exc)
        failures.append(f"{step['name']}: Home Assistant setup service call failed: {exc}")
        print(f"  service: error={exc}")

    if response_error is None and args.wait_state and client is not None:
        matched_state, seen_states = wait_for_state(
            client,
            args.entity_id,
            step["expected_state"],
            args.state_timeout,
            args.poll_interval,
            wait_attributes=args.wait_attributes,
            brightness_tolerance=args.brightness_tolerance,
            kelvin_tolerance=args.kelvin_tolerance,
        )
        if matched_state is None:
            print(f"  state: no setup match within {args.state_timeout:g}s; seen={seen_states}")
            failures.append(f"{step['name']}: missing matching Home Assistant setup state")
        else:
            print(f"  state: {json.dumps(matched_state, separators=(',', ':'))}")

    if response_error is None and mqtt_runtime is not None:
        expected_attributes = expected_mqtt_attributes(step, mqtt_brightness_scale)
        matched_mqtt_state, seen_mqtt_states = wait_for_mqtt_step_state(
            mqtt_messages,
            step,
            args.mqtt_state_timeout,
            require_attributes=args.wait_mqtt_attributes,
            expected_attributes=expected_attributes,
            brightness_tolerance=args.mqtt_brightness_tolerance,
            mired_tolerance=args.mqtt_mired_tolerance,
        )
        if matched_mqtt_state is None:
            print(f"  mqtt_state: no setup match within {args.mqtt_state_timeout:g}s; seen={seen_mqtt_states}")
            failures.append(f"{step['name']}: missing matching MQTT setup state")
        else:
            print(f"  mqtt_state: {json.dumps(matched_mqtt_state, separators=(',', ':'))}")

    return failures


def precondition_visible_start(args, client, mqtt_runtime, mqtt_messages, mqtt_brightness_scale):
    print("precondition: setting a dim cool baseline, then turning off before proof prompts")
    failures = []
    for step in precondition_steps(args.entity_id, args.brightness, args.cool_kelvin):
        failures.extend(run_precondition_step(args, client, mqtt_runtime, mqtt_messages, step, mqtt_brightness_scale))
        time.sleep(args.delay)
    return failures


def run_sequence(args, client=None):
    if args.wait_mqtt_attributes:
        args.wait_mqtt_state = True
    run_id = getattr(args, "run_id", None) or default_run_id("ha")

    token, auth_source = (None, "dry_run") if args.dry_run else resolve_token_with_source(args)
    if not args.dry_run and not token:
        raise SystemExit(
            "Home Assistant token missing; pass --token, --token-file, HOME_ASSISTANT_TOKEN, or run inside a Home Assistant app with SUPERVISOR_TOKEN."
        )

    client = client or (None if args.dry_run else HomeAssistantClient(args.url, token, timeout=args.http_timeout))
    failed_steps = []
    mqtt_runtime = None
    mqtt_messages = queue.Queue()
    mqtt_topic = None
    mqtt_args = None
    mqtt_brightness_scale = configured_mqtt_brightness_scale(args)

    if args.wait_mqtt_state and not args.dry_run:
        mqtt_args = apply_mqtt_config_defaults(mqtt_args_from(args))
        mqtt_topic = mqtt_state_topic(
            mqtt_args.discovery_prefix,
            mqtt_args.component,
            mqtt_args.mesh_topic,
            mqtt_args.device_id,
        )
        mqtt_runtime = connect_mqtt_client(mqtt_args, mqtt_messages)
        mqtt_subscribe_and_wait(mqtt_runtime, mqtt_topic, args.mqtt_qos, args.mqtt_timeout)

    print(f"home_assistant: {normalize_base_url(args.url)}")
    print(f"auth_source:    {auth_source}")
    print(f"entity:         {args.entity_id}")
    if mqtt_topic:
        print(f"mqtt_state:     {mqtt_topic}")
    if args.proof_log:
        print(f"proof:          {args.proof_log}")
        print(f"run_id:         {run_id}")

    if args.list_candidates and client is not None:
        candidates = candidate_lights(client.list_states(), args.candidate_search)
        print("candidate lights:")
        for candidate in candidates:
            label = f" ({candidate['friendly_name']})" if candidate.get("friendly_name") else ""
            print(f"  - {candidate['entity_id']}{label}: {candidate['state']}")
        return 0

    if client is not None:
        try:
            initial_state = client.get_state(args.entity_id)
        except HomeAssistantError as exc:
            if exc.status == 404:
                candidates = candidate_lights(client.list_states(), args.candidate_search)
                print(f"{args.entity_id} was not found. Candidate light entities:")
                for candidate in candidates:
                    label = f" ({candidate['friendly_name']})" if candidate.get("friendly_name") else ""
                    print(f"  - {candidate['entity_id']}{label}: {candidate['state']}")
            raise
        else:
            print(f"initial_state:  {json.dumps(compact_state(initial_state), separators=(',', ':'))}")

    try:
        precondition_enabled = getattr(args, "precondition_visible_start", False)
        if precondition_enabled and not args.dry_run:
            failed_steps.extend(
                precondition_visible_start(
                    args,
                    client,
                    mqtt_runtime,
                    mqtt_messages,
                    mqtt_brightness_scale,
                )
            )
            if failed_steps:
                print("\nHome Assistant service smoke test setup failed:")
                for failed_step in failed_steps:
                    print(f"  - {failed_step}")
                return 1

        for step in smoke_steps(args.entity_id, args.brightness, args.warm_kelvin, args.cool_kelvin):
            response = None
            response_error = None
            matched_state = None
            seen_states = []
            state_elapsed_ms = None
            expected_bridge_state = expected_mqtt_state(step)
            expected_bridge_attributes = expected_mqtt_attributes(step, mqtt_brightness_scale)
            required_bridge_fields = required_mqtt_fields(step) if args.wait_mqtt_attributes else []
            matched_mqtt_state = None
            seen_mqtt_states = []
            mqtt_state_elapsed_ms = None
            observed = None
            endpoint = service_path("light", step["service"])

            if mqtt_runtime is not None:
                drain_queue(mqtt_messages)

            print(f"{step['name']}: POST {endpoint} {json.dumps(step['payload'], separators=(',', ':'))}")
            if client is not None:
                try:
                    response = client.call_light_service(step["service"], step["payload"])
                except Exception as exc:
                    response_error = str(exc)
                    failed_steps.append(f"{step['name']}: Home Assistant service call failed: {exc}")

            if args.wait_state and client is not None and response_error is None:
                state_start = time.monotonic()
                matched_state, seen_states = wait_for_state(
                    client,
                    args.entity_id,
                    step["expected_state"],
                    args.state_timeout,
                    args.poll_interval,
                    wait_attributes=args.wait_attributes,
                    brightness_tolerance=args.brightness_tolerance,
                    kelvin_tolerance=args.kelvin_tolerance,
                )
                state_elapsed_ms = round((time.monotonic() - state_start) * 1000)
                if matched_state is None:
                    print(f"  state: no match within {args.state_timeout:g}s; seen={seen_states}")
                    failed_steps.append(f"{step['name']}: missing matching Home Assistant state")
                else:
                    print(f"  state: {json.dumps(matched_state, separators=(',', ':'))} ({state_elapsed_ms} ms)")

            if mqtt_runtime is not None and response_error is None:
                mqtt_start = time.monotonic()
                matched_mqtt_state, seen_mqtt_states = wait_for_mqtt_step_state(
                    mqtt_messages,
                    step,
                    args.mqtt_state_timeout,
                    require_attributes=args.wait_mqtt_attributes,
                    expected_attributes=expected_bridge_attributes,
                    brightness_tolerance=args.mqtt_brightness_tolerance,
                    mired_tolerance=args.mqtt_mired_tolerance,
                )
                mqtt_state_elapsed_ms = round((time.monotonic() - mqtt_start) * 1000)
                if matched_mqtt_state is None:
                    print(f"  mqtt_state: no match within {args.mqtt_state_timeout:g}s; seen={seen_mqtt_states}")
                    failed_steps.append(f"{step['name']}: missing matching MQTT bridge state")
                else:
                    print(f"  mqtt_state: {json.dumps(matched_mqtt_state, separators=(',', ':'))} ({mqtt_state_elapsed_ms} ms)")

            if args.observe and not args.dry_run:
                observed = observe_step(step)
                print(f"  observed: {'yes' if observed else 'no'}")
                if observed is not True:
                    failed_steps.append(f"{step['name']}: real-light observation was not confirmed")

            write_proof_event(
                args.proof_log,
                {
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "run_id": run_id,
                    "step": step["name"],
                    "home_assistant_url": normalize_base_url(args.url),
                    "auth_source": auth_source,
                    "entity_id": args.entity_id,
                    "service": f"light.{step['service']}",
                    "service_path": endpoint,
                    "payload": step["payload"],
                    "expected_state": step["expected_state"],
                    "response": response,
                    "response_error": response_error,
                    "matched_state": matched_state,
                    "seen_states": seen_states,
                    "state_elapsed_ms": state_elapsed_ms,
                    "mqtt_state_topic": mqtt_topic,
                    "expected_mqtt_state": expected_bridge_state,
                    "expected_mqtt_attributes": expected_bridge_attributes,
                    "mqtt_brightness_scale": mqtt_brightness_scale,
                    "required_mqtt_fields": required_bridge_fields,
                    "matched_mqtt_state": matched_mqtt_state,
                    "seen_mqtt_states": seen_mqtt_states,
                    "mqtt_state_elapsed_ms": mqtt_state_elapsed_ms,
                    "observed": observed,
                    "precondition_visible_start": precondition_enabled,
                },
            )

            time.sleep(args.delay)

        if failed_steps:
            print("\nHome Assistant service smoke test failed:")
            for failed_step in failed_steps:
                print(f"  - {failed_step}")
            return 1

        return 0
    finally:
        if mqtt_runtime is not None:
            mqtt_runtime.client.loop_stop()
            mqtt_runtime.client.disconnect()


def check_api(args, client=None):
    token, auth_source = (None, "dry_run") if args.dry_run else resolve_token_with_source(args)
    if not args.dry_run and not token:
        raise SystemExit(
            "Home Assistant token missing; pass --token, --token-file, HOME_ASSISTANT_TOKEN, or run inside a Home Assistant app with SUPERVISOR_TOKEN."
        )

    print(f"home_assistant: {normalize_base_url(args.url)}")
    print(f"auth_source:    {auth_source}")
    print("api_check:      GET /api/config")
    if args.dry_run:
        return 0

    client = client or HomeAssistantClient(args.url, token, timeout=args.http_timeout)
    config = client.get_config()
    if isinstance(config, dict):
        version = config.get("version")
        location_name = config.get("location_name")
        if version:
            print(f"version:        {version}")
        if location_name:
            print(f"location:       {location_name}")
    print("Home Assistant API check passed.")
    return 0


def print_candidate_lights(client, search):
    candidates = candidate_lights(client.list_states(), search)
    print("candidate lights:")
    for candidate in candidates:
        label = f" ({candidate['friendly_name']})" if candidate.get("friendly_name") else ""
        print(f"  - {candidate['entity_id']}{label}: {candidate['state']}")


def check_entity(args, client=None):
    token, auth_source = (None, "dry_run") if args.dry_run else resolve_token_with_source(args)
    if not args.dry_run and not token:
        raise SystemExit(
            "Home Assistant token missing; pass --token, --token-file, HOME_ASSISTANT_TOKEN, or run inside a Home Assistant app with SUPERVISOR_TOKEN."
        )

    print(f"home_assistant: {normalize_base_url(args.url)}")
    print(f"auth_source:    {auth_source}")
    print(f"entity_check:   GET {state_path(args.entity_id)}")
    entity_timeout = float(getattr(args, "entity_timeout", 0.0) or 0.0)
    if entity_timeout > 0:
        print(f"entity_wait:    up to {entity_timeout:g}s")
    if args.dry_run:
        return 0

    if not str(args.entity_id).startswith("light."):
        print(f"{args.entity_id} is not a Home Assistant light entity id.", file=sys.stderr)
        return 1

    client = client or HomeAssistantClient(args.url, token, timeout=args.http_timeout)
    deadline = time.monotonic() + max(0.0, entity_timeout)
    while True:
        try:
            state = client.get_state(args.entity_id)
            break
        except HomeAssistantError as exc:
            if exc.status != 404:
                raise
            if time.monotonic() >= deadline:
                print(f"{args.entity_id} was not found.", file=sys.stderr)
                print_candidate_lights(client, args.candidate_search)
                return 1
            time.sleep(max(0.0, float(getattr(args, "poll_interval", 0.5) or 0.0)))

    compact = compact_state(state)
    print(f"entity_state:   {json.dumps(compact, separators=(',', ':'))}")
    print("Home Assistant entity check passed.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Call Home Assistant light services for a Pesetech skylight proof.")
    parser.add_argument("--url", default=DEFAULT_HA_URL, help="Home Assistant base URL.")
    parser.add_argument("--token", default=None, help="Home Assistant long-lived access token.")
    parser.add_argument("--token-file", default=None, help="File containing a Home Assistant long-lived access token.")
    parser.add_argument("--entity-id", default=DEFAULT_ENTITY_ID, help="Home Assistant light entity id.")
    parser.add_argument("--brightness", type=int, default=DEFAULT_BRIGHTNESS, help="Home Assistant brightness value, 0..255.")
    parser.add_argument("--warm-kelvin", type=int, default=2200, help="Warm color temperature in Kelvin.")
    parser.add_argument("--cool-kelvin", type=int, default=6500, help="Cool color temperature in Kelvin.")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds between commands.")
    parser.add_argument("--http-timeout", type=float, default=10.0, help="Seconds to wait for each Home Assistant HTTP request.")
    parser.add_argument("--wait-state", action="store_true", help="Poll Home Assistant until the entity reaches each expected on/off state.")
    parser.add_argument("--wait-attributes", action="store_true", help="Also wait for matching brightness and color temperature attributes.")
    parser.add_argument("--state-timeout", type=float, default=10.0, help="Seconds to wait for each matching Home Assistant state.")
    parser.add_argument("--entity-timeout", type=float, default=0.0, help="Seconds to wait for --check-entity to appear before listing candidates.")
    parser.add_argument("--poll-interval", type=float, default=0.5, help="Seconds between Home Assistant state polls.")
    parser.add_argument("--brightness-tolerance", type=int, default=2, help="Allowed HA brightness attribute difference.")
    parser.add_argument("--kelvin-tolerance", type=int, default=150, help="Allowed HA color_temp_kelvin attribute difference.")
    parser.add_argument("--observe", action="store_true", help="Prompt for visual confirmation of each real-light change.")
    parser.add_argument("--precondition-visible-start", action="store_true", help="Before proof prompts, set a dim cool baseline and turn off so the proof steps are easier to observe.")
    parser.add_argument("--proof-log", default=None, help="Append JSONL proof events to this file.")
    parser.add_argument("--run-id", default=None, help="Identifier to write into each proof event for this run.")
    parser.add_argument("--wait-mqtt-state", action="store_true", help="Also wait for matching MQTT bridge state after each HA service call.")
    parser.add_argument("--wait-mqtt-attributes", action="store_true", help="When waiting for MQTT bridge state, require brightness/color_temp fields on brightness/CCT steps.")
    parser.add_argument("--mqtt-config", default=DEFAULT_CONFIG, help="Gateway config path used for MQTT defaults.")
    parser.add_argument("--mqtt-broker", default=None, help="MQTT broker hostname or IP.")
    parser.add_argument("--mqtt-port", type=int, default=None, help="MQTT broker port.")
    parser.add_argument("--mqtt-username", default=None, help="MQTT username.")
    parser.add_argument("--mqtt-password", default=None, help="MQTT password.")
    parser.add_argument("--mqtt-discovery-prefix", default=None, help="MQTT discovery prefix.")
    parser.add_argument("--mqtt-mesh-topic", default=None, help="Bridge MQTT topic/node id.")
    parser.add_argument("--mqtt-device-id", default=None, help="Configured mesh device id.")
    parser.add_argument("--mqtt-qos", type=int, default=0, choices=(0, 1, 2), help="MQTT QoS for state subscription.")
    parser.add_argument("--mqtt-timeout", type=float, default=10.0, help="Seconds to wait for MQTT connect/subscribe.")
    parser.add_argument("--mqtt-state-timeout", type=float, default=5.0, help="Seconds to wait for each matching MQTT bridge state.")
    parser.add_argument("--mqtt-brightness-scale", type=int, default=None, help="Expected MQTT bridge brightness scale; defaults to the Pesetech scale or config override.")
    parser.add_argument("--mqtt-brightness-tolerance", type=int, default=DEFAULT_MQTT_BRIGHTNESS_TOLERANCE, help="Allowed MQTT bridge brightness difference.")
    parser.add_argument("--mqtt-mired-tolerance", type=int, default=DEFAULT_MQTT_MIRED_TOLERANCE, help="Allowed MQTT bridge color_temp mired difference.")
    parser.add_argument("--list-candidates", action="store_true", help="List candidate light entities and exit.")
    parser.add_argument("--candidate-search", default="skylight", help="Search term for --list-candidates and not-found hints.")
    parser.add_argument("--check-api", action="store_true", help="Only verify Home Assistant API reachability and token auth; do not move the light.")
    parser.add_argument("--check-entity", action="store_true", help="Only verify the configured Home Assistant light entity exists; do not move the light.")
    parser.add_argument("--dry-run", action="store_true", help="Print service calls without contacting Home Assistant.")

    try:
        args = parser.parse_args()
        if args.check_api:
            exit_code = check_api(args)
            if exit_code:
                return exit_code
            if args.check_entity:
                return check_entity(args)
            return 0
        if args.check_entity:
            return check_entity(args)
        return run_sequence(args)
    except HomeAssistantError as exc:
        print(str(exc), file=sys.stderr)
        if exc.body:
            print(exc.body, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
