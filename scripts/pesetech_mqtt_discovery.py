#!/usr/bin/env python3
import argparse
import json
import queue
import sys
import threading
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pesetech_mqtt_smoke import DEFAULT_CONFIG, apply_config_defaults, clean_optional, load_config


def discovery_topic(discovery_prefix, component, mesh_topic, device_id):
    return f"{discovery_prefix}/{component}/{mesh_topic}/{device_id}/config"


def discovery_candidate_topic(discovery_prefix, component):
    return f"{discovery_prefix}/{component}/#"


def base_topic(discovery_prefix, component, mesh_topic, device_id):
    return f"{discovery_prefix}/{component}/{mesh_topic}/{device_id}"


def decode_json_payload(payload):
    try:
        return json.loads(payload.decode())
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        return None


class DiscoveryRuntime:
    def __init__(self, client, subscription_acks):
        self.client = client
        self.subscription_acks = subscription_acks


def connect_client(args, messages):
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
        messages.put(
            {
                "topic": message.topic,
                "retain": bool(getattr(message, "retain", False)),
                "payload": decode_json_payload(message.payload),
                "raw_payload": message.payload.decode(errors="replace"),
            }
        )

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

    return DiscoveryRuntime(client, subscription_acks)


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


def wait_for_discovery(messages, topic, timeout):
    deadline = time.monotonic() + timeout
    seen = []

    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            message = messages.get(timeout=min(0.25, remaining))
        except queue.Empty:
            continue

        seen.append(message)
        if message.get("topic") == topic:
            return message, seen

    return None, seen


def is_config_message(message):
    if not isinstance(message, dict):
        return False
    topic = message.get("topic")
    payload = message.get("payload")
    return isinstance(topic, str) and topic.endswith("/config") and isinstance(payload, dict)


def summarize_candidate(message):
    payload = message.get("payload") or {}
    device = payload.get("device") if isinstance(payload.get("device"), dict) else {}
    fields = []
    for label, value in (
        ("default_entity_id", payload.get("default_entity_id")),
        ("unique_id", payload.get("unique_id")),
        ("name", payload.get("name") or device.get("name")),
        ("manufacturer", device.get("manufacturer")),
        ("model", device.get("model")),
    ):
        if value:
            fields.append(f"{label}={value!r}")
    suffix = f" ({', '.join(fields)})" if fields else ""
    return f"{message.get('topic')}{suffix}"


def collect_discovery_candidates(messages, exact_topic, timeout, limit=10):
    deadline = time.monotonic() + timeout
    candidates = []
    seen = []
    seen_topics = set()

    while time.monotonic() < deadline and len(candidates) < limit:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            message = messages.get(timeout=min(0.25, remaining))
        except queue.Empty:
            continue

        seen.append(message)
        topic = message.get("topic")
        if topic == exact_topic or topic in seen_topics or not is_config_message(message):
            continue
        seen_topics.add(topic)
        candidates.append(message)

    return candidates, seen


def expect_equal(errors, path, actual, expected):
    if actual != expected:
        errors.append(f"{path}: expected {expected!r}, got {actual!r}")


def expect_present(errors, path, value):
    if value in (None, "", []):
        errors.append(f"{path}: expected a non-empty value")


def apply_discovery_config_defaults(args):
    args = apply_config_defaults(args)
    if getattr(args, "default_entity_id", None) is None:
        config = load_config(args.config)
        mesh = config.get("mesh") or {}
        node = mesh.get(args.device_id) or {}
        if isinstance(node, dict):
            args.default_entity_id = clean_optional(node.get("default_entity_id"))
    if not getattr(args, "default_entity_id", None):
        args.default_entity_id = f"{args.component}.{args.device_id}"
    return args


def validate_discovery(payload, args):
    errors = []
    if not isinstance(payload, dict):
        return ["discovery payload is not a JSON object"]

    expected_base = base_topic(args.discovery_prefix, args.component, args.mesh_topic, args.device_id)
    expect_equal(errors, "~", payload.get("~"), expected_base)
    expect_equal(errors, "command_topic", payload.get("command_topic"), "~/set")
    expect_equal(errors, "state_topic", payload.get("state_topic"), "~/state")
    expect_equal(errors, "schema", payload.get("schema"), "json")
    expect_equal(errors, "brightness", payload.get("brightness"), True)
    expect_equal(errors, "brightness_scale", payload.get("brightness_scale"), args.brightness_scale)
    expect_equal(errors, "min_mireds", payload.get("min_mireds"), args.min_mireds)
    expect_equal(errors, "max_mireds", payload.get("max_mireds"), args.max_mireds)
    expect_equal(errors, "default_entity_id", payload.get("default_entity_id"), args.default_entity_id)
    expect_equal(errors, "unique_id", payload.get("unique_id"), f"{args.mesh_topic}_{args.device_id}")

    modes = payload.get("supported_color_modes")
    if modes != ["color_temp"]:
        errors.append(f"supported_color_modes: expected ['color_temp'], got {modes!r}")
    if "object_id" in payload:
        errors.append("object_id must not be present in Home Assistant discovery config; use default_entity_id")
    if "color_mode" in payload:
        errors.append("color_mode must not be present in Home Assistant discovery config")

    device = payload.get("device")
    if not isinstance(device, dict):
        errors.append("device: expected object")
    else:
        expect_present(errors, "device.identifiers", device.get("identifiers"))
        expect_present(errors, "device.name", device.get("name"))
        expect_equal(errors, "device.manufacturer", device.get("manufacturer"), "Pesetech/Lepu")
        expect_equal(errors, "device.model", device.get("model"), "Artificial Skylight")

    origin = payload.get("origin")
    if not isinstance(origin, dict):
        errors.append("origin: expected object")
    else:
        expect_equal(errors, "origin.name", origin.get("name"), "pesetech-home-assistant")
        expect_present(errors, "origin.support_url", origin.get("support_url"))

    return errors


