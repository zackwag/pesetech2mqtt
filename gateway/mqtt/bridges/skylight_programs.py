import asyncio
import copy
import json
import logging
import os
import time

from dataclasses import dataclass

from modules.skylight_programs import (
    DEFAULT_PROGRAM_CONFIG_PATH,
    SCENE_OPTIONS,
    apply_program_config,
    load_program_config,
    normalize_program_config,
    program_config_hash,
    save_program_config,
)


APPLY_DEBOUNCE_SECONDS = 1.0
DEVICE_OBJECT_ID = "pesetech_skylight_programs"
STATUS_OBJECT_ID = "pesetech_skylight_programs_status"
BUTTON_OBJECT_ID = "pesetech_skylight_reinforce_now"


@dataclass(frozen=True)
class ProgramEntity:
    component: str
    object_id: str
    name: str
    path: tuple
    value_type: str
    minimum: int = None
    maximum: int = None
    step: int = None
    options: tuple = None


def _program_entities():
    entities = [
        ProgramEntity(
            "switch",
            "pesetech_skylight_dawn_fluctuation_enabled",
            "Dawn Fluctuation Enabled",
            ("dawn_fluctuation", "enabled"),
            "bool",
        ),
        ProgramEntity(
            "text",
            "pesetech_skylight_dawn_fluctuation_wake_time",
            "Dawn Fluctuation Wake Time",
            ("dawn_fluctuation", "time"),
            "time",
        ),
        ProgramEntity(
            "number",
            "pesetech_skylight_dawn_fluctuation_fade_minutes",
            "Dawn Fluctuation Fade Minutes",
            ("dawn_fluctuation", "fade_minutes"),
            "int",
            0,
            1092,
            1,
        ),
        ProgramEntity(
            "number",
            "pesetech_skylight_dawn_fluctuation_brightness",
            "Dawn Fluctuation Brightness",
            ("dawn_fluctuation", "brightness"),
            "int",
            0,
            100,
            1,
        ),
        ProgramEntity(
            "select",
            "pesetech_skylight_dawn_fluctuation_cct_scene",
            "Dawn Fluctuation CCT Scene",
            ("dawn_fluctuation", "scene"),
            "select",
            options=tuple(SCENE_OPTIONS),
        ),
        ProgramEntity(
            "switch",
            "pesetech_skylight_phased_off_enabled",
            "Phased OFF Enabled",
            ("phased_off", "enabled"),
            "bool",
        ),
        ProgramEntity(
            "text",
            "pesetech_skylight_phased_off_start_time",
            "Phased OFF Start Time",
            ("phased_off", "time"),
            "time",
        ),
        ProgramEntity(
            "number",
            "pesetech_skylight_phased_off_fade_minutes",
            "Phased OFF Fade Minutes",
            ("phased_off", "fade_minutes"),
            "int",
            0,
            1092,
            1,
        ),
    ]

    for index in range(4):
        row = index + 1
        prefix = f"pesetech_skylight_hcl_{row}"
        name = f"HCL {row}"
        entities.extend(
            [
                ProgramEntity("switch", f"{prefix}_enabled", f"{name} Enabled", ("hcl", index, "enabled"), "bool"),
                ProgramEntity("text", f"{prefix}_time", f"{name} Time", ("hcl", index, "time"), "time"),
                ProgramEntity(
                    "number",
                    f"{prefix}_brightness",
                    f"{name} Brightness",
                    ("hcl", index, "brightness"),
                    "int",
                    0,
                    100,
                    1,
                ),
                ProgramEntity(
                    "select",
                    f"{prefix}_cct_scene",
                    f"{name} CCT Scene",
                    ("hcl", index, "scene"),
                    "select",
                    options=tuple(SCENE_OPTIONS),
                ),
            ]
        )

    schedule_rows = [
        ("morning_on", "Morning ON", 0),
        ("night_off", "Night OFF", 1),
    ]
    for key, name, index in schedule_rows:
        prefix = f"pesetech_skylight_schedule_{key}"
        entities.extend(
            [
                ProgramEntity("switch", f"{prefix}_enabled", f"Schedule {name} Enabled", ("schedule", index, "enabled"), "bool"),
                ProgramEntity("text", f"{prefix}_time", f"Schedule {name} Time", ("schedule", index, "time"), "time"),
                ProgramEntity("switch", f"{prefix}_power", f"Schedule {name} Power", ("schedule", index, "power"), "bool"),
            ]
        )

    return entities


