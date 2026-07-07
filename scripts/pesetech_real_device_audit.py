#!/usr/bin/env python3
import argparse
import json
import sys
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Optional


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pesetech_verify_ha_service_proof as ha_verify
import pesetech_verify_proof as mqtt_verify
from pesetech_mqtt_smoke import DEFAULT_CONFIG


STEP_NAMES = ["on", "brightness", "warm", "cool", "off"]
OBJECTIVE_REQUIREMENTS = {
    "on/off": {"on", "off"},
    "brightness": {"brightness"},
    "color temperature": {"warm", "cool"},
}


@dataclass
class ProofCheck:
    name: str
    sequence: Optional[list]
    errors: list

    @property
    def ok(self):
        return not self.errors and self.sequence is not None

    @property
    def run_id(self):
        if self.sequence is None:
            return None
        ids = {event.get("run_id") for event in self.sequence if event.get("run_id") is not None}
        if len(ids) == 1:
            return next(iter(ids))
        return None

    @property
    def run_ids(self):
        if self.sequence is None:
            return set()
        return {event.get("run_id") for event in self.sequence if event.get("run_id")}

    @property
    def missing_run_id_steps(self):
        if self.sequence is None:
            return []
        return [event.get("step", "<missing step>") for event in self.sequence if not event.get("run_id")]


def mqtt_args(args):
    return mqtt_verify.apply_config_defaults(
        SimpleNamespace(
            config=args.config,
            discovery_prefix=args.discovery_prefix,
            component="light",
            mesh_topic=args.mesh_topic,
            device_id=args.device_id,
            brightness=args.mqtt_brightness,
            run_id=args.proof_run_id,
            allow_missing_state=False,
            allow_unobserved=args.allow_unobserved,
        )
    )


def ha_args(args):
    return SimpleNamespace(
        url=args.ha_url,
        entity_id=args.ha_entity_id,
        brightness=args.ha_brightness,
        warm_kelvin=args.ha_warm_kelvin,
        cool_kelvin=args.ha_cool_kelvin,
        run_id=args.proof_run_id,
        require_attributes=not args.allow_missing_ha_attributes,
        brightness_tolerance=args.ha_brightness_tolerance,
        kelvin_tolerance=args.ha_kelvin_tolerance,
        mqtt_brightness_scale=args.ha_mqtt_brightness_scale,
        mqtt_brightness_tolerance=args.ha_mqtt_brightness_tolerance,
        mqtt_mired_tolerance=args.ha_mqtt_mired_tolerance,
        allow_missing_state=False,
        require_mqtt_state=True,
        require_mqtt_attributes=not args.allow_missing_ha_mqtt_attributes,
        allow_service_error=False,
        allow_unobserved=args.allow_unobserved,
    )


def validate_mqtt_proof(args):
    events, parse_errors = mqtt_verify.load_jsonl(args.proof_log)
    sequence, validation_errors = mqtt_verify.validate_sequence(events, mqtt_args(args))
    return ProofCheck("MQTT command proof", sequence, parse_errors + validation_errors)


def validate_ha_proof(args):
    events, parse_errors = ha_verify.load_jsonl(args.ha_proof_log)
    sequence, validation_errors = ha_verify.validate_sequence(events, ha_args(args))
    return ProofCheck("Home Assistant service proof", sequence, parse_errors + validation_errors)


def requirement_status(checks):
    status = {}
    for requirement, steps in OBJECTIVE_REQUIREMENTS.items():
        status[requirement] = all(
            check.ok and steps.issubset({event.get("step") for event in check.sequence or []})
            for check in checks
        )
    return status


def run_id_errors(checks, allow_different_run_ids=False):
    if allow_different_run_ids:
        return []

    errors = []
    for check in checks:
        if check.sequence is None:
            continue
        missing_steps = check.missing_run_id_steps
        if missing_steps:
            errors.append(f"{check.name} is missing run_id on steps: {', '.join(missing_steps)}")
        if len(check.run_ids) > 1:
            errors.append(f"{check.name} contains multiple run_ids: {', '.join(sorted(check.run_ids))}")

    if errors:
        return errors

    ids = [check.run_id for check in checks if check.sequence is not None]
    if len(ids) != len(checks) or any(not run_id for run_id in ids):
        return ["proof logs must each contain a non-empty run_id"]
    if len(set(ids)) == 1:
        return []
    return [f"proof logs do not share the same run_id: {', '.join(ids)}"]


