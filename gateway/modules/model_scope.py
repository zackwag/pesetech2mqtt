import asyncio
import json
import os
import time

from bluetooth_mesh import models
from bluetooth_mesh.messages.generic.light.ctl import LightCTLOpcode
from bluetooth_mesh.messages.time import TimeOpcode
from bluetooth_mesh.models.base import Model

from mesh.nodes.light import Light, LightCTLTemperatureServer
from mesh.nodes.light import PESETECH_READ_ATTEMPTS
from mesh.nodes.light import PESETECH_READ_RETRY_DELAY
from mesh.nodes.light import PESETECH_READ_TIMEOUT

from . import Module


SCHEDULER_GET = 0x4982
SCHEDULER_STATUS = 0x4A82
SCHEDULER_ACTION_GET = 0x4882
SCHEDULER_ACTION_STATUS = 0x5F


class SchedulerServer:
    MODEL_ID = (None, 0x1206)


class SchedulerSetupServer:
    MODEL_ID = (None, 0x1207)


class SchedulerClient(Model):
    MODEL_ID = (None, 0x1208)
    OPCODES = {SCHEDULER_STATUS, SCHEDULER_ACTION_STATUS}
    PUBLISH = True
    SUBSCRIBE = True

    def expect_raw_app(self, source, app_index, opcode, destination=None):
        future = asyncio.Future()

        def app_message_received(_source, _app_index, _destination, message):
            if _source != source or _app_index != app_index:
                return False
            if destination is not None and _destination != destination:
                return False
            if message["opcode"] != opcode:
                return False
            if not future.done():
                future.set_result(message)
            return True

        self.app_message_callbacks[opcode].add(app_message_received)
        return future


models.LightCTLClient.OPCODES = set(models.LightCTLClient.OPCODES) | {
    TimeOpcode.TIME_STATUS,
    TimeOpcode.TIME_ROLE_STATUS,
    SCHEDULER_STATUS,
    SCHEDULER_ACTION_STATUS,
}


MODEL_NAMES = {
    "0000": "Config Server",
    "0002": "Health Server",
    "0003": "Health Client",
    "1000": "Generic OnOff Server",
    "1002": "Generic Level Server",
    "1004": "Generic Default Transition Time Server",
    "1006": "Generic Power OnOff Server",
    "1007": "Generic Power OnOff Setup Server",
    "1200": "Time Server",
    "1201": "Time Setup Server",
    "1206": "Scheduler Server",
    "1207": "Scheduler Setup Server",
    "1300": "Light Lightness Server",
    "1301": "Light Lightness Setup Server",
    "1303": "Light CTL Server",
    "1304": "Light CTL Setup Server",
    "1306": "Light CTL Temperature Server",
    "00000211": "Pesetech/Telink Vendor Model",
}

SCHEDULER_ACTION_NAMES = {
    0: "off",
    1: "on",
    2: "scene",
    15: "none",
}


