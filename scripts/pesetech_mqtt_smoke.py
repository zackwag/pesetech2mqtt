#!/usr/bin/env python3
import argparse
import json
import sys
import queue
import threading
import time
from pathlib import Path
from uuid import uuid4


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pesetech_preflight import is_placeholder, load_simple_yaml


DEFAULT_CONFIG = "docker/config/config.yaml"
PESETECH_COOL_MIREDS = 100
PESETECH_WARM_MIREDS = 556


def default_run_id(prefix):
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"{prefix}-{timestamp}-{uuid4().hex[:8]}"


def command_topic(discovery_prefix, component, mesh_topic, device_id):
    return f"{discovery_prefix}/{component}/{mesh_topic}/{device_id}/set"


def state_topic(discovery_prefix, component, mesh_topic, device_id):
    return f"{discovery_prefix}/{component}/{mesh_topic}/{device_id}/state"


def clean_optional(value):
    if value is None or value == "" or is_placeholder(value):
        return None
    return value


def configured_device_id(mesh, requested_device_id):
    if requested_device_id:
        return requested_device_id

    candidates = [
        device_id
        for device_id in mesh.keys()
        if not is_placeholder(device_id)
    ]
    if "skylight" in candidates:
        return "skylight"
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        raise SystemExit(
            "Config has multiple mesh devices; pass --device-id for the skylight to smoke-test."
        )
    return "skylight"


def load_config(path):
    if not path:
        return {}

    config_path = Path(path)
    if not config_path.exists():
        return {}

    text = config_path.read_text(encoding="utf-8")
    try:
        import yaml
    except ImportError:
        config = load_simple_yaml(text)
    else:
        if getattr(yaml, "__file__", None):
            config = yaml.safe_load(text) or {}
        else:
            config = load_simple_yaml(text)

    return config if isinstance(config, dict) else {}


def apply_config_defaults(args):
    config = load_config(args.config)
    mqtt = config.get("mqtt") or {}
    mesh = config.get("mesh") or {}

    if args.broker is None:
        args.broker = clean_optional(mqtt.get("broker"))
    if args.port is None:
        args.port = int(clean_optional(mqtt.get("port")) or 1883)
    if args.username is None:
        args.username = clean_optional(mqtt.get("username"))
    if args.password is None:
        args.password = clean_optional(mqtt.get("password"))
    if args.discovery_prefix is None:
        args.discovery_prefix = clean_optional(mqtt.get("discovery_prefix")) or "homeassistant"
    if args.mesh_topic is None:
        args.mesh_topic = clean_optional(mqtt.get("topic")) or clean_optional(mqtt.get("node_id")) or "mqtt_mesh"
    args.device_id = configured_device_id(mesh, clean_optional(args.device_id))

    if not args.broker:
        raise SystemExit(
            f"MQTT broker is not set; pass --broker or set mqtt.broker in {args.config}."
        )
    if is_placeholder(args.broker):
        raise SystemExit(f"mqtt.broker in {args.config} is still a placeholder.")
    if not args.discovery_prefix or is_placeholder(args.discovery_prefix):
        raise SystemExit("MQTT discovery prefix must be a real topic segment.")
    if not args.mesh_topic or is_placeholder(args.mesh_topic):
        raise SystemExit("MQTT mesh topic/node id must be a real topic segment.")
    if not args.device_id or is_placeholder(args.device_id):
        raise SystemExit("Mesh device id must be a real topic segment.")

    return args


def smoke_steps(brightness):
    return [
        {
            "name": "on",
            "description": "turned on",
            "payload": {"state": "ON"},
            "expected_state": {"state": "ON"},
        },
        {
            "name": "brightness",
            "description": "changed brightness",
            "payload": {"state": "ON", "brightness": brightness},
            "expected_state": {"state": "ON", "brightness": brightness},
        },
        {
            "name": "warm",
            "description": "moved to the warm color-temperature end",
            "payload": {"state": "ON", "color_temp": PESETECH_WARM_MIREDS},
            "expected_state": {"state": "ON", "color_temp": PESETECH_WARM_MIREDS},
        },
        {
            "name": "cool",
            "description": "moved to the cool color-temperature end",
            "payload": {"state": "ON", "color_temp": PESETECH_COOL_MIREDS},
            "expected_state": {"state": "ON", "color_temp": PESETECH_COOL_MIREDS},
        },
        {
            "name": "off",
            "description": "turned off",
            "payload": {"state": "OFF"},
            "expected_state": {"state": "OFF"},
        },
    ]


