import asyncio
import json
import os
import re
import time

from bluetooth_mesh import models
from bluetooth_mesh.messages.config import ConfigOpcode, NodeIdentity, StatusCode

from mesh.nodes.light import Light, LightCTLTemperatureServer
from mesh.nodes.light import PESETECH_READ_ATTEMPTS
from mesh.nodes.light import PESETECH_READ_RETRY_DELAY
from mesh.nodes.light import PESETECH_READ_TIMEOUT
from mesh.nodes.light import PESETECH_WRITE_RETRANSMISSIONS

from . import Module


HEX_RE = re.compile(r"^[0-9a-fA-F_:\-\s]+$")


class RawCommandModule(Module):
    """
    Send one targeted Bluetooth Mesh command and optionally read back state.

    This is intentionally small and hardware-facing: it lets protocol work
    iterate through the add-on without Home Assistant service/MQTT ceremony.
    """

    def __init__(self):
        super().__init__()
        self.report_path = ""

    def initialize(self, app, store, config):
        super().initialize(app, store, config)
        self.report_path = os.environ.get("PESETECH_RAW_COMMAND_REPORT", "")

    def setup_cli(self, parser):
        parser.add_argument("--command", choices=["raw", "pesetech-brightness"], default="raw")
        parser.add_argument("--node", action="append", default=[])
        parser.add_argument("--opcode", default="")
        parser.add_argument("--payload", default="")
        parser.add_argument("--brightness", type=int, default=32768)
        parser.add_argument("--address", default="")
        parser.add_argument(
            "--address-model",
            choices=["unicast", "onoff", "lightness", "ctl", "ctl-temperature"],
            default="lightness",
        )
        parser.add_argument("--retransmissions", type=int, default=PESETECH_WRITE_RETRANSMISSIONS)
        parser.add_argument("--send-interval-ms", type=int, default=75)
        parser.add_argument("--timeout", type=float, default=20.0)
        parser.add_argument("--read-attempts", type=int, default=PESETECH_READ_ATTEMPTS)
        parser.add_argument("--read-timeout", type=float, default=PESETECH_READ_TIMEOUT)
        parser.add_argument("--read-retry-delay", type=float, default=PESETECH_READ_RETRY_DELAY)
        parser.add_argument("--read-after", dest="read_after", action="store_true", default=True)
        parser.add_argument("--no-read-after", dest="read_after", action="store_false")
        parser.add_argument("--report-output", default=None)

    def _parse_int(self, value, label):
        text = str(value).strip()
        if not text:
            raise ValueError(f"{label} is required")
        if text.lower().startswith("0x"):
            return int(text, 16)
        if any("a" <= char.lower() <= "f" for char in text) or (len(text) > 1 and text.startswith("0")):
            return int(text, 16)
        return int(text, 10)

    def _parse_payload(self, value):
        text = str(value or "").strip()
        if not text:
            return b""
        if not HEX_RE.match(text):
            raise ValueError("payload must be hex bytes")
        compact = re.sub(r"[^0-9a-fA-F]", "", text)
        if len(compact) % 2:
            raise ValueError("payload hex must contain an even number of digits")
        return bytes.fromhex(compact)

    def _parse_payload_sequence(self, value):
        text = str(value or "").strip()
        if not text:
            return [b""]
        parts = text.split()
        if len(parts) <= 1:
            return [self._parse_payload(text)]
        return [self._parse_payload(part) for part in parts]

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

    def _target_address(self, node, args):
        if args.address:
            return self._parse_int(args.address, "address")
        if args.address_model == "unicast":
            return node.unicast

        model = {
            "onoff": models.GenericOnOffServer,
            "lightness": models.LightLightnessServer,
            "ctl": models.LightCTLServer,
            "ctl-temperature": LightCTLTemperatureServer,
        }[args.address_model]

        try:
            return node._model_address(model)
        except KeyError as exc:
            raise RuntimeError(f"{node} has no bound {args.address_model} model address") from exc

    async def _run_step(self, node, label, action, timeout, required=True):
        started = time.time()
        try:
            result = await asyncio.wait_for(action(), timeout=timeout + 1.0)
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
        last_success = next((attempt for attempt in reversed(attempt_results) if attempt["status"] == "passed"), None)
        first_success = next(
            (attempt["attempt"] for attempt in attempt_results if attempt["status"] == "passed"),
            None,
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
            "last_success_result": last_success.get("result") if last_success else None,
            "error": last_error if status == "failed" else "",
            "retained_state": self._retained_state(node),
        }

    def _config_node_identity_params(self, payload):
        if payload:
            if len(payload) != 3:
                raise ValueError("CONFIG_NODE_IDENTITY_SET payload must be 3 bytes: net_key_index_le identity")
            net_key_index = int.from_bytes(payload[:2], "little") & 0x0FFF
            identity = NodeIdentity(payload[2])
        else:
            net_key_index = self.app.primary_net_key[0]
            identity = NodeIdentity.RUNNING
        return {"net_key_index": net_key_index, "identity": identity}

    def _plain_config_node_identity_status(self, message):
        body = message[ConfigOpcode.CONFIG_NODE_IDENTITY_STATUS.name.lower()]
        status = body["status"]
        identity = body["identity"]
        return {
            "status": status.name if hasattr(status, "name") else str(status),
            "net_key_index": int(body["net_key_index"]),
            "identity": identity.name if hasattr(identity, "name") else str(identity),
        }

    async def _send_raw(self, address, opcode, payload, retransmissions, send_interval, timeout):
        if opcode == ConfigOpcode.CONFIG_NODE_IDENTITY_SET:
            client = self.app.elements[0][models.ConfigClient]
            params = self._config_node_identity_params(payload)
            status = client.expect_dev(
                address,
                net_index=self.app.primary_net_key[0],
                opcode=ConfigOpcode.CONFIG_NODE_IDENTITY_STATUS,
                params={"net_key_index": params["net_key_index"]},
            )

            async def request():
                return await client.send_dev(
                    address,
                    net_index=self.app.primary_net_key[0],
                    opcode=ConfigOpcode.CONFIG_NODE_IDENTITY_SET,
                    params=params,
                )

            message = await client.query(
                request,
                status,
                send_interval=max(send_interval, 0.5),
                timeout=timeout,
            )
            body = message[ConfigOpcode.CONFIG_NODE_IDENTITY_STATUS.name.lower()]
            result = self._plain_config_node_identity_status(message)
            if body["status"] != StatusCode.SUCCESS or body["identity"] != NodeIdentity.RUNNING:
                raise RuntimeError(f"CONFIG_NODE_IDENTITY_SET did not start node identity: {result}")
            return result

        client = self.app.elements[0][models.LightLightnessClient]

        async def request():
            return await client.send_app(
                address,
                app_index=self.app.app_keys[0][0],
                opcode=opcode,
                params=payload,
            )

        if retransmissions <= 1:
            return await request()
        return await client.repeat(request, retransmissions=retransmissions, send_interval=send_interval)

    async def _send_raw_sequence(self, address, opcode, payloads, retransmissions, send_interval, timeout):
        results = []
        for index, payload in enumerate(payloads):
            result = await self._send_raw(address, opcode, payload, retransmissions, send_interval, timeout)
            results.append({"index": index, "payload": payload.hex(), "result": result})
            if index + 1 < len(payloads):
                await asyncio.sleep(0.5)
        return results

    async def _read_standard_state(self, node, args, entry):
        if not args.read_after:
            return
        if node.supports(Light.OnOffProperty):
            entry["steps"].append(
                await self._run_retry_step(
                    node,
                    "get_onoff",
                    node.get_onoff,
                    args.read_attempts,
                    args.read_timeout,
                    args.read_retry_delay,
                )
            )
        if node.supports(Light.BrightnessProperty):
            entry["steps"].append(
                await self._run_retry_step(
                    node,
                    "get_lightness",
                    node.get_lightness,
                    args.read_attempts,
                    args.read_timeout,
                    args.read_retry_delay,
                )
            )
        if node.supports(Light.TemperatureProperty) and node._is_model_bound(LightCTLTemperatureServer):
            temperature_status = await self._run_retry_step(
                node,
                "light_ctl_temperature_get",
                node.get_ctl_temperature,
                args.read_attempts,
                args.read_timeout,
                args.read_retry_delay,
                required=False,
            )
            entry["steps"].append(temperature_status)
            if temperature_status["status"] == "passed":
                return
        if node.supports(Light.TemperatureProperty) and node._is_model_bound(models.LightCTLServer):
            entry["steps"].append(
                await self._run_retry_step(
                    node,
                    "light_ctl_get_for_temperature",
                    node.get_ctl,
                    args.read_attempts,
                    args.read_timeout,
                    args.read_retry_delay,
                    required=False,
                )
            )

    async def _run_node(self, node, args):
        entry = {
            **self._node_summary(node),
            "status": "pending",
            "command": {
                "name": args.command,
                "address_model": args.address_model,
                "retransmissions": args.retransmissions,
                "send_interval_ms": args.send_interval_ms,
                "read_after": args.read_after,
            },
            "supports": {},
            "steps": [],
            "state": {},
        }

        try:
            await asyncio.wait_for(node.bind(self.app), timeout=args.timeout)
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

        target_address = self._target_address(node, args)
        entry["command"]["target_address"] = f"{target_address:04X}"
        send_interval = max(0, args.send_interval_ms) / 1000
        retransmissions = max(1, args.retransmissions)

        if args.command == "raw":
            opcode = self._parse_int(args.opcode, "opcode")
            payloads = self._parse_payload_sequence(args.payload)
            entry["command"]["opcode"] = f"0x{opcode:X}"
            entry["command"]["payload"] = " ".join(payload.hex() for payload in payloads)
            entry["command"]["payloads"] = [payload.hex() for payload in payloads]
            entry["steps"].append(
                await self._run_step(
                    node,
                    "send_raw",
                    lambda: self._send_raw_sequence(
                        target_address,
                        opcode,
                        payloads,
                        retransmissions,
                        send_interval,
                        args.timeout,
                    ),
                    args.timeout,
                )
            )
        elif args.command == "pesetech-brightness":
            brightness = max(0, min(65535, int(args.brightness)))
            entry["command"]["brightness"] = brightness
            entry["steps"].append(
                await self._run_step(
                    node,
                    "set_pesetech_brightness",
                    lambda: node.set_brightness(brightness),
                    args.timeout,
                )
            )

        await self._read_standard_state(node, args, entry)
        entry["state"] = self._retained_state(node)

        required_steps = [step for step in entry["steps"] if step.get("required", True)]
        optional_steps = [step for step in entry["steps"] if not step.get("required", True)]
        required_failed = any(step["status"] != "passed" for step in required_steps)
        optional_failed = any(step["status"] != "passed" for step in optional_steps)
        if not entry["steps"] or required_failed:
            entry["status"] = "failed"
        elif optional_failed:
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
            "operation": "raw-command",
            "status": "pending",
            "started_at": started,
            "finished_at": None,
            "timeout_seconds": args.timeout,
            "selectors": selectors,
            "sent_light_commands": True,
            "published_mqtt": False,
            "nodes": [],
        }

        if not nodes:
            payload["status"] = "failed"
            payload["error"] = "No configured light nodes matched the requested selector."
        else:
            for node in nodes:
                payload["nodes"].append(await self._run_node(node, args))
            statuses = [node["status"] for node in payload["nodes"]]
            if not statuses or any(status == "failed" for status in statuses):
                payload["status"] = "failed"
            elif any(status == "partial" for status in statuses):
                payload["status"] = "partial"
            else:
                payload["status"] = "passed"

        payload["finished_at"] = time.time()
        output_path = args.report_output or self.report_path
        self._write_report(output_path, payload)

        for node in payload["nodes"]:
            command = node.get("command") or {}
            target = command.get("target_address", "????")
            print(
                f"Raw command {node.get('id') or node['uuid']}@{node['unicast']} -> {target}: "
                f"{node['status']} {json.dumps(node.get('state', {}), sort_keys=True)}",
                flush=True,
            )
            for step in node.get("steps", []):
                if step["status"] == "passed":
                    print(f"  {step['name']}: passed in {step['elapsed_seconds']}s", flush=True)
                else:
                    print(f"  {step['name']}: failed in {step['elapsed_seconds']}s: {step.get('error', '')}", flush=True)

        if output_path:
            print(f"Wrote raw command report to {output_path}", flush=True)
        if payload["status"] in {"passed", "partial"}:
            if payload["status"] == "partial":
                print("Raw command completed with optional read failures.", flush=True)
            else:
                print("Raw command completed.", flush=True)
            return
        raise RuntimeError(payload.get("error") or "One or more raw command steps failed.")