class ModelScopeModule(Module):
    """
    Probe the real light's readable Bluetooth Mesh model surface.
    """

    def __init__(self):
        super().__init__()
        self.report_path = ""

    def initialize(self, app, store, config):
        super().initialize(app, store, config)
        self.report_path = os.environ.get("PESETECH_MODEL_SCOPE_REPORT", "")

    def setup_cli(self, parser):
        parser.add_argument("--timeout", type=float, default=20.0)
        parser.add_argument("--attempts", type=int, default=PESETECH_READ_ATTEMPTS)
        parser.add_argument("--retry-timeout", type=float, default=PESETECH_READ_TIMEOUT)
        parser.add_argument("--retry-delay", type=float, default=PESETECH_READ_RETRY_DELAY)
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

    def _known_models(self, node):
        imported = node.imported_models if isinstance(node.imported_models, dict) else {}
        items = []
        for model_id, address in sorted(imported.items()):
            model_key = str(model_id).replace("0x", "").replace("0X", "").upper()
            try:
                address = int(address)
            except (TypeError, ValueError):
                continue
            items.append(
                {
                    "model_id": model_key,
                    "name": MODEL_NAMES.get(model_key, "Unknown/unsupported model"),
                    "address": f"{address:04X}",
                    "kind": "vendor" if len(model_key) > 4 else "sig",
                }
            )
        return items

    def _retained_state(self, node):
        state = {}
        if Light.OnOffProperty in node._retained:
            state["onoff"] = bool(node._retained[Light.OnOffProperty])
        if Light.BrightnessProperty in node._retained:
            state["brightness"] = node._retained[Light.BrightnessProperty]
        if Light.TemperatureProperty in node._retained:
            mireds = node._retained[Light.TemperatureProperty]
            state["color_temp_mireds"] = mireds
            try:
                state["color_temp_kelvin"] = 1000000 // int(mireds)
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        return state

    def _light_ctl_status_read(self, address):
        async def read(timeout):
            client = self.app.elements[0][models.LightCTLClient]
            app_index = self.app.app_keys[0][0]
            status = client.expect_app(
                source=address,
                app_index=app_index,
                destination=None,
                opcode=LightCTLOpcode.LIGHT_CTL_STATUS,
                params=dict(),
            )

            async def request():
                return await client.send_app(
                    address,
                    app_index=app_index,
                    opcode=LightCTLOpcode.LIGHT_CTL_GET,
                    params=dict(),
                )

            message = await client.query(request, status, send_interval=0.5, timeout=timeout)
            return message[LightCTLOpcode.LIGHT_CTL_STATUS.name.lower()]

        return read

    def _light_ctl_temperature_read(self, address):
        async def read(timeout):
            client = self.app.elements[0][models.LightCTLClient]
            state = await client.get_ctl([address], self.app.app_keys[0][0], timeout=timeout)
            result = state.get(address) if isinstance(state, dict) else None
            if result is None:
                raise TimeoutError(f"No Light CTL Temperature Status reply from {address:04x}; result was {state!r}")
            if isinstance(result, BaseException):
                raise result
            return result

        return read

    def _expect_raw_app(self, client, source, app_index, opcode, destination=None):
        future = asyncio.Future()

        def app_message_received(_source, _app_index, _destination, message):
            if _source != source or _app_index != app_index:
                return False
            if destination is not None and _destination != destination:
                return False
            if message["opcode"] != opcode:
                return False
            if not future.done():
                future.set_result(message)
            return True

        client.app_message_callbacks[opcode].add(app_message_received)
        return future

    def _time_read(self, address):
        async def read(timeout):
            client = self.app.elements[0][models.LightCTLClient]
            app_index = self.app.app_keys[0][0]
            status = client.expect_app(
                source=address,
                app_index=app_index,
                destination=None,
                opcode=TimeOpcode.TIME_STATUS,
                params=dict(),
            )

            async def request():
                return await client.send_app(
                    address,
                    app_index=app_index,
                    opcode=TimeOpcode.TIME_GET,
                    params=dict(),
                )

            message = await client.query(request, status, send_interval=0.5, timeout=timeout)
            return message[TimeOpcode.TIME_STATUS.name.lower()]

        return read

    def _time_role_read(self, address):
        async def read(timeout):
            client = self.app.elements[0][models.LightCTLClient]
            app_index = self.app.app_keys[0][0]
            status = client.expect_app(
                source=address,
                app_index=app_index,
                destination=None,
                opcode=TimeOpcode.TIME_ROLE_STATUS,
                params=dict(),
            )

            async def request():
                return await client.send_app(
                    address,
                    app_index=app_index,
                    opcode=TimeOpcode.TIME_ROLE_GET,
                    params=dict(),
                )

            message = await client.query(request, status, send_interval=0.5, timeout=timeout)
            role = message[TimeOpcode.TIME_ROLE_STATUS.name.lower()]["time_role"]
            return role.name.lower() if hasattr(role, "name") else str(role)

        return read

    def _scheduler_status_read(self, address):
        async def read(timeout):
            client = self.app.elements[0][models.LightCTLClient]
            app_index = self.app.app_keys[0][0]
            status = self._expect_raw_app(client, address, app_index, SCHEDULER_STATUS)

            async def request():
                return await client.send_app(address, app_index=app_index, opcode=SCHEDULER_GET, params=b"")

            message = await client.query(request, status, send_interval=0.5, timeout=timeout)
            params = bytes(message.get("params") or b"")
            bitmask = int.from_bytes(params, "little") if params else 0
            return {
                "raw": params.hex(),
                "schedule_bitmask": bitmask,
                "active_indices": [index for index in range(16) if bitmask & (1 << index)],
            }

        return read

    def _scheduler_action_read(self, address, index):
        async def read(timeout):
            client = self.app.elements[0][models.LightCTLClient]
            app_index = self.app.app_keys[0][0]
            status = self._expect_raw_app(client, address, app_index, SCHEDULER_ACTION_STATUS)

            async def request():
                return await client.send_app(
                    address,
                    app_index=app_index,
                    opcode=SCHEDULER_ACTION_GET,
                    params=bytes([index]),
                )

            message = await client.query(request, status, send_interval=0.5, timeout=timeout)
            params = bytes(message.get("params") or b"")
            return self._parse_scheduler_action(params)

        return read

    def _parse_scheduler_action(self, data):
        if len(data) != 10:
            raise ValueError(f"Scheduler Action Status must be 10 bytes; got {len(data)} bytes: {data.hex()}")
        index = data[0] & 0x0F
        year = ((data[0] >> 4) & 0x0F) | ((data[1] & 0x07) << 4)
        month = ((data[1] >> 3) & 0x1F) | ((data[2] & 0x7F) << 5)
        day = ((data[2] >> 7) & 0x01) | ((data[3] & 0x0F) << 1)
        hour = ((data[3] >> 4) & 0x0F) | ((data[4] & 0x01) << 4)
        minute = (data[4] >> 1) & 0x3F
        second = ((data[4] >> 7) & 0x01) | ((data[5] & 0x1F) << 1)
        week = ((data[5] >> 5) & 0x07) | ((data[6] & 0x0F) << 3)
        action = (data[6] >> 4) & 0x0F
        transition_time = data[7]
        scene_id = int.from_bytes(data[8:10], "little")
        return {
            "raw": data.hex(),
            "index": index,
            "year": year,
            "month_mask": f"0x{month:03X}",
            "day": day,
            "hour": hour,
            "minute": minute,
            "second": second,
            "week_mask": f"0x{week:02X}",
            "action": action,
            "action_name": SCHEDULER_ACTION_NAMES.get(action, f"unknown_{action}"),
            "transition_time_raw": transition_time,
            "scene_id": scene_id,
        }

    async def _run_retry_step(self, node, label, read, attempts, timeout, retry_delay, required=False):
        step_started = time.time()
        attempt_results = []
        max_attempts = max(1, int(attempts))

        for attempt in range(1, max_attempts + 1):
            started = time.time()
            try:
                result = await asyncio.wait_for(read(timeout=timeout), timeout=timeout + 0.75)
                attempt_results.append(
                    {
                        "attempt": attempt,
                        "status": "passed",
                        "elapsed_seconds": round(time.time() - started, 3),
                        "result": result,
                    }
                )
                break
            except Exception as exc:
                attempt_results.append(
                    {
                        "attempt": attempt,
                        "status": "failed",
                        "elapsed_seconds": round(time.time() - started, 3),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

            if attempt < max_attempts:
                await asyncio.sleep(retry_delay)

        passed_count = sum(1 for attempt in attempt_results if attempt["status"] == "passed")
        failed_count = sum(1 for attempt in attempt_results if attempt["status"] == "failed")
        status = "passed" if passed_count else "failed"
        last_success = next((attempt for attempt in reversed(attempt_results) if attempt["status"] == "passed"), None)
        first_success = next(
            (attempt["attempt"] for attempt in attempt_results if attempt["status"] == "passed"),
            None,
        )
        best_elapsed = min(
            (attempt["elapsed_seconds"] for attempt in attempt_results if attempt["status"] == "passed"),
            default=None,
        )
        last_error = next(
            (attempt.get("error", "") for attempt in reversed(attempt_results) if attempt["status"] == "failed"),
            "",
        )

        return {
            "name": label,
            "required": required,
            "status": status,
            "elapsed_seconds": round(time.time() - step_started, 3),
            "attempts": attempt_results,
            "attempt_count": len(attempt_results),
            "max_attempt_count": max_attempts,
            "passed_count": passed_count,
            "failed_count": failed_count,
            "success_rate": round(passed_count / len(attempt_results), 3),
            "first_success_attempt": first_success,
            "best_elapsed_seconds": best_elapsed,
            "last_success_result": last_success.get("result") if last_success else None,
            "error": last_error if status == "failed" else "",
            "retained_state": self._retained_state(node),
        }

    async def _bind_optional_model(self, node, model):
        try:
            return await node.bind_model(model)
        except Exception:
            return False

    async def _read_node(self, node, timeout, attempts, retry_timeout, retry_delay):
        entry = {
            **self._node_summary(node),
            "status": "pending",
            "known_models": self._known_models(node),
            "supports": {},
            "steps": [],
            "state": {},
        }

        try:
            await asyncio.wait_for(node.bind(self.app), timeout=timeout)
        except Exception as exc:
            entry["status"] = "failed"
            entry["error"] = f"bind failed: {type(exc).__name__}: {exc}"
            entry["state"] = self._retained_state(node)
            return entry

        supports_time = await self._bind_optional_model(node, models.TimeServer)
        supports_time_setup = await self._bind_optional_model(node, models.TimeSetupServer)
        supports_scheduler = await self._bind_optional_model(node, SchedulerServer)
        await self._bind_optional_model(node, SchedulerSetupServer)

        entry["supports"] = {
            "onoff": node.supports(Light.OnOffProperty),
            "brightness": node.supports(Light.BrightnessProperty),
            "color_temp": node.supports(Light.TemperatureProperty),
            "time": supports_time,
            "time_role": supports_time_setup,
            "scheduler": supports_scheduler,
        }

        if entry["supports"]["onoff"]:
            entry["steps"].append(
                await self._run_retry_step(node, "generic_onoff_get", node.get_onoff, attempts, retry_timeout, retry_delay)
            )
        if entry["supports"]["brightness"]:
            entry["steps"].append(
                await self._run_retry_step(
                    node,
                    "light_lightness_get",
                    node.get_lightness,
                    attempts,
                    retry_timeout,
                    retry_delay,
                )
            )
        if node._is_model_bound(models.LightCTLServer):
            address = node._model_address(models.LightCTLServer)
            entry["steps"].append(
                await self._run_retry_step(
                    node,
                    "light_ctl_get",
                    self._light_ctl_status_read(address),
                    attempts,
                    retry_timeout,
                    retry_delay,
                )
            )
        if node._is_model_bound(LightCTLTemperatureServer):
            address = node._model_address(LightCTLTemperatureServer)
            entry["steps"].append(
                await self._run_retry_step(
                    node,
                    "light_ctl_temperature_get",
                    self._light_ctl_temperature_read(address),
                    attempts,
                    retry_timeout,
                    retry_delay,
                )
            )
        if supports_time:
            address = node._model_address(models.TimeServer)
            entry["steps"].append(
                await self._run_retry_step(node, "time_get", self._time_read(address), attempts, retry_timeout, retry_delay)
            )
        if supports_time_setup:
            address = node._model_address(models.TimeSetupServer)
            entry["steps"].append(
                await self._run_retry_step(
                    node,
                    "time_role_get",
                    self._time_role_read(address),
                    attempts,
                    retry_timeout,
                    retry_delay,
                )
            )
        if supports_scheduler:
            address = node._model_address(SchedulerServer)
            scheduler_status = await self._run_retry_step(
                node,
                "scheduler_get",
                self._scheduler_status_read(address),
                attempts,
                retry_timeout,
                retry_delay,
            )
            entry["steps"].append(scheduler_status)
            indices = []
            last = scheduler_status.get("last_success_result")
            if isinstance(last, dict):
                indices = last.get("active_indices") or []
            if not indices and scheduler_status["status"] != "failed":
                indices = [0]
            for index in indices[:16]:
                entry["steps"].append(
                    await self._run_retry_step(
                        node,
                        f"scheduler_action_get_{index}",
                        self._scheduler_action_read(address, index),
                        attempts,
                        retry_timeout,
                        retry_delay,
                    )
                )

        entry["state"] = self._retained_state(node)
        passed_steps = [step for step in entry["steps"] if step["status"] == "passed"]
        failed_steps = [step for step in entry["steps"] if step["status"] == "failed"]
        if not passed_steps:
            entry["status"] = "failed"
        elif failed_steps:
            entry["status"] = "partial"
        else:
            entry["status"] = "passed"
        return entry

    def _write_report(self, path, payload):
        if not path:
            return
        with open(path, "w", encoding="utf-8") as output:
            json.dump(payload, output, indent=2, sort_keys=True)
            output.write("\n")

    async def handle_cli(self, args):
        started = time.time()
        selectors = [item for item in args.node if str(item).strip()]
        nodes = [
            node
            for node in self.app.nodes.all()
            if isinstance(node, Light) and self._node_matches(node, selectors)
        ]

        payload = {
            "operation": "model-scope",
            "mode": "standard-read-probe",
            "status": "pending",
            "started_at": started,
            "finished_at": None,
            "timeout_seconds": args.timeout,
            "attempts_per_read": max(1, int(args.attempts)),
            "retry_timeout_seconds": max(0.5, float(args.retry_timeout)),
            "retry_delay_seconds": max(0.0, float(args.retry_delay)),
            "selectors": selectors,
            "sent_light_commands": False,
            "published_mqtt": False,
            "nodes": [],
        }

        if not nodes:
            payload["status"] = "failed"
            payload["error"] = "No configured light nodes matched the requested selector."
        else:
            for node in nodes:
                payload["nodes"].append(
                    await self._read_node(
                        node,
                        args.timeout,
                        payload["attempts_per_read"],
                        payload["retry_timeout_seconds"],
                        payload["retry_delay_seconds"],
                    )
                )
            statuses = [node["status"] for node in payload["nodes"]]
            if not statuses or all(status == "failed" for status in statuses):
                payload["status"] = "failed"
            elif any(status != "passed" for status in statuses):
                payload["status"] = "partial"
            else:
                payload["status"] = "passed"

        payload["finished_at"] = time.time()
        output_path = args.report_output or self.report_path
        self._write_report(output_path, payload)

        for node in payload["nodes"]:
            print(
                f"Model scope {node.get('id') or node['uuid']}@{node['unicast']}: "
                f"{node['status']} {json.dumps(node.get('state', {}), sort_keys=True)}",
                flush=True,
            )
            for step in node.get("steps", []):
                print(
                    f"  {step['name']}: {step['status']} "
                    f"{step.get('passed_count', 0)}/{step.get('attempt_count', 1)} "
                    f"in {step['elapsed_seconds']}s {step.get('error', '')}",
                    flush=True,
                )

        if output_path:
            print(f"Wrote model scope report to {output_path}", flush=True)
        if payload["status"] in {"passed", "partial"}:
            print("Model scope probe completed.", flush=True)
            return
        raise RuntimeError(payload.get("error") or "No model-scope reads succeeded.")
