import asyncio
import copy
import hashlib
import json
import math
import os
import tempfile
import time

from . import Module


DEFAULT_DAYS = [7, 1, 2, 3, 4, 5, 6]
VENDOR_SEND_INTERVAL = 0.075
VENDOR_RETRANSMISSIONS = 10
APP_PAYLOAD_DELAY_SECONDS = 0.5
PESETECH_VENDOR_OPCODE = 0xE31102
DEFAULT_PROGRAM_CONFIG_PATH = "/share/pesetech_skylight_programs.json"
SCENE_TYPE_IDS = {
    "1800K": 76,
    "2500K": 77,
    "2700K": 78,
    "3000K": 79,
    "4000K": 80,
    "5000K": 81,
    "6000K": 82,
    "7000K": 83,
    "10000K": 84,
}
SCENE_OPTIONS = list(SCENE_TYPE_IDS)


DEFAULT_PROGRAM_CONFIG = {
    "targets": [
        "Skylight A",
        "Skylight B",
    ],
    "dawn_fluctuation": {
        "enabled": True,
        "time": "07:45",
        "fade_minutes": 30,
        "brightness": 100,
        "scene": "3000K",
        "scene_type_id": 79,
        "days": DEFAULT_DAYS,
    },
    "phased_off": {
        "enabled": True,
        "time": "20:45",
        "fade_minutes": 30,
        "days": DEFAULT_DAYS,
    },
    "hcl": [
        {
            "enabled": True,
            "clock_id": 16,
            "time": "08:30",
            "brightness": 100,
            "scene": "6000K",
            "scene_type_id": 82,
            "days": DEFAULT_DAYS,
        },
        {
            "enabled": True,
            "clock_id": 19,
            "time": "09:00",
            "brightness": 100,
            "scene": "10000K",
            "scene_type_id": 84,
            "days": DEFAULT_DAYS,
        },
        {
            "enabled": True,
            "clock_id": 17,
            "time": "20:00",
            "brightness": 100,
            "scene": "7000K",
            "scene_type_id": 83,
            "days": DEFAULT_DAYS,
        },
        {
            "enabled": True,
            "clock_id": 18,
            "time": "21:00",
            "brightness": 100,
            "scene": "3000K",
            "scene_type_id": 79,
            "days": DEFAULT_DAYS,
        },
    ],
    "schedule": [
        {
            "enabled": True,
            "clock_id": 11,
            "time": "07:44",
            "power": True,
            "days": DEFAULT_DAYS,
        },
        {
            "enabled": True,
            "clock_id": 12,
            "time": "22:15",
            "power": False,
            "days": DEFAULT_DAYS,
        },
    ],
}


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enable", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disable", "disabled"}:
        return False
    return default


def _copy_default_config():
    return copy.deepcopy(DEFAULT_PROGRAM_CONFIG)


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def program_config_hash(config):
    return hashlib.sha256(_canonical_json(normalize_program_config(config)).encode("utf-8")).hexdigest()[:16]


def _bounded_int(value, label, minimum, maximum):
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return result


def _time_to_seconds(value):
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError(f"time must be HH:MM, got {value!r}")
    hour = _bounded_int(parts[0], "time hour", 0, 23)
    minute = _bounded_int(parts[1], "time minute", 0, 59)
    return hour * 3600 + minute * 60


def _normalize_time(value):
    seconds = _time_to_seconds(value)
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}"


def _days_to_mask(days):
    values = days if days is not None else DEFAULT_DAYS
    if not isinstance(values, list) or not values:
        raise ValueError("days must be a non-empty list of app day numbers")
    mask = 0
    for item in values:
        day = _bounded_int(item, "day", 1, 7)
        mask += int(math.pow(2, day - 1))
    return mask


def _normalize_targets(value):
    targets = value if isinstance(value, list) else DEFAULT_PROGRAM_CONFIG["targets"]
    result = [str(item).strip() for item in targets if str(item).strip()]
    if not result:
        raise ValueError("targets must include at least one light")
    return result


