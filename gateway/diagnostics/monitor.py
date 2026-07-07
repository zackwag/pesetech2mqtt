import asyncio
import json
import logging
import os
import time
import uuid

from collections import Counter
from datetime import datetime, timezone
from enum import Enum

from .rotation import prune_extra_rotations, rotate_if_needed


def raw_payload_has_marker(marker):
    raw = os.environ.get("PESETECH_RAW_PAYLOAD", "")
    markers = raw.replace(",", " ").replace(";", " ").split()
    return marker in markers


class DiagnosticMonitor:
    """
    Best-effort JSONL diagnostics for Pesetech light control.

    The monitor is intentionally passive: it records existing MQTT commands,
    mesh writes, readbacks, and inbound mesh messages without sending any extra
    Bluetooth traffic or changing control behavior.
    """

    DEFAULT_MAX_BYTES = 5 * 1024 * 1024
    DEFAULT_MAX_FILES = 3

    def __init__(self, enabled=False, path="", summary_interval_seconds=60, max_bytes=None, max_files=None):
        self.enabled = bool(enabled)
        self.path = path or "/share/pesetech-command-monitor.jsonl"
        self.summary_interval_seconds = max(1, int(summary_interval_seconds or 60))
        self.max_bytes = int(max_bytes or self.DEFAULT_MAX_BYTES)
        self.max_files = int(max_files or self.DEFAULT_MAX_FILES)
        self.run_id = uuid.uuid4().hex
        self._inbound_counts = Counter()
        self._inbound_last = {}
        self._callbacks = []

    @classmethod
    def from_config(cls, config):
        def optional(path, fallback):
            try:
                return config.optional(path, fallback)
            except Exception:
                return fallback

        enabled = bool(optional("diagnostic_monitor.enabled", False))
        path = optional("diagnostic_monitor.path", "/share/pesetech-command-monitor.jsonl")
        interval = int(optional("diagnostic_monitor.summary_interval_seconds", 60))
        max_bytes = int(optional("diagnostic_monitor.max_bytes", cls.DEFAULT_MAX_BYTES))
        max_files = int(optional("diagnostic_monitor.max_files", cls.DEFAULT_MAX_FILES))

        env_enabled = os.environ.get("PESETECH_DIAGNOSTIC_MONITOR_ENABLED")
        if env_enabled is not None:
            enabled = env_enabled.strip().lower() == "true"

        # Compatibility path for already-installed add-ons whose Supervisor
        # schema does not yet accept diagnostic_monitor_enabled.
        if raw_payload_has_marker("diagnostic-monitor-enabled"):
            enabled = True

        env_path = os.environ.get("PESETECH_DIAGNOSTIC_MONITOR_PATH")
        if env_path:
            path = env_path

        env_interval = os.environ.get("PESETECH_DIAGNOSTIC_MONITOR_SUMMARY_INTERVAL_SECONDS")
        if env_interval:
            try:
                interval = int(env_interval)
            except ValueError:
                logging.warning(f"Ignoring invalid diagnostic monitor interval {env_interval!r}")

        return cls(
            enabled=enabled,
            path=path,
            summary_interval_seconds=interval,
            max_bytes=max_bytes,
            max_files=max_files,
        )

    def _now(self):
        return datetime.now(timezone.utc).isoformat()

    def _event_base(self, event):
        return {
            "ts": self._now(),
            "mono": round(time.monotonic(), 6),
            "event": event,
            "run_id": self.run_id,
        }

    def _json_safe(self, value):
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, bytes):
            return {"hex": value.hex()}
        if isinstance(value, Enum):
            return value.name
        if isinstance(value, datetime):
            return value.isoformat()
        if hasattr(value, "total_seconds"):
            try:
                return {"seconds": value.total_seconds()}
            except Exception:
                pass
        if isinstance(value, dict) or hasattr(value, "items"):
            try:
                return {str(key): self._json_safe(item) for key, item in value.items()}
            except Exception:
                return str(value)
        if isinstance(value, (list, tuple, set)):
            return [self._json_safe(item) for item in value]
        return str(value)

    def _write(self, data):
        if not self.enabled:
            return
        try:
            line = json.dumps(self._json_safe(data), sort_keys=True) + "\n"
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            prune_extra_rotations(self.path, self.max_files)
            rotate_if_needed(self.path, len(line.encode("utf-8")), self.max_bytes, self.max_files)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line)
        except Exception as exc:
            logging.warning(f"Diagnostic monitor failed to write event: {type(exc).__name__}: {exc}")

    def record(self, event, **fields):
        if not self.enabled:
            return
        data = self._event_base(event)
        data.update(fields)
        self._write(data)

    def command_id(self):
        return uuid.uuid4().hex[:12]

    def node_fields(self, node):
        config = getattr(node, "config", None)
        optional = getattr(config, "optional", None)
        return {
            "node_id": optional("id", "") if callable(optional) else "",
            "node_name": optional("name", "") if callable(optional) else "",
            "uuid": str(getattr(node, "uuid", "")),
            "unicast": f"{int(getattr(node, 'unicast', 0)):04x}" if hasattr(node, "unicast") else "",
        }

    def record_mqtt_light_command(self, command_id, node, topic, payload):
        fields = self.node_fields(node)
        fields.update(
            {
                "command_id": command_id,
                "topic": topic or "",
                "payload": payload,
            }
        )
        self.record("mqtt_light_command", **fields)

    def record_mqtt_light_route(self, command_id, node, route, **details):
        fields = self.node_fields(node)
        fields.update(
            {
                "command_id": command_id,
                "route": route,
                "details": details,
            }
        )
        self.record("mqtt_light_route", **fields)

    def record_ack_write(
        self,
        *,
        command_id=None,
        node=None,
        label,
        address,
        request_opcode,
        status_opcode,
        send_interval,
        timeout,
        attempts,
        elapsed_seconds,
        outcome,
        status_payload=None,
        exception=None,
    ):
        fields = self.node_fields(node) if node is not None else {}
        fields.update(
            {
                "command_id": command_id,
                "label": label,
                "address": f"{int(address):04x}",
                "request_opcode": self._opcode_name(request_opcode),
                "status_opcode": self._opcode_name(status_opcode),
                "send_interval": send_interval,
                "timeout": timeout,
                "attempts": attempts,
                "elapsed_seconds": round(elapsed_seconds, 6),
                "outcome": outcome,
            }
        )
        if status_payload is not None:
            fields["status_payload"] = status_payload
        if exception is not None:
            fields["exception"] = {
                "type": type(exception).__name__,
                "message": str(exception),
            }
        self.record("mesh_ack_write", **fields)

    def record_unack_write(
        self,
        *,
        command_id=None,
        node=None,
        label,
        address,
        opcode,
        retransmissions,
        send_interval,
    ):
        fields = self.node_fields(node) if node is not None else {}
        fields.update(
            {
                "command_id": command_id,
                "label": label,
                "address": f"{int(address):04x}",
                "opcode": self._opcode_name(opcode),
                "retransmissions": retransmissions,
                "send_interval": send_interval,
            }
        )
        self.record("mesh_unack_write", **fields)

    def record_readback(self, *, command_id=None, node=None, trigger, results, elapsed_seconds, outcome):
        fields = self.node_fields(node) if node is not None else {}
        fields.update(
            {
                "command_id": command_id,
                "trigger": trigger,
                "results": results,
                "elapsed_seconds": round(elapsed_seconds, 6),
                "outcome": outcome,
            }
        )
        self.record("standard_readback", **fields)

    def _opcode_name(self, opcode):
        return getattr(opcode, "name", str(opcode))

    def record_inbound(self, source, app_index, destination, message):
        if not self.enabled:
            return
        opcode = None
        try:
            opcode = message.get("opcode")
        except Exception:
            opcode = None
        key = (f"{int(source):04x}", self._opcode_name(opcode))
        self._inbound_counts[key] += 1
        self._inbound_last[key] = {
            "source": key[0],
            "app_index": app_index,
            "destination": f"{int(destination):04x}",
            "opcode": key[1],
            "message": message,
        }

    def flush_inbound_summary(self):
        if not self.enabled or not self._inbound_counts:
            return
        by_source_opcode = {
            f"{source}:{opcode}": count for (source, opcode), count in sorted(self._inbound_counts.items())
        }
        last_by_source_opcode = {
            f"{source}:{opcode}": value for (source, opcode), value in sorted(self._inbound_last.items())
        }
        self.record(
            "mesh_inbound_summary",
            interval_seconds=self.summary_interval_seconds,
            by_source_opcode=by_source_opcode,
            last_by_source_opcode=last_by_source_opcode,
        )
        self._inbound_counts.clear()
        self._inbound_last.clear()

    async def run(self):
        if not self.enabled:
            return
        logging.info(f"Diagnostic monitor enabled; writing JSONL to {self.path}")
        self.record("monitor_enabled", path=self.path, summary_interval_seconds=self.summary_interval_seconds)
        try:
            while True:
                await asyncio.sleep(self.summary_interval_seconds)
                self.flush_inbound_summary()
        except asyncio.CancelledError:
            self.flush_inbound_summary()
            self.record("gateway_stop")
            raise

    def attach_app(self, app):
        if not self.enabled:
            return
        self.record("gateway_start")
        for client, opcodes in self._client_opcodes(app):
            callbacks = getattr(client, "app_message_callbacks", None)
            if callbacks is None:
                continue
            for opcode in opcodes:
                try:
                    callback = self._make_inbound_callback()
                    callbacks[opcode].add(callback)
                    self._callbacks.append((callbacks[opcode], callback))
                except Exception as exc:
                    self.record(
                        "monitor_error",
                        stage="attach_app",
                        opcode=self._opcode_name(opcode),
                        exception={"type": type(exc).__name__, "message": str(exc)},
                    )

    def _make_inbound_callback(self):
        def app_message_received(source, app_index, destination, message):
            self.record_inbound(source, app_index, destination, message)
            return False

        return app_message_received

    def _client_opcodes(self, app):
        try:
            from bluetooth_mesh import models
            from bluetooth_mesh.messages.generic.onoff import GenericOnOffOpcode
            from bluetooth_mesh.messages.generic.light.ctl import LightCTLOpcode
            from bluetooth_mesh.messages.generic.light.lightness import LightLightnessOpcode
            from bluetooth_mesh.messages.time import TimeOpcode
        except Exception:
            return []

        element = app.elements[0]
        result = [
            (
                element[models.GenericOnOffClient],
                [GenericOnOffOpcode.GENERIC_ONOFF_STATUS],
            ),
            (
                element[models.LightLightnessClient],
                [LightLightnessOpcode.LIGHT_LIGHTNESS_STATUS],
            ),
            (
                element[models.LightCTLClient],
                [
                    LightCTLOpcode.LIGHT_CTL_STATUS,
                    LightCTLOpcode.LIGHT_CTL_TEMPERATURE_STATUS,
                    TimeOpcode.TIME_STATUS,
                ],
            ),
        ]
        return result
