#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pesetech_ha_service_smoke import (
    DEFAULT_BRIGHTNESS,
    DEFAULT_ENTITY_ID,
    DEFAULT_HA_URL,
    DEFAULT_MQTT_BRIGHTNESS_SCALE,
    DEFAULT_MQTT_BRIGHTNESS_TOLERANCE,
    DEFAULT_MQTT_MIRED_TOLERANCE,
    compact_state,
    expected_mqtt_attributes,
    normalize_base_url,
    service_path,
)


STEP_NAMES = ["on", "brightness", "warm", "cool", "off"]
SUCCESS_STATUSES = {200, 201}


def expected_steps(entity_id, brightness, warm_kelvin, cool_kelvin):
    return [
        {
            "step": "on",
            "service": "light.turn_on",
            "service_path": service_path("light", "turn_on"),
            "payload": {"entity_id": entity_id},
            "expected_state": {"state": "on"},
        },
        {
            "step": "brightness",
            "service": "light.turn_on",
            "service_path": service_path("light", "turn_on"),
            "payload": {"entity_id": entity_id, "brightness": brightness},
            "expected_state": {"state": "on", "attributes": {"brightness": brightness}},
        },
        {
            "step": "warm",
            "service": "light.turn_on",
            "service_path": service_path("light", "turn_on"),
            "payload": {"entity_id": entity_id, "color_temp_kelvin": warm_kelvin},
            "expected_state": {"state": "on", "attributes": {"color_temp_kelvin": warm_kelvin}},
        },
        {
            "step": "cool",
            "service": "light.turn_on",
            "service_path": service_path("light", "turn_on"),
            "payload": {"entity_id": entity_id, "color_temp_kelvin": cool_kelvin},
            "expected_state": {"state": "on", "attributes": {"color_temp_kelvin": cool_kelvin}},
        },
        {
            "step": "off",
            "service": "light.turn_off",
            "service_path": service_path("light", "turn_off"),
            "payload": {"entity_id": entity_id},
            "expected_state": {"state": "off"},
        },
    ]


def expected_required_mqtt_fields(step_name):
    if step_name == "brightness":
        return ["brightness"]
    if step_name in {"warm", "cool"}:
        return ["color_temp"]
    return []


def expected_mqtt_attributes_for_step(expected, brightness_scale=DEFAULT_MQTT_BRIGHTNESS_SCALE):
    return expected_mqtt_attributes(
        {
            "step": expected["step"],
            "service": expected["service"],
            "payload": expected["payload"],
        },
        brightness_scale,
    )


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


def values_match(actual, expected, tolerance):
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return abs(actual - expected) <= tolerance
    return actual == expected


def state_matches_expected(state, expected, *, require_attributes=False, brightness_tolerance=2, kelvin_tolerance=150):
    if not isinstance(state, dict):
        return False
    if state.get("state") != expected.get("state"):
        return False
    if not require_attributes:
        return True

    expected_attributes = expected.get("attributes") or {}
    attributes = state.get("attributes") or {}
    for key, expected_value in expected_attributes.items():
        tolerance = kelvin_tolerance if key == "color_temp_kelvin" else brightness_tolerance
        if not values_match(attributes.get(key), expected_value, tolerance):
            return False
    return True


def mqtt_state_contains_expected(state, expected):
    if not isinstance(state, dict) or not isinstance(expected, dict):
        return False
    return all(state.get(key) == value for key, value in expected.items())


def mqtt_attribute_matches(state, expected_attributes, *, brightness_tolerance, mired_tolerance):
    if not isinstance(state, dict):
        return False
    for field, expected_value in expected_attributes.items():
        tolerance = brightness_tolerance if field == "brightness" else mired_tolerance
        if not values_match(state.get(field), expected_value, tolerance):
            return False
    return True


def validate_response(event, prefix):
    errors = []
    if event.get("response_error"):
        errors.append(f"{prefix} Home Assistant service call failed: {event['response_error']}")
        return errors

    response = event.get("response")
    if not isinstance(response, dict):
        errors.append(f"{prefix} missing Home Assistant service response")
        return errors

    status = response.get("status")
    if status not in SUCCESS_STATUSES:
        errors.append(f"{prefix} Home Assistant service returned status {status!r}")
    return errors