def parse_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return None

    value = value.strip()
    candidates = [value]
    if value.endswith("Z"):
        candidates.append(value[:-1] + "+00:00")

    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                return parsed
        except ValueError:
            pass
        try:
            parsed = datetime.strptime(candidate, "%Y-%m-%dT%H:%M:%S%z")
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                return parsed
        except ValueError:
            pass
    return None


def parsed_sequence_timestamps(check):
    if check.sequence is None:
        return []
    parsed = []
    for event in check.sequence:
        timestamp = parse_timestamp(event.get("timestamp"))
        if timestamp is None:
            return []
        parsed.append((event, timestamp))
    return parsed


def timestamp_errors(checks, allow_missing_timestamps=False):
    if allow_missing_timestamps:
        return []

    errors = []
    for check in checks:
        if check.sequence is None:
            continue
        parsed_events = []
        for event in check.sequence:
            parsed = parse_timestamp(event.get("timestamp"))
            if parsed is None:
                errors.append(
                    f"{check.name} has missing or invalid timestamp on step {event.get('step', '<missing step>')}: "
                    f"{event.get('timestamp')!r}"
                )
            else:
                parsed_events.append((event, parsed))
        if len(parsed_events) != len(check.sequence):
            continue
        for (previous_event, previous_timestamp), (event, timestamp) in zip(parsed_events, parsed_events[1:]):
            if timestamp < previous_timestamp:
                errors.append(
                    f"{check.name} timestamps are not chronological: "
                    f"{event.get('step', '<missing step>')} at {event.get('timestamp')!r} is before "
                    f"{previous_event.get('step', '<missing step>')} at {previous_event.get('timestamp')!r}"
                )
    return errors


def print_check(check):
    print(f"{check.name}: {'PASS' if check.ok else 'FAIL'}")
    if check.sequence is not None:
        if check.run_id:
            print(f"  run_id: {check.run_id}")
        print(f"  steps:  {', '.join(event.get('step', '<missing>') for event in check.sequence)}")
    for error in check.errors:
        print(f"  - {error}")


def phase_timing_errors(checks, allow_out_of_order_phases=False, allow_missing_timestamps=False):
    if allow_out_of_order_phases or allow_missing_timestamps or len(checks) < 2:
        return []

    mqtt_timestamps = parsed_sequence_timestamps(checks[0])
    ha_timestamps = parsed_sequence_timestamps(checks[1])
    if not mqtt_timestamps or not ha_timestamps:
        return []

    mqtt_end_event, mqtt_end = mqtt_timestamps[-1]
    ha_start_event, ha_start = ha_timestamps[0]
    if ha_start < mqtt_end:
        return [
            "Home Assistant service proof starts before MQTT command proof finishes: "
            f"{ha_start_event.get('step', '<missing step>')} at {ha_start_event.get('timestamp')!r} is before "
            f"{mqtt_end_event.get('step', '<missing step>')} at {mqtt_end_event.get('timestamp')!r}"
        ]
    return []


def ha_mqtt_topic_errors(args, ha_check, allow_missing_topic=False):
    if allow_missing_topic or ha_check.sequence is None:
        return []

    expected_args = mqtt_args(args)
    expected_topic = mqtt_verify.state_topic(
        expected_args.discovery_prefix,
        expected_args.component,
        expected_args.mesh_topic,
        expected_args.device_id,
    )

    errors = []
    for event in ha_check.sequence:
        if event.get("mqtt_state_topic") != expected_topic:
            errors.append(
                f"Home Assistant service proof {event.get('step', '<missing step>')}: "
                f"mqtt_state_topic {event.get('mqtt_state_topic')!r} != {expected_topic!r}"
            )
    return errors


def check_summary(args, check):
    if check.sequence is None:
        return None
    if check.name == "MQTT command proof":
        return mqtt_verify.sequence_summary(check.sequence, mqtt_args(args))
    if check.name == "Home Assistant service proof":
        return ha_verify.sequence_summary(check.sequence, ha_args(args))
    return None


def check_report(args, check):
    sequence = None
    if check.sequence is not None:
        sequence = [
            {
                "step": event.get("step"),
                "timestamp": event.get("timestamp"),
                "run_id": event.get("run_id"),
                "observed": event.get("observed"),
                "precondition_visible_start": event.get("precondition_visible_start"),
                "payload": event.get("payload"),
                "expected_state": event.get("expected_state"),
                "matched_state": event.get("matched_state"),
                "publish": event.get("publish"),
                "service": event.get("service"),
                "service_path": event.get("service_path"),
                "response_status": (event.get("response") or {}).get("status") if isinstance(event.get("response"), dict) else None,
                "state_topic": event.get("state_topic"),
                "mqtt_state_topic": event.get("mqtt_state_topic"),
                "expected_mqtt_state": event.get("expected_mqtt_state"),
                "expected_mqtt_attributes": event.get("expected_mqtt_attributes"),
                "matched_mqtt_state": event.get("matched_mqtt_state"),
            }
            for event in check.sequence
        ]

    return {
        "name": check.name,
        "ok": check.ok,
        "run_id": check.run_id,
        "run_ids": sorted(check.run_ids),
        "missing_run_id_steps": check.missing_run_id_steps,
        "summary": check_summary(args, check),
        "steps": sequence,
        "errors": check.errors,
    }