def print_report(topic, message, seen, errors, dump_json=False, candidates=None):
    print(f"discovery: {topic}")
    if message is None:
        print(f"received: 0 matching message(s); seen={len(seen)}")
    else:
        print(f"received: retain={message.get('retain')!r}")
        if dump_json:
            print(json.dumps(message.get("payload"), indent=2, sort_keys=True))

    if errors:
        print("\nDiscovery verification failed:")
        for error in errors:
            print(f"  - {error}")
        if candidates:
            print("\nCandidate retained light discovery configs:")
            for candidate in candidates:
                print(f"  - {summarize_candidate(candidate)}")
        return 1

    print("\nDiscovery verification passed.")
    return 0


def verify_discovery(args):
    topic = discovery_topic(args.discovery_prefix, args.component, args.mesh_topic, args.device_id)
    messages = queue.Queue()
    runtime = None

    if args.dry_run:
        return print_report(topic, {"retain": None, "payload": None}, [], [], dump_json=False)

    try:
        runtime = connect_client(args, messages)
        subscribe_and_wait(runtime, topic, args.qos, args.mqtt_timeout)
        message, seen = wait_for_discovery(messages, topic, args.discovery_timeout)
        candidates = []
        errors = []
        if message is None:
            errors.append(f"no retained discovery message received on {topic}")
            candidate_topic = discovery_candidate_topic(args.discovery_prefix, args.component)
            subscribe_and_wait(runtime, candidate_topic, args.qos, args.mqtt_timeout)
            candidates, candidate_seen = collect_discovery_candidates(
                messages,
                topic,
                args.candidate_timeout,
            )
            seen.extend(candidate_seen)
        else:
            errors.extend(validate_discovery(message.get("payload"), args))
            if args.require_retained and message.get("retain") is not True:
                errors.append("discovery message was not marked retained")

        return print_report(topic, message, seen, errors, dump_json=args.dump_json, candidates=candidates)
    finally:
        if runtime is not None:
            runtime.client.loop_stop()
            runtime.client.disconnect()


def main():
    parser = argparse.ArgumentParser(description="Verify the retained Home Assistant MQTT discovery payload.")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Gateway config path used for MQTT defaults.")
    parser.add_argument("--broker", default=None, help="MQTT broker hostname or IP.")
    parser.add_argument("--port", type=int, default=None, help="MQTT broker port.")
    parser.add_argument("--username", default=None, help="MQTT username.")
    parser.add_argument("--password", default=None, help="MQTT password.")
    parser.add_argument("--discovery-prefix", default=None, help="MQTT discovery prefix.")
    parser.add_argument("--component", default="light", help="Home Assistant component.")
    parser.add_argument("--mesh-topic", default=None, help="Bridge MQTT topic/node id.")
    parser.add_argument("--device-id", default=None, help="Configured mesh device id.")
    parser.add_argument("--default-entity-id", default=None, help="Expected Home Assistant entity id hint.")
    parser.add_argument("--brightness-scale", type=int, default=65280, help="Expected Pesetech brightness scale.")
    parser.add_argument("--min-mireds", type=int, default=100, help="Expected Pesetech cool mired endpoint.")
    parser.add_argument("--max-mireds", type=int, default=556, help="Expected Pesetech warm mired endpoint.")
    parser.add_argument("--qos", type=int, default=0, choices=(0, 1, 2), help="MQTT QoS.")
    parser.add_argument("--mqtt-timeout", type=float, default=10.0, help="Seconds to wait for MQTT connect/subscribe.")
    parser.add_argument("--discovery-timeout", type=float, default=5.0, help="Seconds to wait for discovery payload.")
    parser.add_argument("--candidate-timeout", type=float, default=2.0, help="Seconds to scan retained light discovery configs for troubleshooting when the exact topic is missing.")
    parser.add_argument("--require-retained", action="store_true", help="Require the received discovery message to be retained.")
    parser.add_argument("--dump-json", action="store_true", help="Print the discovery JSON payload.")
    parser.add_argument("--dry-run", action="store_true", help="Print expected topic without connecting.")
    args = apply_discovery_config_defaults(parser.parse_args())
    raise SystemExit(verify_discovery(args))


if __name__ == "__main__":
    main()