def _normalize_scene(entry, fallback):
    scene = str(entry.get("scene") or entry.get("scene_name") or fallback.get("scene") or "3000K").strip()
    if scene not in SCENE_TYPE_IDS:
        raise ValueError(f"scene must be one of {', '.join(SCENE_OPTIONS)}, got {scene!r}")
    return scene, SCENE_TYPE_IDS[scene]


def _normalize_days(_entry):
    return list(DEFAULT_DAYS)


def _normalize_dawn(entry, fallback):
    entry = entry or {}
    scene, scene_type_id = _normalize_scene(entry, fallback)
    result = {
        "enabled": _as_bool(entry.get("enabled"), _as_bool(fallback.get("enabled"), True)),
        "time": _normalize_time(entry.get("time", fallback["time"])),
        "fade_minutes": _bounded_int(entry.get("fade_minutes", fallback["fade_minutes"]), "fade_minutes", 0, 1092),
        "brightness": _bounded_int(entry.get("brightness", fallback["brightness"]), "brightness", 0, 100),
        "scene": scene,
        "scene_type_id": scene_type_id,
        "days": _normalize_days(entry),
    }
    if _time_to_seconds(result["time"]) - result["fade_minutes"] * 60 < 0:
        raise ValueError("dawn_fluctuation fade starts before midnight; use a later wake time or shorter fade")
    return result


def _normalize_phased(entry, fallback):
    entry = entry or {}
    return {
        "enabled": _as_bool(entry.get("enabled"), _as_bool(fallback.get("enabled"), True)),
        "time": _normalize_time(entry.get("time", fallback["time"])),
        "fade_minutes": _bounded_int(entry.get("fade_minutes", fallback["fade_minutes"]), "fade_minutes", 0, 1092),
        "days": _normalize_days(entry),
    }


def _normalize_hcl_row(entry, fallback):
    entry = entry or {}
    scene, scene_type_id = _normalize_scene(entry, fallback)
    return {
        "enabled": _as_bool(entry.get("enabled"), _as_bool(fallback.get("enabled"), True)),
        "clock_id": _bounded_int(fallback["clock_id"], "hcl clock_id", 0, 255),
        "time": _normalize_time(entry.get("time", fallback["time"])),
        "brightness": _bounded_int(entry.get("brightness", fallback["brightness"]), "brightness", 0, 100),
        "scene": scene,
        "scene_type_id": scene_type_id,
        "days": _normalize_days(entry),
    }


def _normalize_schedule_row(entry, fallback):
    entry = entry or {}
    power_default = _as_bool(fallback.get("power"), True)
    return {
        "enabled": _as_bool(entry.get("enabled"), _as_bool(fallback.get("enabled"), True)),
        "clock_id": _bounded_int(fallback["clock_id"], "schedule clock_id", 0, 255),
        "time": _normalize_time(entry.get("time", fallback["time"])),
        "power": _as_bool(entry.get("power"), power_default),
        "days": _normalize_days(entry),
    }


def _rows_by_clock_id(rows):
    result = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            result[int(row.get("clock_id"))] = row
        except (TypeError, ValueError):
            continue
    return result


def normalize_program_config(config=None):
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError("skylight programs config must be a JSON object")

    defaults = _copy_default_config()
    hcl_by_clock = _rows_by_clock_id(config.get("hcl") or [])
    schedule_by_clock = _rows_by_clock_id(config.get("schedule") or [])

    return {
        "targets": _normalize_targets(config.get("targets")),
        "dawn_fluctuation": _normalize_dawn(
            config.get("dawn_fluctuation") or config.get("dawn") or {},
            defaults["dawn_fluctuation"],
        ),
        "phased_off": _normalize_phased(
            config.get("phased_off") or config.get("phasedOff") or {},
            defaults["phased_off"],
        ),
        "hcl": [
            _normalize_hcl_row(hcl_by_clock.get(row["clock_id"], {}), row)
            for row in defaults["hcl"]
        ],
        "schedule": [
            _normalize_schedule_row(schedule_by_clock.get(row["clock_id"], {}), row)
            for row in defaults["schedule"]
        ],
    }