def validate_sequence(events, args):
    errors = []
    run_id = getattr(args, "run_id", None)
    sequence = latest_sequence(events, run_id)
    if sequence is None:
        suffix = f" for run_id {run_id!r}" if run_id else ""
        return None, [f"proof log does not contain a complete on/brightness/warm/cool/off sequence{suffix}"]

    expected_url = normalize_base_url(args.url)

    for event, expected in zip(sequence, expected_steps(args.entity_id, args.brightness, args.warm_kelvin, args.cool_kelvin)):
        prefix = f"{event.get('step', '<missing step>')}:"

        if event.get("home_assistant_url") != expected_url:
            errors.append(f"{prefix} home_assistant_url {event.get('home_assistant_url')!r} != {expected_url!r}")

        if event.get("entity_id") != args.entity_id:
            errors.append(f"{prefix} entity_id {event.get('entity_id')!r} != {args.entity_id!r}")

        if event.get("service") != expected["service"]:
            errors.append(f"{prefix} service {event.get('service')!r} != {expected['service']!r}")

        if event.get("service_path") != expected["service_path"]:
            errors.append(f"{prefix} service_path {event.get('service_path')!r} != {expected['service_path']!r}")

        if event.get("payload") != expected["payload"]:
            errors.append(f"{prefix} payload {event.get('payload')!r} != {expected['payload']!r}")

        if event.get("expected_state") != expected["expected_state"]:
            errors.append(f"{prefix} expected_state {event.get('expected_state')!r} != {expected['expected_state']!r}")

        if not args.allow_missing_state and not state_matches_expected(
            event.get("matched_state"),
            expected["expected_state"],
            require_attributes=args.require_attributes,
            brightness_tolerance=args.brightness_tolerance,
            kelvin_tolerance=args.kelvin_tolerance,
        ):
            errors.append(f"{prefix} missing matching Home Assistant state for {expected['expected_state']!r}")

        expected_mqtt_state = event.get("expected_mqtt_state")
        require_mqtt = args.require_mqtt_state or args.require_mqtt_attributes
        if require_mqtt:
            if expected_mqtt_state != {"state": "OFF" if expected["service"] == "light.turn_off" else "ON"}:
                errors.append(f"{prefix} expected_mqtt_state {expected_mqtt_state!r} is not the expected bridge on/off state")
            if not mqtt_state_contains_expected(event.get("matched_mqtt_state"), expected_mqtt_state):
                errors.append(f"{prefix} missing matching MQTT bridge state for {expected_mqtt_state!r}")
        if args.require_mqtt_attributes:
            expected_fields = expected_required_mqtt_fields(expected["step"])
            expected_mqtt_attributes = expected_mqtt_attributes_for_step(expected, args.mqtt_brightness_scale)
            if event.get("required_mqtt_fields") != expected_fields:
                errors.append(f"{prefix} required_mqtt_fields {event.get('required_mqtt_fields')!r} != {expected_fields!r}")
            if event.get("expected_mqtt_attributes") != expected_mqtt_attributes:
                errors.append(
                    f"{prefix} expected_mqtt_attributes {event.get('expected_mqtt_attributes')!r} "
                    f"!= {expected_mqtt_attributes!r}"
                )
            matched_mqtt_state = event.get("matched_mqtt_state") or {}
            missing_fields = [field for field in expected_fields if field not in matched_mqtt_state]
            if missing_fields:
                errors.append(f"{prefix} missing MQTT bridge attribute fields: {', '.join(missing_fields)}")
            elif not mqtt_attribute_matches(
                matched_mqtt_state,
                expected_mqtt_attributes,
                brightness_tolerance=args.mqtt_brightness_tolerance,
                mired_tolerance=args.mqtt_mired_tolerance,
            ):
                errors.append(
                    f"{prefix} MQTT bridge attributes {matched_mqtt_state!r} "
                    f"do not match expected {expected_mqtt_attributes!r}"
                )

        if not args.allow_service_error:
            errors.extend(validate_response(event, prefix))

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
            for event, expected in zip(sequence, expected_steps(args.entity_id, args.brightness, args.warm_kelvin, args.cool_kelvin))
            if state_matches_expected(
                event.get("matched_state"),
                expected["expected_state"],
                require_attributes=args.require_attributes,
                brightness_tolerance=args.brightness_tolerance,
                kelvin_tolerance=args.kelvin_tolerance,
            )
        )

    observed_yes = sum(1 for event in sequence if event.get("observed") is True)
    observed_no = sum(1 for event in sequence if event.get("observed") is False)
    observed_missing = sum(1 for event in sequence if event.get("observed") is None)
    service_ok = sum(
        1
        for event in sequence
        if not event.get("response_error")
        and isinstance(event.get("response"), dict)
        and event["response"].get("status") in SUCCESS_STATUSES
    )
    mqtt_matched = sum(
        1
        for event in sequence
        if mqtt_state_contains_expected(event.get("matched_mqtt_state"), event.get("expected_mqtt_state"))
    )

    return {
        "total": len(sequence),
        "ha_state_matched": matched_count,
        "mqtt_state_matched": mqtt_matched,
        "service_ok": service_ok,
        "observed_yes": observed_yes,
        "observed_no": observed_no,
        "observed_missing": observed_missing,
    }


