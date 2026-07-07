#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pesetech_mqtt_smoke import (
    DEFAULT_CONFIG,
    PESETECH_COOL_MIREDS,
    PESETECH_WARM_MIREDS,
    clean_optional,
    configured_device_id,
    load_config,
)


STEP_NAMES = ["on", "brightness", "warm", "cool", "off"]


def command_topic(discovery_prefix, component, mesh_topic, device_id):
    return f"{discovery_prefix}/{component}/{mesh_topic}/{device_id}/set"


def state_topic(discovery_prefix, component, mesh_topic, device_id):
    return f"{discovery_prefix}/{component}/{mesh_topic}/{device_id}/state"


def apply_config_defaults(args):
    config = load_config(args.config)
    mqtt = config.get("mqtt") or {}
    mesh = config.get("mesh") or {}

    if args.discovery_prefix is None:
        args.discovery_prefix = clean_optional(mqtt.get("discovery_prefix")) or "homeassistant"
    if args.mesh_topic is None:
        args.mesh_topic = clean_optional(mqtt.get("topic")) or clean_optional(mqtt.get("node_id")) or "mqtt_mesh"
    args.device_id = configured_device_id(mesh, clean_optional(args.device_id))

    return args


def expected_steps(brightness):
    return [
        {
            "step": "on",
            "payload": {"state": "ON"},
            "expected_state": {"state": "ON"},
        },
        {
            "step": "brightness",
            "payload": {"state": "ON", "brightness": brightness},
            "expected_state": {"state": "ON", "brightness": brightness},
        },
        {
            "step": "warm",
            "payload": {"state": "ON", "color_temp": PESETECH_WARM_MIREDS},
            "expected_state": {"state": "ON", "color_temp": PESETECH_WARM_MIREDS},
        },
        {
            "step": "cool",
            "payload": {"state": "ON", "color_temp": PESETECH_COOL_MIREDS},
            "expected_state": {"state": "ON", "color_temp": PESETECH_COOL_MIREDS},
        },
        {
            "step": "off",
            "payload": {"state": "OFF"},
            "expected_state": {"state": "OFF"},
        },
    ]


def load_jsonl(path):
    events = []
    errors = []

    with open(path, "r", encoding="utf-8") as proof_file:
        for line_number, line in enumerate(proof_file, start=1):
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as exc:
                errors.append(f"line {line_number}: invalid JSON: {exc}")

    return events, errors


def sequence_matches_run_id(sequence, run_id=None):
    if run_id is not None:
        return all(event.get("run_id") == run_id for event in sequence)

    run_ids = {
        event.get("run_id")
        for event in sequence
        if event.get("run_id") is not None
    }
    return len(run_ids) <= 1


def sequence_run_id(sequence):
    if not sequence:
        return None
    run_ids = {
        event.get("run_id")
        for event in sequence
        if event.get("run_id") is not None
    }
    if len(run_ids) == 1:
        return next(iter(run_ids))
    return None


def latest_sequence(events, run_id=None):
    if len(events) < len(STEP_NAMES):
        return None

    for index in range(len(events) - len(STEP_NAMES), -1, -1):
        window = events[index : index + len(STEP_NAMES)]
        if [event.get("step") for event in window] == STEP_NAMES and sequence_matches_run_id(window, run_id):
            return window

    return None


def state_contains_expected(state, expected):
    if not isinstance(state, dict):
        return False

    return all(state.get(key) == value for key, value in expected.items())