def shared_run_id(checks):
    ids = [check.run_id for check in checks if check.sequence is not None]
    if len(ids) == len(checks) and len(set(ids)) == 1:
        return ids[0]
    return None


def objective_next_action(passed, strict_visual_proof):
    if not passed:
        return "Fix the failed proof requirement above, rerun the failing proof gate, then rerun final-audit."
    if not strict_visual_proof:
        return "Run host prove-ha-addon without --readiness-only so each physical-light step can be visually confirmed."
    return "Objective proven. Leave the gateway running in service mode for Home Assistant control."


def objective_report(args, checks, requirements, errors, strict_visual_proof):
    expected_args = mqtt_args(args)
    technical_state_proven = not errors
    return {
        "goal": "Home Assistant controls the real Pesetech/Lepu skylight for on/off, brightness, and color temperature.",
        "proven": technical_state_proven and strict_visual_proof,
        "technical_state_proven": technical_state_proven,
        "strict_visual_proof": strict_visual_proof,
        "proof_run_id": shared_run_id(checks),
        "ha_url": args.ha_url,
        "ha_entity_id": args.ha_entity_id,
        "mqtt_command_topic": mqtt_verify.command_topic(
            expected_args.discovery_prefix,
            expected_args.component,
            expected_args.mesh_topic,
            expected_args.device_id,
        ),
        "mqtt_state_topic": mqtt_verify.state_topic(
            expected_args.discovery_prefix,
            expected_args.component,
            expected_args.mesh_topic,
            expected_args.device_id,
        ),
        "requirements": requirements,
        "next_action": objective_next_action(technical_state_proven, strict_visual_proof),
    }


def audit_report(args, checks, requirements, errors):
    strict_visual_proof = not args.allow_unobserved and not errors
    return {
        "passed": not errors,
        "objective_proven": not errors and strict_visual_proof,
        "strict_visual_proof": strict_visual_proof,
        "objective": objective_report(args, checks, requirements, errors, strict_visual_proof),
        "requirements": requirements,
        "checks": [check_report(args, check) for check in checks],
        "errors": errors,
        "target": {
            "config": args.config,
            "proof_log": args.proof_log,
            "ha_proof_log": args.ha_proof_log,
            "ha_url": args.ha_url,
            "ha_entity_id": args.ha_entity_id,
            "proof_run_id": args.proof_run_id,
        },
        "allowances": {
            "allow_unobserved": args.allow_unobserved,
            "allow_different_run_ids": args.allow_different_run_ids,
            "allow_missing_timestamps": args.allow_missing_timestamps,
            "allow_out_of_order_phases": args.allow_out_of_order_phases,
            "allow_missing_ha_attributes": args.allow_missing_ha_attributes,
            "allow_missing_ha_mqtt_attributes": args.allow_missing_ha_mqtt_attributes,
            "allow_missing_ha_mqtt_topic": args.allow_missing_ha_mqtt_topic,
        },
    }