PROGRAM_ENTITIES = _program_entities()


class SkylightProgramsMqttBridge:
    def __init__(self, messenger):
        self._messenger = messenger
        self._app = None
        self._config_path = None
        self._program_config = None
        self._apply_task = None
        self._apply_lock = asyncio.Lock()
        self._dirty = False
        self._pending_reason = "startup"
        self._last_report = None
        self._status = {
            "state": "idle",
            "message": "Ready",
            "last_apply_started_at": None,
            "last_apply_finished_at": None,
            "last_error": "",
        }
        self._entities_by_topic = {
            (entity.component, entity.object_id): entity
            for entity in PROGRAM_ENTITIES
        }

    def shutdown(self):
        if self._apply_task is not None:
            self._apply_task.cancel()

    def _programs_config_path(self):
        if os.environ.get("PESETECH_SKYLIGHT_PROGRAMS_PATH"):
            return os.environ["PESETECH_SKYLIGHT_PROGRAMS_PATH"]

        section = self._messenger.config.optional("skylight_programs", {}) or {}
        if isinstance(section, dict) and section.get("path"):
            return str(section["path"])

        return DEFAULT_PROGRAM_CONFIG_PATH

    def _device(self):
        return {
            "identifiers": [DEVICE_OBJECT_ID],
            "name": "Pesetech Skylight Programs",
            "manufacturer": "Pesetech/Lepu",
            "model": "Artificial Skylight Program Controller",
        }

    def _origin(self):
        return {
            "name": "pesetech-home-assistant",
            "support_url": "https://github.com/hrdwdmrbl/pesetech-home-assistant",
        }

    def _base_config(self, entity, name=None):
        return {
            "~": self._messenger.node_topic(entity.component, entity.object_id),
            "name": name or entity.name,
            "unique_id": f"{self._messenger.topic}_{entity.object_id}",
            "default_entity_id": f"{entity.component}.{entity.object_id}",
            "device": self._device(),
            "origin": self._origin(),
        }

    def _status_config(self):
        return {
            "~": self._messenger.node_topic("sensor", STATUS_OBJECT_ID),
            "name": "Program Apply Status",
            "unique_id": f"{self._messenger.topic}_{STATUS_OBJECT_ID}",
            "default_entity_id": f"sensor.{STATUS_OBJECT_ID}",
            "state_topic": "~/state",
            "json_attributes_topic": "~/attributes",
            "icon": "mdi:calendar-sync",
            "device": self._device(),
            "origin": self._origin(),
        }

    def _button_config(self):
        return {
            "~": self._messenger.node_topic("button", BUTTON_OBJECT_ID),
            "name": "Reinforce Now",
            "unique_id": f"{self._messenger.topic}_{BUTTON_OBJECT_ID}",
            "default_entity_id": f"button.{BUTTON_OBJECT_ID}",
            "command_topic": "~/set",
            "payload_press": "PRESS",
            "device": self._device(),
            "origin": self._origin(),
        }

    async def _publish_discovery(self):
        for entity in PROGRAM_ENTITIES:
            message = self._base_config(entity)
            message["command_topic"] = "~/set"
            message["state_topic"] = "~/state"

            if entity.component == "switch":
                message["payload_on"] = "ON"
                message["payload_off"] = "OFF"
                message["state_on"] = "ON"
                message["state_off"] = "OFF"
            elif entity.component == "number":
                message["min"] = entity.minimum
                message["max"] = entity.maximum
                message["step"] = entity.step
                message["mode"] = "box"
            elif entity.component == "text":
                message["mode"] = "text"
                message["min"] = 0
                message["max"] = 5
                message["pattern"] = "^([01][0-9]|2[0-3]):[0-5][0-9]$"
            elif entity.component == "select":
                message["options"] = list(entity.options)

            await self._messenger.publish(entity.component, entity.object_id, "config", message, retain=True)

        await self._messenger.publish("button", BUTTON_OBJECT_ID, "config", self._button_config(), retain=True)
        await self._messenger.publish("sensor", STATUS_OBJECT_ID, "config", self._status_config(), retain=True)

    async def clear_discovery(self):
        for entity in PROGRAM_ENTITIES:
            await self._messenger.publish(entity.component, entity.object_id, "config", "", retain=True)
        await self._messenger.publish("button", BUTTON_OBJECT_ID, "config", "", retain=True)
        await self._messenger.publish("sensor", STATUS_OBJECT_ID, "config", "", retain=True)

    def _read_path(self, path):
        value = self._program_config
        for part in path:
            value = value[part]
        return value

    def _write_path(self, config, path, value):
        target = config
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value

    def _parse_command(self, entity, payload):
        text = payload.decode("utf-8", errors="replace").strip()
        if entity.value_type == "bool":
            value = text.upper()
            if value in {"ON", "1", "TRUE"}:
                return True
            if value in {"OFF", "0", "FALSE"}:
                return False
            raise ValueError(f"{entity.object_id} expected ON or OFF, got {text!r}")
        if entity.value_type == "int":
            value = int(float(text))
            if entity.minimum is not None and value < entity.minimum:
                raise ValueError(f"{entity.object_id} must be at least {entity.minimum}")
            if entity.maximum is not None and value > entity.maximum:
                raise ValueError(f"{entity.object_id} must be at most {entity.maximum}")
            return value
        if entity.value_type == "select":
            if text not in entity.options:
                raise ValueError(f"{entity.object_id} expected one of {', '.join(entity.options)}, got {text!r}")
            return text
        if entity.value_type == "time":
            return text
        raise ValueError(f"{entity.object_id} has unknown value type {entity.value_type!r}")

    async def _publish_entity_state(self, entity):
        value = self._read_path(entity.path)
        if entity.component == "switch":
            state = "ON" if value else "OFF"
        else:
            state = value
        await self._messenger.publish(entity.component, entity.object_id, "state", state, retain=True)

    async def _publish_all_states(self):
        for entity in PROGRAM_ENTITIES:
            await self._publish_entity_state(entity)
        await self._publish_status()

    def _compact_report(self, report):
        if not report:
            return None
        return {
            "operation": report.get("operation"),
            "status": report.get("status"),
            "dry_run": report.get("dry_run"),
            "sent_light_commands": report.get("sent_light_commands"),
            "config_hash": report.get("config_hash"),
            "nodes": [
                {
                    "id": node.get("id"),
                    "name": node.get("name"),
                    "unicast": node.get("unicast"),
                    "status": node.get("status"),
                    "failed_steps": [
                        step.get("name")
                        for step in node.get("steps") or []
                        if step.get("status") == "failed"
                    ],
                }
                for node in report.get("nodes") or []
            ],
        }

    async def _publish_status(self):
        config_hash = ""
        try:
            config_hash = program_config_hash(self._program_config)
        except Exception:
            logging.exception("Failed to hash Pesetech skylight program config")

        attrs = {
            "message": self._status.get("message", ""),
            "config_path": self._config_path,
            "config_hash": config_hash,
            "last_apply_started_at": self._status.get("last_apply_started_at"),
            "last_apply_finished_at": self._status.get("last_apply_finished_at"),
            "last_error": self._status.get("last_error", ""),
            "last_report": self._compact_report(self._last_report),
        }
        await self._messenger.publish("sensor", STATUS_OBJECT_ID, "state", self._status.get("state", "unknown"), retain=True)
        await self._messenger.publish("sensor", STATUS_OBJECT_ID, "attributes", attrs, retain=True)

    async def _set_status(self, state, message="", error=""):
        self._status["state"] = state
        self._status["message"] = message
        self._status["last_error"] = error
        await self._publish_status()

    async def _load_or_seed_config(self):
        self._config_path = self._programs_config_path()
        missing = not os.path.exists(self._config_path)
        self._program_config = load_program_config(self._config_path)
        if missing:
            self._program_config = save_program_config(self._config_path, self._program_config)

    async def _handle_entity_command(self, entity, payload):
        try:
            value = self._parse_command(entity, payload)
            next_config = copy.deepcopy(self._program_config)
            self._write_path(next_config, entity.path, value)
            next_config = normalize_program_config(next_config)
            self._program_config = save_program_config(self._config_path, next_config)
        except Exception as exc:
            logging.exception("Invalid Pesetech skylight program command for %s", entity.object_id)
            await self._set_status("invalid", f"Rejected {entity.name}", f"{type(exc).__name__}: {exc}")
            await self._publish_entity_state(entity)
            return

        await self._publish_all_states()
        self._schedule_apply(f"changed {entity.object_id}", APPLY_DEBOUNCE_SECONDS)

    def _schedule_apply(self, reason, delay):
        self._dirty = True
        self._pending_reason = reason
        if self._apply_task is None or self._apply_task.done():
            self._apply_task = asyncio.create_task(self._apply_loop(delay))

    async def _apply_loop(self, delay):
        next_delay = delay
        while True:
            if next_delay:
                await asyncio.sleep(next_delay)
            self._dirty = False
            await self._apply_now(self._pending_reason)
            if not self._dirty:
                break
            next_delay = APPLY_DEBOUNCE_SECONDS

    async def _apply_now(self, reason):
        async with self._apply_lock:
            started = time.time()
            self._status["last_apply_started_at"] = started
            await self._set_status("applying", reason, "")
            try:
                report = await apply_program_config(
                    self._app,
                    copy.deepcopy(self._program_config),
                    dry_run=False,
                    selectors=None,
                    config_path=self._config_path,
                )
                self._last_report = report
                self._status["last_apply_finished_at"] = time.time()
                if report.get("status") == "passed":
                    await self._set_status("passed", "Skylight programs applied", "")
                else:
                    error = report.get("error") or f"skylight-programs status {report.get('status')}"
                    await self._set_status("failed", "Skylight programs apply failed", error)
            except Exception as exc:
                logging.exception("Failed to apply Pesetech skylight programs")
                self._status["last_apply_finished_at"] = time.time()
                await self._set_status("failed", "Skylight programs apply failed", f"{type(exc).__name__}: {exc}")

    async def _handle_message(self, message):
        parts = message.topic.split("/")
        if len(parts) < 5:
            return

        component = parts[-4]
        object_id = parts[-2]
        suffix = parts[-1]
        if suffix != "set":
            return

        if component == "button" and object_id == BUTTON_OBJECT_ID:
            self._schedule_apply("manual reinforce", 0)
            return

        entity = self._entities_by_topic.get((component, object_id))
        if entity is None:
            return
        await self._handle_entity_command(entity, message.payload)

    async def listen(self, app):
        self._app = app
        try:
            await self._load_or_seed_config()
        except Exception as exc:
            logging.exception("Failed to load Pesetech skylight program config")
            self._program_config = normalize_program_config()
            self._status["state"] = "failed"
            self._status["message"] = "Failed to load program config"
            self._status["last_error"] = f"{type(exc).__name__}: {exc}"

        await self._publish_discovery()
        await self._publish_all_states()

        topic = f"{self._messenger.discovery_prefix}/+/{self._messenger.topic}/+/set"
        async with self._messenger.client.filtered_messages(topic) as messages:
            await self._messenger.client.subscribe(topic)
            logging.info("Subscribed MQTT command topics for Pesetech skylight programs")
            async for message in messages:
                await self._handle_message(message)