def validate_sequence(events, args):
    errors = []
    run_id = getattr(args, "run_id", None)
    sequence = latest_sequence(events, run_id)
    if sequence is None:
        suffix = f" for run_id {run_id!r}" if run_id else ""
        return None, [f"proof log does not contain a complete on/brightness/warm/cool/off sequence{suffix}"]

    expected_command_topic = command_topic(args.discovery_prefix, args.component, args.mesh_topic, args.device_id)
    expected_state_topic = state_topic(args.discovery_prefix, args.component, args.mesh_topic, args.device_id)

    for event, expected in zip(sequence, expected_steps(args.brightness)):
        prefix = f"{event.get('step', '<missing step>')}:"

        if event.get("payload") != expected["payload"]:
            errors.append(f"{prefix} payload {event.get('payload')!r} != {expected['payload']!r}")

        if event.get("expected_state") != expected["expected_state"]:
            errors.append(f"{prefix} expected_state {event.get('expected_state')!r} != {expected['expected_state']!r}")

        if event.get("command_topic") != expected_command_topic:
            errors.append(f"{prefix} command_topic {event.get('command_topic')!r} != {expected_command_topic!r}")

        if event.get("state_topic") != expected_state_topic:
            errors.append(f"{prefix} state_topic {event.get('state_topic')!r} != {expected_state_topic!r}")

        matched_state = event.get("matched_state")
        if not args.allow_missing_state and not state_contains_expected(matched_state, expected["expected_state"]):
            errors.append(f"{prefix} missing matching MQTT state for {expected['expected_state']!r}")

        publish = event.get("publish")
        if isinstance(publish, dict):
            if publish.get("error"):
                errors.append(f"{prefix} MQTT publish failed: {publish['error']}")
            if publish.get("rc") not in (None, 0):
                errors.append(f"{prefix} MQTT publish returned rc={publish.get('rc')!r}")
            if publish.get("published") is False:
                errors.append(f"{prefix} MQTT publish did not complete")

        if not args.allow_unobserved and event.get("observed") is not True:
            errors.append(f"{prefix} real-light observation was not confirmed true")

    return sequence, errors


def sequence_summary(sequence, args=None):
    if sequence is None:
        return None

    if args is None:
        matched_count = sum(1 for event in sequence if isinstance(event.get("matched_state"), dict))
    else:
        matched_count = sum(
            1
            for event, expected in zip(sequence, expected_steps(args.brightness))
            if state_contains_expected(event.get("matched_state"), expected["expected_state"])
        )

    observed_yes = sum(1 for event in sequence if event.get("observed") is True)
    observed_no = sum(1 for event in sequence if event.get("observed") is False)
    observed_missing = sum(1 for event in sequence if event.get("observed") is None)

    return {
        "total": len(sequence),
        "mqtt_state_matched": matched_count,
        "observed_yes": observed_yes,
        "observed_no": observed_no,
        "observed_missing": observed_missing,
    }


def print_report(sequence, errors, args=None):
    if sequence is not None:
        print("Validated proof sequence:")
        run_id = sequence_run_id(sequence)
        if run_id:
            print(f"  run_id: {run_id}")
        for event in sequence:
            publish = event.get("publish") if isinstance(event.get("publish"), dict) else {}
            print(
                f"  - {event.get('step')}: "
                f"observed={event.get('observed')!r}, "
                f"matched_state={event.get('matched_state')!r}, "
                f"publish_rc={publish.get('rc')!r}, "
                f"published={publish.get('published')!r}, "
                f"state_elapsed_ms={event.get('state_elapsed_ms')!r}"
            )
        summary = sequence_summary(sequence, args)
        print(
            f"Summary: MQTT state matched {summary['mqtt_state_matched']}/{summary['total']}; "
            f"visual yes={summary['observed_yes']}, "
            f"no={summary['observed_no']}, "
            f"unobserved={summary['observed_missing']}"
        )

    if errors:
        print("\nProof verification failed:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("\nProof verification passed.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Verify a Pesetech skylight MQTT smoke-test proof log.")
    parser.add_argument("proof_log", help="JSONL proof log from pesetech_mqtt_smoke.py.")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Gateway config path used for MQTT topic defaults.")
    parser.add_argument("--discovery-prefix", default=None, help="MQTT discovery prefix.")
    parser.add_argument("--component", default="light", help="Home Assistant component.")
    parser.add_argument("--mesh-topic", default=None, help="Bridge MQTT topic/node id.")
    parser.add_argument("--device-id", default=None, help="Configured mesh device id.")
    parser.add_argument("--brightness", type=int, default=32640, help="Raw brightness value used for the smoke test.")
    parser.add_argument("--run-id", default=None, help="Verify only the proof sequence with this run id.")
    parser.add_argument("--allow-missing-state", action="store_true", help="Do not require matching MQTT state events.")
    parser.add_argument("--allow-unobserved", action="store_true", help="Do not require visual confirmation.")
    args = apply_config_defaults(parser.parse_args())

    events, errors = load_jsonl(args.proof_log)
    sequence, validation_errors = validate_sequence(events, args)
    raise SystemExit(print_report(sequence, errors + validation_errors, args=args))


if __name__ == "__main__":
    main()