def load_program_config(path):
    if not path or not os.path.exists(path):
        return normalize_program_config()
    with open(path, "r", encoding="utf-8") as handle:
        return normalize_program_config(json.load(handle))


def save_program_config(path, config):
    if not path:
        raise ValueError("skylight programs config path is required")
    config = normalize_program_config(config)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".pesetech-skylight-programs.", suffix=".json", dir=directory or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(config, output, indent=2, sort_keys=True)
            output.write("\n")
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise
    return config


def _lightness_from_percent(percent):
    percent = _bounded_int(percent, "brightness", 0, 100)
    return int(math.ceil((percent * 65535.0) / 100.0))


def _le(value, width, label):
    value = _bounded_int(value, label, 0, (1 << (width * 8)) - 1)
    return value.to_bytes(width, "little")


def _clock_config_payload(clock_id, enabled, days, start_seconds):
    return (
        b"\xa1\xff"
        + _le(clock_id, 1, "clock_id")
        + _le(1 if enabled else 0, 1, "enabled")
        + _le(_days_to_mask(days), 1, "days mask")
        + _le(start_seconds, 4, "start seconds")
        + bytes.fromhex("0000000100")
    )


def _auto_config_payload(auto_index, enabled, days, start_seconds, fade_seconds):
    return (
        b"\xa1\xff"
        + _le(auto_index, 1, "auto_index")
        + _le(1 if enabled else 0, 1, "enabled")
        + _le(_days_to_mask(days), 1, "days mask")
        + _le(start_seconds, 4, "start seconds")
        + _le(fade_seconds, 2, "fade seconds")
        + bytes.fromhex("000100")
    )


def _runtime_payload(slot, brightness_percent):
    lightness = _lightness_from_percent(brightness_percent)
    return (
        b"\xa0\xff"
        + _le(slot, 1, "slot")
        + b"\x00\x00"
        + b"\x00\x00"
        + _le(lightness, 2, "lightness")
        + b"\x00\x00"
        + b"\x00\x00"
        + b"\x00\x00"
        + b"\x00\x00"
        + b"\x00\x00"
        + b"\x00\x00\x00\x00"
    )


def _feature_targets(config, feature):
    return feature.get("targets") or config.get("targets") or []


def _feature_enabled(feature):
    return _as_bool(feature.get("enabled"), True)


def _minutes_to_seconds(value, label):
    return _bounded_int(value, label, 0, 1092) * 60


def _command(label, kind, targets, payloads, desired):
    return {
        "label": label,
        "kind": kind,
        "targets": list(targets or []),
        "desired": desired,
        "payloads": [
            {
                "name": item["name"],
                "payload": item["payload"].hex(),
                "delay_after_seconds": item.get("delay_after_seconds", 0),
            }
            for item in payloads
        ],
        "_payloads": payloads,
    }