def baseline_brightness(brightness):
    brightness = int(brightness)
    if brightness <= 1:
        return brightness
    return max(1, brightness // 4)


def precondition_steps(brightness):
    baseline = baseline_brightness(brightness)
    return [
        {
            "name": "precondition_baseline",
            "description": "set to a dim cool baseline before proof",
            "payload": {"state": "ON", "brightness": baseline, "color_temp": PESETECH_COOL_MIREDS},
            "expected_state": {"state": "ON", "brightness": baseline, "color_temp": PESETECH_COOL_MIREDS},
        },
        {
            "name": "precondition_off",
            "description": "turned off before proof",
            "payload": {"state": "OFF"},
            "expected_state": {"state": "OFF"},
        },
    ]


def smoke_payloads(brightness):
    return [step["payload"] for step in smoke_steps(brightness)]


def matches_expected_state(message, expected):
    if not isinstance(message, dict):
        return False

    for key, value in expected.items():
        if message.get(key) != value:
            return False

    return True


def decode_state(payload):
    try:
        return json.loads(payload.decode())
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        return None


class MqttRuntime:
    def __init__(self, client, subscription_acks):
        self.client = client
        self.subscription_acks = subscription_acks


def connect_client(args, state_messages):
    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:
        raise SystemExit("Missing paho-mqtt. Install requirements.txt or run this inside the gateway container.") from exc

    connected = threading.Event()
    connect_errors = queue.Queue()
    subscription_acks = queue.Queue()

    client = mqtt.Client()
    if args.username or args.password:
        client.username_pw_set(args.username, args.password)

    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            connected.set()
        else:
            connect_errors.put(rc)

    def on_message(client, userdata, message):
        decoded = decode_state(message.payload)
        if decoded is not None:
            state_messages.put(decoded)

    def on_subscribe(client, userdata, mid, granted_qos):
        subscription_acks.put(mid)

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_subscribe = on_subscribe
    client.connect(args.broker, args.port, keepalive=60)
    client.loop_start()

    if not connected.wait(args.mqtt_timeout):
        client.loop_stop()
        client.disconnect()
        if not connect_errors.empty():
            raise SystemExit(f"MQTT connection failed with rc={connect_errors.get()}")
        raise SystemExit(f"Timed out waiting {args.mqtt_timeout:g}s for MQTT connection.")

    return MqttRuntime(client, subscription_acks)


def subscribe_and_wait(runtime, topic, qos, timeout):
    result = runtime.client.subscribe(topic, qos=qos)
    rc, mid = result if isinstance(result, tuple) else (0, getattr(result, "mid", None))
    if rc != 0:
        raise SystemExit(f"MQTT subscribe failed with rc={rc} for {topic}")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            ack_mid = runtime.subscription_acks.get(timeout=min(0.25, remaining))
        except queue.Empty:
            continue

        if mid is None or ack_mid == mid:
            return

    raise SystemExit(f"Timed out waiting {timeout:g}s for MQTT subscribe acknowledgement on {topic}")


def wait_for_expected_state(state_messages, expected, timeout):
    deadline = time.monotonic() + timeout
    seen = []

    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            message = state_messages.get(timeout=min(0.25, remaining))
        except queue.Empty:
            continue

        seen.append(message)
        if matches_expected_state(message, expected):
            return message, seen

    return None, seen


def publish_message(client, topic, message, qos):
    result = client.publish(topic, payload=message, qos=qos)
    info = {
        "rc": None,
        "mid": None,
        "published": None,
        "error": None,
    }

    if isinstance(result, tuple):
        info["rc"] = result[0] if len(result) > 0 else None
        info["mid"] = result[1] if len(result) > 1 else None
        info["published"] = info["rc"] == 0
        return info

    info["rc"] = getattr(result, "rc", None)
    info["mid"] = getattr(result, "mid", None)
    result.wait_for_publish()

    is_published = getattr(result, "is_published", None)
    info["published"] = bool(is_published()) if callable(is_published) else True
    return info


def drain_queue(messages):
    while not messages.empty():
        try:
            messages.get_nowait()
        except queue.Empty:
            return


def observe_step(step):
    answer = input(f"Did the real skylight {step['description']}? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def write_proof_event(path, event):
    if path is None:
        return

    with open(path, "a", encoding="utf-8") as proof_file:
        proof_file.write(json.dumps(event, sort_keys=True) + "\n")


def publish_setup_step(args, runtime, state_messages, topic, step):
    payload = step["payload"]
    expected = step["expected_state"]
    message = json.dumps(payload, separators=(",", ":"))
    print(f"{step['name']}: {message}")
    if runtime is None:
        return []

    failures = []
    try:
        publish_info = publish_message(runtime.client, topic, message, args.qos)
    except Exception as exc:
        failures.append(f"{step['name']}: MQTT publish failed: {exc}")
        print(f"  publish: error={exc}")
        return failures

    if publish_info["rc"] not in (None, 0):
        failures.append(f"{step['name']}: MQTT publish returned rc={publish_info['rc']}")
    if publish_info["published"] is False:
        failures.append(f"{step['name']}: MQTT publish did not complete")
    print(
        "  publish: "
        f"rc={publish_info['rc']!r}, "
        f"mid={publish_info['mid']!r}, "
        f"published={publish_info['published']!r}"
    )

    if args.wait_state:
        matched_state, seen_states = wait_for_expected_state(state_messages, expected, args.state_timeout)
        if matched_state is None:
            print(f"  state: no matching setup state within {args.state_timeout:g}s; seen={seen_states}")
            failures.append(f"{step['name']}: missing matching MQTT setup state")
        else:
            print(f"  state: {json.dumps(matched_state, separators=(',', ':'))}")
    return failures


def precondition_visible_start(args, runtime, topic, state_messages):
    print("precondition: setting a dim cool baseline, then turning off before proof prompts")
    failures = []
    for step in precondition_steps(args.brightness):
        drain_queue(state_messages)
        failures.extend(publish_setup_step(args, runtime, state_messages, topic, step))
        time.sleep(args.delay)
    return failures


def publish_sequence(args):
    topic = command_topic(args.discovery_prefix, args.component, args.mesh_topic, args.device_id)
    state = state_topic(args.discovery_prefix, args.component, args.mesh_topic, args.device_id)
    run_id = getattr(args, "run_id", None) or default_run_id("mqtt")

    state_messages = queue.Queue()
    runtime = None
    failed_steps = []
    if not args.dry_run:
        runtime = connect_client(args, state_messages)
        if args.wait_state:
            subscribe_and_wait(runtime, state, args.qos, args.mqtt_timeout)

    print(f"command: {topic}")
    if args.wait_state:
        print(f"state:   {state}")
    if args.proof_log:
        print(f"proof:   {args.proof_log}")
        print(f"run_id:  {run_id}")

    try:
        precondition_enabled = getattr(args, "precondition_visible_start", False)
        if precondition_enabled:
            failed_steps.extend(precondition_visible_start(args, runtime, topic, state_messages))
            if failed_steps:
                print("\nSmoke test setup failed:")
                for failed_step in failed_steps:
                    print(f"  - {failed_step}")
                return 1

        for step in smoke_steps(args.brightness):
            payload = step["payload"]
            expected = step["expected_state"]
            matched_state = None
            seen_states = []
            observed = None
            publish_info = {
                "rc": None,
                "mid": None,
                "published": None,
                "error": None,
            }
            state_elapsed_ms = None

            drain_queue(state_messages)

            message = json.dumps(payload, separators=(",", ":"))
            print(f"{step['name']}: {message}")
            if runtime is not None:
                try:
                    publish_info = publish_message(runtime.client, topic, message, args.qos)
                except Exception as exc:
                    publish_info["error"] = str(exc)
                    failed_steps.append(f"{step['name']}: MQTT publish failed: {exc}")
                else:
                    if publish_info["rc"] not in (None, 0):
                        failed_steps.append(f"{step['name']}: MQTT publish returned rc={publish_info['rc']}")
                    if publish_info["published"] is False:
                        failed_steps.append(f"{step['name']}: MQTT publish did not complete")

                if publish_info["error"]:
                    print(f"  publish: error={publish_info['error']}")
                elif publish_info["rc"] is not None or publish_info["published"] is not None:
                    print(
                        "  publish: "
                        f"rc={publish_info['rc']!r}, "
                        f"mid={publish_info['mid']!r}, "
                        f"published={publish_info['published']!r}"
                    )

            if args.wait_state and runtime is not None:
                state_start = time.monotonic()
                matched_state, seen_states = wait_for_expected_state(state_messages, expected, args.state_timeout)
                state_elapsed_ms = round((time.monotonic() - state_start) * 1000)
                if matched_state is None:
                    print(f"  state: no matching state within {args.state_timeout:g}s; seen={seen_states}")
                    failed_steps.append(f"{step['name']}: missing matching MQTT state")
                else:
                    print(f"  state: {json.dumps(matched_state, separators=(',', ':'))} ({state_elapsed_ms} ms)")

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
                    "command_topic": topic,
                    "state_topic": state,
                    "payload": payload,
                    "expected_state": expected,
                    "publish": publish_info,
                    "matched_state": matched_state,
                    "seen_states": seen_states,
                    "state_elapsed_ms": state_elapsed_ms,
                    "observed": observed,
                    "precondition_visible_start": precondition_enabled,
                },
            )

            time.sleep(args.delay)

        if failed_steps:
            print("\nSmoke test failed:")
            for failed_step in failed_steps:
                print(f"  - {failed_step}")
            return 1

        return 0
    finally:
        if runtime is not None:
            runtime.client.loop_stop()
            runtime.client.disconnect()


