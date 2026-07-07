import asyncio
import json
import os
import time

from mesh.nodes.light import Light
from mesh.nodes.light import LightCTLTemperatureServer
from mesh.nodes.light import PESETECH_READ_ATTEMPTS
from mesh.nodes.light import PESETECH_READ_RETRY_DELAY
from mesh.nodes.light import PESETECH_READ_TIMEOUT
from bluetooth_mesh import models

from . import Module
from .model_scope import ModelScopeModule, SchedulerServer, SchedulerSetupServer


class StateReaderModule(Module):
    """
    Read live state from configured light nodes without publishing MQTT commands.
    """

    def __init__(self):
        super().__init__()
        self.report_path = ""

    def initialize(self, app, store, config):
        super().initialize(app, store, config)
        self.report_path = os.environ.get("PESETECH_STATE_READ_REPORT", "")

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

    async def _run_step(self, node, label, read, timeout, required=True):
        started = time.time()
        try:
            result = await asyncio.wait_for(read(timeout=timeout), timeout=timeout + 1.0)
            return {
                "name": label,
                "required": required,
                "status": "passed",
                "elapsed_seconds": round(time.time() - started, 3),
                "result": result,
                "retained_state": self._retained_state(node),
            }
        except Exception as exc:
            return {
                "name": label,
                "required": required,
                "status": "failed",
                "elapsed_seconds": round(time.time() - started, 3),
                "error": f"{type(exc).__name__}: {exc}",
                "retained_state": self._retained_state(node),
            }

    async def _run_retry_step(self, node, label, read, attempts, timeout, retry_delay, required=True):
        step_started = time.time()
        max_attempts = max(1, int(attempts))
        attempt_results = []

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

        last_success = next(
            (
                attempt
                for attempt in reversed(attempt_results)
                if attempt["status"] == "passed"
            ),
            None,
        )
        first_success = next(
            (
                attempt["attempt"]
                for attempt in attempt_results
                if attempt["status"] == "passed"
            ),
            None,
        )
        best_elapsed = min(
            (
                attempt["elapsed_seconds"]
                for attempt in attempt_results
                if attempt["status"] == "passed"
            ),
            default=None,
        )
        last_error = next(
            (
                attempt.get("error", "")
                for attempt in reversed(attempt_results)
                if attempt["status"] == "failed"
            ),
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

    def _step_by_name(self, entry, name):
        return next((step for step in entry["steps"] if step.get("name") == name), None)

    def _derive_onoff_from_lightness(self, entry):
        onoff_step = self._step_by_name(entry, "get_onoff")
        lightness_step = self._step_by_name(entry, "get_lightness")
        if not onoff_step or onoff_step.get("status") != "failed":
            return False
        if not lightness_step or lightness_step.get("status") != "passed":
            return False

        result = lightness_step.get("last_success_result") or {}
        brightness = result.get("present_lightness")
        if brightness is None:
            brightness = entry.get("state", {}).get("brightness")
        if brightness is None:
            return False

        derived = int(brightness) > 0
        entry["state"]["onoff"] = derived
        entry["derived_state"] = {
            "onoff": {
                "source": "brightness",
                "brightness": int(brightness),
                "value": derived,
            }
        }
        entry["steps"].append(
            {
                "name": "derive_onoff_from_lightness",
                "required": False,
                "status": "passed",
                "elapsed_seconds": 0.0,
                "attempts": [],
                "attempt_count": 1,
                "max_attempt_count": 1,
                "passed_count": 1,
                "failed_count": 0,
                "success_rate": 1.0,
                "first_success_attempt": None,
                "last_success_result": entry["derived_state"]["onoff"],
                "retained_state": entry["state"],
            }
        )
        return True

    async def _read_node(self, node, timeout, attempts, retry_timeout, retry_delay):
        entry = {
            **self._node_summary(node),
            "status": "pending",
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

        entry["supports"] = {
            "onoff": node.supports(Light.OnOffProperty),
            "brightness": node.supports(Light.BrightnessProperty),
            "color_temp": node.supports(Light.TemperatureProperty),
        }

        if entry["supports"]["onoff"]:
            entry["steps"].append(
                await self._run_retry_step(
                    node, "get_onoff", node.get_onoff, attempts, retry_timeout, retry_delay
                )
            )
        if entry["supports"]["brightness"]:
            entry["steps"].append(
                await self._run_retry_step(
                    node, "get_lightness", node.get_lightness, attempts, retry_timeout, retry_delay
                )
            )

        scope = ModelScopeModule()
        scope.initialize(self.app, self.store, self.config)
        optional_attempts = 1
        optional_retry_timeout = min(float(retry_timeout), 1.25)
        ctl_attempts = max(1, int(attempts))
        ctl_retry_timeout = min(float(retry_timeout), 1.25)
        supports_time = await scope._bind_optional_model(node, models.TimeServer)
        supports_time_setup = await scope._bind_optional_model(node, models.TimeSetupServer)
        supports_scheduler = await scope._bind_optional_model(node, SchedulerServer)
        await scope._bind_optional_model(node, SchedulerSetupServer)
        entry["supports"].update(
            {
                "time": supports_time,
                "time_role": supports_time_setup,
                "scheduler": supports_scheduler,
            }
        )

        ctl_temperature_status = None
        if node._is_model_bound(LightCTLTemperatureServer):
            ctl_temperature_status = await self._run_retry_step(
                node,
                "light_ctl_temperature_get",
                node.get_ctl_temperature,
                ctl_attempts,
                ctl_retry_timeout,
                retry_delay,
                required=False,
            )
            entry["steps"].append(ctl_temperature_status)
        if (
            node._is_model_bound(models.LightCTLServer)
            and (ctl_temperature_status is None or ctl_temperature_status["status"] == "failed")
        ):
            entry["steps"].append(
                await self._run_retry_step(
                    node,
                    "light_ctl_get_for_temperature",
                    node.get_ctl,
                    ctl_attempts,
                    ctl_retry_timeout,
                    retry_delay,
                    required=False,
                )
            )
        if supports_time:
            address = node._model_address(models.TimeServer)
            entry["steps"].append(
                await self._run_retry_step(
                    node,
                    "time_get",
                    scope._time_read(address),
                    optional_attempts,
                    optional_retry_timeout,
                    retry_delay,
                    required=False,
                )
            )
        if supports_time_setup:
            address = node._model_address(models.TimeSetupServer)
            entry["steps"].append(
                await self._run_retry_step(
                    node,
                    "time_role_get",
                    scope._time_role_read(address),
                    optional_attempts,
                    optional_retry_timeout,
                    retry_delay,
                    required=False,
                )
            )
        if supports_scheduler:
            address = node._model_address(SchedulerServer)
            scheduler_status = await self._run_retry_step(
                node,
                "scheduler_get",
                scope._scheduler_status_read(address),
                optional_attempts,
                optional_retry_timeout,
                retry_delay,
                required=False,
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
                        scope._scheduler_action_read(address, index),
                        optional_attempts,
                        optional_retry_timeout,
                        retry_delay,
                        required=False,
                    )
                )

        entry["state"] = self._retained_state(node)
        derived_onoff = self._derive_onoff_from_lightness(entry)
        required_steps = [step for step in entry["steps"] if step.get("required", True)]
        required_failed = any(
            step["status"] == "failed"
            for step in required_steps
            if not (derived_onoff and step.get("name") == "get_onoff")
        )
        if not entry["steps"] or required_failed:
            entry["status"] = "failed"
        else:
            entry["status"] = "passed"
        return entry

    def _write_report(self, path, payload):
        if not path:
            return
        with open(path, "w", encoding="utf-8") as output:
            json.dump(payload, output, indent=2, sort_keys=True, default=str)
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
            "operation": "read-state",
            "mode": "retry-probe",
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
            if not statuses or any(status == "failed" for status in statuses):
                payload["status"] = "failed"
            else:
                payload["status"] = "passed"

        payload["finished_at"] = time.time()
        output_path = args.report_output or self.report_path
        self._write_report(output_path, payload)

        for node in payload["nodes"]:
            print(
                f"Read state {node.get('id') or node['uuid']}@{node['unicast']}: "
                f"{node['status']} {json.dumps(node.get('state', {}), sort_keys=True, default=str)}",
                flush=True,
            )
            for step in node.get("steps", []):
                if step["status"] == "passed":
                    print(
                        f"  {step['name']}: passed {step.get('passed_count', 1)}/"
                        f"{step.get('attempt_count', 1)} in {step['elapsed_seconds']}s",
                        flush=True,
                    )
                else:
                    print(
                        f"  {step['name']}: failed {step.get('passed_count', 0)}/"
                        f"{step.get('attempt_count', 1)} in {step['elapsed_seconds']}s: "
                        f"{step.get('error', '')}",
                        flush=True,
                    )

        if output_path:
            print(f"Wrote state read report to {output_path}", flush=True)
        if payload["status"] == "passed":
            print("State read completed.", flush=True)
            return
        raise RuntimeError(payload.get("error") or "One or more state reads failed.")