def build_program_commands(config):
    commands = []
    config = config or {}

    dawn = config.get("dawn_fluctuation") or config.get("dawn")
    if dawn:
        enabled = _feature_enabled(dawn)
        wake_seconds = _time_to_seconds(dawn["time"])
        fade_seconds = _minutes_to_seconds(dawn.get("fade_minutes", dawn.get("fadeOutTime", 30)), "fade_minutes")
        start_seconds = wake_seconds - fade_seconds
        if start_seconds < 0:
            raise ValueError("dawn_fluctuation fade starts before midnight; split it or use a shorter fade")
        brightness = dawn.get("brightness", dawn.get("brightness_percent", dawn.get("light", 100)))
        commands.append(
            _command(
                "Dawn Fluctuation",
                "dawn_fluctuation",
                _feature_targets(config, dawn),
                [
                    {
                        "name": "auto_program",
                        "payload": _auto_config_payload(0, enabled, dawn.get("days"), start_seconds, fade_seconds),
                        "delay_after_seconds": APP_PAYLOAD_DELAY_SECONDS,
                    },
                    {"name": "runtime_brightness", "payload": _runtime_payload(0, brightness)},
                ],
                {
                    "enabled": enabled,
                    "time": dawn["time"],
                    "fade_minutes": fade_seconds // 60,
                    "start_seconds": start_seconds,
                    "brightness": _bounded_int(brightness, "brightness", 0, 100),
                    "scene": dawn.get("scene") or dawn.get("scene_name"),
                    "scene_type_id": dawn.get("scene_type_id") or dawn.get("sceneTypeId"),
                    "days": dawn.get("days") or DEFAULT_DAYS,
                },
            )
        )

    phased = config.get("phased_off") or config.get("phasedOff")
    if phased:
        enabled = _feature_enabled(phased)
        fade_seconds = _minutes_to_seconds(
            phased.get("fade_minutes", phased.get("fadeOutTime", 30)),
            "fade_minutes",
        )
        commands.append(
            _command(
                "Phased OFF",
                "phased_off",
                _feature_targets(config, phased),
                [
                    {
                        "name": "auto_program",
                        "payload": _auto_config_payload(
                            10,
                            enabled,
                            phased.get("days"),
                            _time_to_seconds(phased["time"]),
                            fade_seconds,
                        ),
                    }
                ],
                {
                    "enabled": enabled,
                    "time": phased["time"],
                    "fade_minutes": fade_seconds // 60,
                    "days": phased.get("days") or DEFAULT_DAYS,
                },
            )
        )

    for index, entry in enumerate(config.get("hcl") or [], start=1):
        enabled = _feature_enabled(entry)
        clock_id = _bounded_int(entry.get("clock_id"), "hcl clock_id", 0, 255)
        brightness = entry.get("brightness", entry.get("brightness_percent", entry.get("light", 100)))
        commands.append(
            _command(
                f"HCL {index}",
                "hcl",
                _feature_targets(config, entry),
                [
                    {
                        "name": "clock",
                        "payload": _clock_config_payload(clock_id, enabled, entry.get("days"), _time_to_seconds(entry["time"])),
                        "delay_after_seconds": APP_PAYLOAD_DELAY_SECONDS,
                    },
                    {"name": "runtime_brightness", "payload": _runtime_payload(clock_id, brightness)},
                ],
                {
                    "enabled": enabled,
                    "time": entry["time"],
                    "clock_id": clock_id,
                    "brightness": _bounded_int(brightness, "brightness", 0, 100),
                    "scene": entry.get("scene") or entry.get("scene_name"),
                    "scene_type_id": entry.get("scene_type_id") or entry.get("sceneTypeId"),
                    "days": entry.get("days") or DEFAULT_DAYS,
                },
            )
        )

    for index, entry in enumerate(config.get("schedule") or [], start=1):
        enabled = _feature_enabled(entry)
        clock_id = _bounded_int(entry.get("clock_id"), "schedule clock_id", 0, 255)
        if "brightness" in entry or "brightness_percent" in entry or "light" in entry:
            brightness = entry.get("brightness", entry.get("brightness_percent", entry.get("light")))
        else:
            brightness = 100 if _as_bool(entry.get("power"), True) else 0
        commands.append(
            _command(
                f"Schedule {index}",
                "schedule",
                _feature_targets(config, entry),
                [
                    {
                        "name": "clock",
                        "payload": _clock_config_payload(clock_id, enabled, entry.get("days"), _time_to_seconds(entry["time"])),
                        "delay_after_seconds": APP_PAYLOAD_DELAY_SECONDS,
                    },
                    {"name": "runtime_brightness", "payload": _runtime_payload(clock_id, brightness)},
                ],
                {
                    "enabled": enabled,
                    "time": entry["time"],
                    "clock_id": clock_id,
                    "power": 1 if _bounded_int(brightness, "brightness", 0, 100) > 0 else 0,
                    "brightness": _bounded_int(brightness, "brightness", 0, 100),
                    "days": entry.get("days") or DEFAULT_DAYS,
                },
            )
        )

    return commands