def write_audit_report(path, report):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def audit(args):
    checks = [validate_mqtt_proof(args), validate_ha_proof(args)]
    errors = []
    for check in checks:
        errors.extend(f"{check.name}: {error}" for error in check.errors)
    errors.extend(run_id_errors(checks, allow_different_run_ids=args.allow_different_run_ids))
    errors.extend(timestamp_errors(checks, allow_missing_timestamps=args.allow_missing_timestamps))
    errors.extend(
        phase_timing_errors(
            checks,
            allow_out_of_order_phases=args.allow_out_of_order_phases,
            allow_missing_timestamps=args.allow_missing_timestamps,
        )
    )
    errors.extend(
        ha_mqtt_topic_errors(
            args,
            checks[1],
            allow_missing_topic=args.allow_missing_ha_mqtt_topic,
        )
    )

    requirements = requirement_status(checks)
    for requirement, passed in requirements.items():
        if not passed:
            errors.append(f"{requirement} requirement is not proven by both proof logs")

    output_json = getattr(args, "output_json", None)
    if output_json:
        try:
            write_audit_report(output_json, audit_report(args, checks, requirements, errors))
        except OSError as exc:
            errors.append(f"could not write audit JSON report {output_json}: {exc}")

    print("Pesetech real-device proof audit")
    print()
    for check in checks:
        print_check(check)
        print()

    print("Objective requirements:")
    for requirement, passed in requirements.items():
        print(f"  - {requirement}: {'PASS' if passed else 'FAIL'}")

    if errors:
        print("\nFinal audit failed:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("\nFinal audit passed.")
    if args.allow_unobserved:
        print(
            "Technical Home Assistant/MQTT proof passed for on/off, brightness, and color temperature, "
            "but strict real-light visual proof is still required."
        )
    else:
        print("Home Assistant control of the real Pesetech skylight is proven for on/off, brightness, and color temperature.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Audit MQTT and Home Assistant proof logs for final Pesetech real-device proof.")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Gateway config path used for MQTT topic defaults.")
    parser.add_argument("--proof-log", default="docker/config/pesetech-proof.jsonl", help="MQTT smoke proof log.")
    parser.add_argument("--ha-proof-log", default="docker/config/pesetech-ha-service-proof.jsonl", help="Home Assistant service proof log.")
    parser.add_argument("--proof-run-id", "--run-id", dest="proof_run_id", default=None, help="Require this proof run id in both logs.")
    parser.add_argument("--discovery-prefix", default=None, help="MQTT discovery prefix.")
    parser.add_argument("--mesh-topic", default=None, help="Bridge MQTT topic/node id.")
    parser.add_argument("--device-id", default=None, help="Configured mesh device id.")
    parser.add_argument("--mqtt-brightness", type=int, default=32640, help="Raw MQTT brightness value used for the MQTT smoke proof.")
    parser.add_argument("--ha-url", default=ha_verify.DEFAULT_HA_URL, help="Home Assistant base URL used for the HA service proof.")
    parser.add_argument("--ha-entity-id", default=ha_verify.DEFAULT_ENTITY_ID, help="Home Assistant light entity id.")
    parser.add_argument("--ha-brightness", type=int, default=ha_verify.DEFAULT_BRIGHTNESS, help="Home Assistant brightness value used for the HA proof.")
    parser.add_argument("--ha-warm-kelvin", type=int, default=2200, help="Warm color temperature used for the HA proof.")
    parser.add_argument("--ha-cool-kelvin", type=int, default=6500, help="Cool color temperature used for the HA proof.")
    parser.add_argument("--ha-brightness-tolerance", type=int, default=2, help="Allowed HA brightness attribute difference.")
    parser.add_argument("--ha-kelvin-tolerance", type=int, default=150, help="Allowed HA color_temp_kelvin attribute difference.")
    parser.add_argument("--ha-mqtt-brightness-scale", type=int, default=ha_verify.DEFAULT_MQTT_BRIGHTNESS_SCALE, help="MQTT bridge brightness scale expected for HA proof verification.")
    parser.add_argument("--ha-mqtt-brightness-tolerance", type=int, default=ha_verify.DEFAULT_MQTT_BRIGHTNESS_TOLERANCE, help="Allowed MQTT bridge brightness difference in HA proof verification.")
    parser.add_argument("--ha-mqtt-mired-tolerance", type=int, default=ha_verify.DEFAULT_MQTT_MIRED_TOLERANCE, help="Allowed MQTT bridge color_temp mired difference in HA proof verification.")
    parser.add_argument("--allow-unobserved", action="store_true", help="Allow proof logs without interactive visual confirmations; not valid for final proof.")
    parser.add_argument("--allow-different-run-ids", action="store_true", help="Do not require both proof logs to share the same run_id.")
    parser.add_argument("--allow-missing-timestamps", action="store_true", help="Do not require timezone-aware parseable timestamps on proof events; not valid for final proof.")
    parser.add_argument("--allow-out-of-order-phases", action="store_true", help="Do not require HA service proof timestamps to occur after MQTT command proof timestamps; not valid for final proof.")
    parser.add_argument("--allow-missing-ha-attributes", action="store_true", help="Do not require brightness/color_temp_kelvin attributes in HA state.")
    parser.add_argument("--allow-missing-ha-mqtt-attributes", action="store_true", help="Do not require brightness/color_temp fields in MQTT state recorded by the HA service proof.")
    parser.add_argument("--allow-missing-ha-mqtt-topic", action="store_true", help="Do not require HA service proof MQTT state topic to match gateway config; not valid for final proof.")
    parser.add_argument("--output-json", help="Write a structured final-audit report to this JSON file.")
    return audit(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