def main():
    parser = argparse.ArgumentParser(description="Publish HA MQTT light commands for a Pesetech skylight smoke test.")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Gateway config path used for MQTT defaults.")
    parser.add_argument("--broker", default=None, help="MQTT broker hostname or IP.")
    parser.add_argument("--port", type=int, default=None, help="MQTT broker port.")
    parser.add_argument("--username", default=None, help="MQTT username.")
    parser.add_argument("--password", default=None, help="MQTT password.")
    parser.add_argument("--discovery-prefix", default=None, help="MQTT discovery prefix.")
    parser.add_argument("--component", default="light", help="Home Assistant component.")
    parser.add_argument("--mesh-topic", default=None, help="Bridge MQTT topic/node id.")
    parser.add_argument("--device-id", default=None, help="Configured mesh device id.")
    parser.add_argument("--brightness", type=int, default=32640, help="Raw brightness value for the brightness test.")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds between commands.")
    parser.add_argument("--qos", type=int, default=0, choices=(0, 1, 2), help="MQTT QoS.")
    parser.add_argument("--mqtt-timeout", type=float, default=10.0, help="Seconds to wait for MQTT connect/subscribe.")
    parser.add_argument("--wait-state", action="store_true", help="Wait for the bridge to publish matching state.")
    parser.add_argument("--state-timeout", type=float, default=5.0, help="Seconds to wait for each matching state.")
    parser.add_argument("--observe", action="store_true", help="Prompt for visual confirmation of each real-light change.")
    parser.add_argument("--precondition-visible-start", action="store_true", help="Before proof prompts, set a dim cool baseline and turn off so the proof steps are easier to observe.")
    parser.add_argument("--proof-log", default=None, help="Append JSONL proof events to this file.")
    parser.add_argument("--run-id", default=None, help="Identifier to write into each proof event for this run.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without publishing.")
    raise SystemExit(publish_sequence(apply_config_defaults(parser.parse_args())))


if __name__ == "__main__":
    main()