class SkylightProgramsModule(Module):
    """
    Send the app-compatible Pesetech skylight schedule/program vendor payloads.
    """

    def __init__(self):
        super().__init__()
        self.report_path = ""
        self.default_config_path = ""

    def initialize(self, app, store, config):
        super().initialize(app, store, config)
        self.report_path = os.environ.get("PESETECH_SKYLIGHT_PROGRAMS_REPORT", "")
        self.default_config_path = os.environ.get(
            "PESETECH_SKYLIGHT_PROGRAMS_PATH",
            DEFAULT_PROGRAM_CONFIG_PATH,
        )

    def setup_cli(self, parser):
        parser.add_argument("--config", default=None)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--node", action="append", default=[])
        parser.add_argument("--report-output", default=None)

    def _node_matches(self, node, selectors):
        if not selectors:
            return True

        node_id = str(node.config.optional("id", "")).lower()
        node_name = str(node.config.optional("name", "")).lower()
        node_uuid = str(node.uuid).lower()
        node_unicast = f"{node.unicast:04x}"
        values = {node_id, node_name, node_uuid, node_unicast, f"0x{node_unicast}"}
        return any(str(selector).strip().lower() in values for selector in selectors)

    def _node_summary(self, node):
        return {
            "uuid": str(node.uuid),
            "id": node.config.optional("id", ""),
            "name": node.config.optional("name", ""),
            "type": node.type,
            "unicast": f"{node.unicast:04X}",
            "element_count": node.count,
        }

    def _command_matches_node(self, command, node):
        return self._node_matches(node, command.get("targets") or [])

    def _load_config(self, path):
        with open(path, "r", encoding="utf-8") as handle:
            config = json.load(handle)
        if not isinstance(config, dict):
            raise ValueError("skylight programs config must be a JSON object")
        return config

    def _public_commands(self, commands):
        return [
            {
                key: value
                for key, value in command.items()
                if key != "_payloads"
            }
            for command in commands
        ]

    async def _send_payload(self, address, payload):
        from bluetooth_mesh import models

        client = self.app.elements[0][models.LightLightnessClient]

        async def request():
            return await client.send_app(
                address,
                app_index=self.app.app_keys[0][0],
                opcode=PESETECH_VENDOR_OPCODE,
                params=payload,
            )

        return await client.repeat(
            request,
            retransmissions=VENDOR_RETRANSMISSIONS,
            send_interval=VENDOR_SEND_INTERVAL,
        )

    async def _run_command(self, node, command, dry_run):
        from bluetooth_mesh import models

        started = time.time()
        step = {
            "name": command["label"],
            "kind": command["kind"],
            "status": "pending",
            "elapsed_seconds": None,
            "desired": command["desired"],
            "opcode": f"0x{PESETECH_VENDOR_OPCODE:X}",
            "payloads": [
                {
                    "name": payload["name"],
                    "payload": payload["payload"].hex(),
                    "delay_after_seconds": payload.get("delay_after_seconds", 0),
                }
                for payload in command["_payloads"]
            ],
        }
        try:
            address = node._model_address(models.LightLightnessServer)
            step["target_address"] = f"{address:04X}"
            if not dry_run:
                for payload in command["_payloads"]:
                    await self._send_payload(address, payload["payload"])
                    delay = float(payload.get("delay_after_seconds") or 0)
                    if delay:
                        await asyncio.sleep(delay)
            step["status"] = "dry-run" if dry_run else "passed"
        except Exception as exc:
            step["status"] = "failed"
            step["error"] = f"{type(exc).__name__}: {exc}"
        step["elapsed_seconds"] = round(time.time() - started, 3)
        return step

    async def _run_node(self, node, commands, dry_run):
        entry = {
            **self._node_summary(node),
            "status": "pending",
            "steps": [],
        }
        selected = [command for command in commands if self._command_matches_node(command, node)]
        if not selected:
            entry["status"] = "skipped"
            return entry

        try:
            await asyncio.wait_for(node.bind(self.app), timeout=20.0)
        except Exception as exc:
            entry["status"] = "failed"
            entry["error"] = f"bind failed: {type(exc).__name__}: {exc}"
            return entry

        for command in selected:
            entry["steps"].append(await self._run_command(node, command, dry_run))

        statuses = [step["status"] for step in entry["steps"]]
        if any(status == "failed" for status in statuses):
            entry["status"] = "failed"
        elif all(status == "dry-run" for status in statuses):
            entry["status"] = "dry-run"
        else:
            entry["status"] = "passed"
        return entry

    def _write_report(self, path, payload):
        if not path:
            return
        with open(path, "w", encoding="utf-8") as output:
            json.dump(payload, output, indent=2, sort_keys=True)
            output.write("\n")

    async def run_config(self, config, dry_run=False, selectors=None, config_path=None):
        from mesh.nodes.light import Light

        started = time.time()
        config = normalize_program_config(config)
        selectors = [item for item in selectors or [] if str(item).strip()]
        payload = {
            "operation": "skylight-programs",
            "status": "pending",
            "started_at": started,
            "finished_at": None,
            "config_path": config_path,
            "config_hash": program_config_hash(config),
            "dry_run": bool(dry_run),
            "sent_light_commands": not bool(dry_run),
            "published_mqtt": False,
            "commands": [],
            "nodes": [],
        }

        try:
            commands = build_program_commands(config)
            payload["commands"] = self._public_commands(commands)
            if not commands:
                raise ValueError("skylight programs config did not define any commands")

            nodes = [
                node
                for node in self.app.nodes.all()
                if isinstance(node, Light) and self._node_matches(node, selectors)
            ]
            if not nodes:
                raise ValueError("No configured light nodes matched the requested selector.")

            for node in nodes:
                payload["nodes"].append(await self._run_node(node, commands, bool(dry_run)))

            statuses = [node["status"] for node in payload["nodes"] if node["status"] != "skipped"]
            if not statuses:
                payload["status"] = "skipped"
            elif any(status == "failed" for status in statuses):
                payload["status"] = "failed"
            elif all(status == "dry-run" for status in statuses):
                payload["status"] = "dry-run"
            else:
                payload["status"] = "passed"
        except Exception as exc:
            payload["status"] = "failed"
            payload["error"] = f"{type(exc).__name__}: {exc}"

        payload["finished_at"] = time.time()
        return payload

    async def handle_cli(self, args):
        config_path = args.config or self.default_config_path
        output_path = args.report_output or self.report_path
        payload = await self.run_config(
            self._load_config(config_path),
            dry_run=bool(args.dry_run),
            selectors=args.node,
            config_path=config_path,
        )
        self._write_report(output_path, payload)

        for command in payload.get("commands") or []:
            print(f"Skylight program {command['label']}: {command['kind']}", flush=True)
            for item in command.get("payloads") or []:
                print(f"  {item['name']}: {item['payload']}", flush=True)
        for node in payload.get("nodes") or []:
            print(
                f"Skylight programs {node.get('id') or node['uuid']}@{node['unicast']}: {node['status']}",
                flush=True,
            )
            for step in node.get("steps", []):
                if step["status"] in {"passed", "dry-run"}:
                    print(f"  {step['name']}: {step['status']} in {step['elapsed_seconds']}s", flush=True)
                else:
                    print(f"  {step['name']}: failed in {step['elapsed_seconds']}s: {step.get('error', '')}", flush=True)

        if output_path:
            print(f"Wrote skylight programs report to {output_path}", flush=True)
        if payload["status"] in {"passed", "dry-run", "skipped"}:
            print("Skylight programs completed.", flush=True)
            return
        raise RuntimeError(payload.get("error") or "One or more skylight program steps failed.")


async def apply_program_config(app, config, dry_run=False, selectors=None, config_path=None):
    runner = SkylightProgramsModule()
    runner.app = app
    return await runner.run_config(config, dry_run=dry_run, selectors=selectors, config_path=config_path)