def print_report(sequence, errors, args=None):
    if sequence is not None:
        print("Validated Home Assistant service proof sequence:")
        run_id = sequence_run_id(sequence)
        if run_id:
            print(f"  run_id: {run_id}")
        for event in sequence:
            response = event.get("response") if isinstance(event.get("response"), dict) else {}
            print(
                f"  - {event.get('step')}: "
                f"service={event.get('service')!r}, "
                f"observed={event.get('observed')!r}, "
                f"matched_state={compact_state(event.get('matched_state'))!r}, "
                f"matched_mqtt_state={event.get('matched_mqtt_state')!r}, "
                f"status={response.get('status')!r}, "
                f"state_elapsed_ms={event.get('state_elapsed_ms')!r}"
            )
        summary = sequence_summary(sequence, args)
        print(
            f"Summary: HA state matched {summary['ha_state_matched']}/{summary['total']}; "
            f"MQTT state matched {summary['mqtt_state_matched']}/{summary['total']}; "
            f"service ok={summary['service_ok']}/{summary['total']}; "
            f"visual yes={summary['observed_yes']}, "
            f"no={summary['observed_no']}, "
            f"unobserved={summary['observed_missing']}"
        )

    if errors:
        print("\nHome Assistant service proof verification failed:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("\nHome Assistant service proof verification passed.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Verify a Pesetech skylight Home Assistant service proof log.")
    parser.add_argument("proof_log", help="JSONL proof log from pesetech_ha_service_smoke.py.")
    parser.add_argument("--url", default=DEFAULT_HA_URL, help="Home Assistant base URL used for the proof.")
    parser.add_argument("--entity-id", default=DEFAULT_ENTITY_ID, help="Home Assistant light entity id.")
    parser.add_argument("--brightness", type=int, default=DEFAULT_BRIGHTNESS, help="Home Assistant brightness value used for the proof.")
    parser.add_argument("--warm-kelvin", type=int, default=2200, help="Warm color temperature used for the proof.")
    parser.add_argument("--cool-kelvin", type=int, default=6500, help="Cool color temperature used for the proof.")
    parser.add_argument("--run-id", default=None, help="Verify only the proof sequence with this run id.")
    parser.add_argument("--require-attributes", action="store_true", help="Require matching brightness/color_temp_kelvin attributes.")
    parser.add_argument("--brightness-tolerance", type=int, default=2, help="Allowed HA brightness attribute difference.")
    parser.add_argument("--kelvin-tolerance", type=int, default=150, help="Allowed HA color_temp_kelvin attribute difference.")
    parser.add_argument("--allow-missing-state", action="store_true", help="Do not require matching Home Assistant state events.")
    parser.add_argument("--require-mqtt-state", action="store_true", help="Require matching MQTT bridge state events recorded by the HA service smoke test.")
    parser.add_argument("--require-mqtt-attributes", action="store_true", help="Require matching brightness/color_temp values in recorded MQTT bridge state for brightness/CCT steps.")
    parser.add_argument("--mqtt-brightness-scale", type=int, default=DEFAULT_MQTT_BRIGHTNESS_SCALE, help="MQTT bridge brightness scale expected for HA brightness conversion.")
    parser.add_argument("--mqtt-brightness-tolerance", type=int, default=DEFAULT_MQTT_BRIGHTNESS_TOLERANCE, help="Allowed MQTT bridge brightness difference.")
    parser.add_argument("--mqtt-mired-tolerance", type=int, default=DEFAULT_MQTT_MIRED_TOLERANCE, help="Allowed MQTT bridge color_temp mired difference.")
    parser.add_argument("--allow-service-error", action="store_true", help="Do not fail on missing/error service responses.")
    parser.add_argument("--allow-unobserved", action="store_true", help="Do not require visual confirmation.")
    args = parser.parse_args()

    events, errors = load_jsonl(args.proof_log)
    sequence, validation_errors = validate_sequence(events, args)
    raise SystemExit(print_report(sequence, errors + validation_errors, args=args))


if __name__ == "__main__":
    main()
