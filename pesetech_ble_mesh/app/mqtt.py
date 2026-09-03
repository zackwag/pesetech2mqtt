import asyncio
import json
import logging
import os
import ssl
import urllib.request

from asyncio_mqtt import MqttError
from asyncio_mqtt.client import Client

from .skylight import (
    BRIGHTNESS,
    BRIGHTNESS_SCALE,
    MAX_MIRED,
    MIN_MIRED,
    ONOFF,
    TEMPERATURE,
    PesetechSkylight,
)


LOGGER = logging.getLogger(__name__)
DISCOVERY_PREFIX = "homeassistant"
GATEWAY_ID = "mqtt_mesh"
MAX_TRANSITION_SECONDS = 37200.0
SUPERVISOR_MQTT_URL = "http://supervisor/services/mqtt"


class InvalidMqttCommand(ValueError):
    pass


def supervisor_mqtt_settings():
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("SUPERVISOR_TOKEN is missing")
    request = urllib.request.Request(
        SUPERVISOR_MQTT_URL,
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.load(response)
    if result.get("result") != "ok" or not isinstance(result.get("data"), dict):
        raise RuntimeError(f"Supervisor MQTT service request failed: {result!r}")
    data = result["data"]
    try:
        return {
            "hostname": data["host"],
            "port": int(data["port"]),
            "username": data.get("username"),
            "password": data.get("password"),
            "tls_context": ssl.create_default_context() if data.get("ssl") else None,
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Supervisor returned invalid MQTT service data: {data!r}") from exc


def mqtt_settings():
    host = os.environ.get("MQTT_HOST")
    if not host:
        return supervisor_mqtt_settings()
    use_ssl = os.environ.get("MQTT_SSL", "").lower() in ("1", "true", "yes")
    return {
        "hostname": host,
        "port": int(os.environ.get("MQTT_PORT", "8883" if use_ssl else "1883")),
        "username": os.environ.get("MQTT_USERNAME") or None,  # treat "" as absent
        "password": os.environ.get("MQTT_PASSWORD") or None,  # treat "" as absent
        "tls_context": ssl.create_default_context() if use_ssl else None,
    }


def parse_command(raw):
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidMqttCommand("payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise InvalidMqttCommand("payload must be a JSON object")

    command = {}
    if "state" in payload:
        if not isinstance(payload["state"], str) or payload["state"].upper() not in {"ON", "OFF"}:
            raise InvalidMqttCommand("state must be ON or OFF")
        command["state"] = payload["state"].upper()

    if "brightness" in payload:
        if isinstance(payload["brightness"], bool):
            raise InvalidMqttCommand("brightness must be an integer")
        try:
            brightness = int(payload["brightness"])
        except (TypeError, ValueError) as exc:
            raise InvalidMqttCommand("brightness must be an integer") from exc
        if not 0 <= brightness <= BRIGHTNESS_SCALE:
            raise InvalidMqttCommand(f"brightness must be between 0 and {BRIGHTNESS_SCALE}")
        command["brightness"] = brightness

    if "color_temp" in payload:
        if isinstance(payload["color_temp"], bool):
            raise InvalidMqttCommand("color_temp must be an integer")
        try:
            color_temp = int(payload["color_temp"])
        except (TypeError, ValueError) as exc:
            raise InvalidMqttCommand("color_temp must be an integer") from exc
        if not MIN_MIRED <= color_temp <= MAX_MIRED:
            raise InvalidMqttCommand(f"color_temp must be between {MIN_MIRED} and {MAX_MIRED}")
        command["color_temp"] = color_temp

    if "transition" in payload:
        if isinstance(payload["transition"], bool):
            raise InvalidMqttCommand("transition must be a number")
        try:
            transition = float(payload["transition"])
        except (TypeError, ValueError) as exc:
            raise InvalidMqttCommand("transition must be a number") from exc
        if not 0 <= transition <= MAX_TRANSITION_SECONDS:
            raise InvalidMqttCommand(f"transition must be between 0 and {MAX_TRANSITION_SECONDS:g} seconds")
        command["transition"] = transition

    if not {"state", "brightness", "color_temp"} & command.keys():
        raise InvalidMqttCommand("payload contains no light command")
    return command


class PesetechMqttLightBridge:
    def __init__(self, client, node):
        self.client = client
        self.node = node
        self._readback_task = None

    @property
    def base_topic(self):
        return f"{DISCOVERY_PREFIX}/light/{GATEWAY_ID}/{self.node.id}"

    @property
    def command_topic(self):
        return f"{self.base_topic}/set"

    async def publish_json(self, topic, payload, *, retain):
        await self.client.publish(topic, json.dumps(payload).encode("utf-8"), retain=retain)

    async def publish_discovery(self):
        payload = {
            "~": self.base_topic,
            "name": self.node.name,
            "unique_id": f"{GATEWAY_ID}_{self.node.id}",
            "default_entity_id": self.node.default_entity_id,
            "command_topic": "~/set",
            "state_topic": "~/state",
            "schema": "json",
            "brightness": True,
            "brightness_scale": BRIGHTNESS_SCALE,
            "supported_color_modes": ["color_temp"],
            "min_mireds": MIN_MIRED,
            "max_mireds": MAX_MIRED,
            "device": {
                "identifiers": [f"bluetooth_mesh_{self.node.uuid}"],
                "name": self.node.name,
                "manufacturer": "Pesetech/Lepu",
                "model": "Artificial Skylight",
            },
            "origin": {
                "name": "pesetech-home-assistant",
                "support_url": "https://github.com/hrdwdmrbl/pesetech-home-assistant",
            },
        }
        await self.publish_json(f"{self.base_topic}/config", payload, retain=True)

    async def publish_state(self):
        await self.publish_json(f"{self.base_topic}/state", self.node.state_payload(), retain=True)

    def _apply_desired(self, command):
        state = command.get("state")
        if state == "OFF":
            self.node.set_desired(onoff=False)
            return
        self.node.set_desired(
            onoff=True if state == "ON" else None,
            brightness=command.get("brightness"),
            temperature=command.get("color_temp"),
        )

    async def handle_command(self, command):
        transition = command.get("transition")
        self._apply_desired(command)
        await self.publish_state()

        if command.get("state") == "OFF":
            await self.node.turn_off(transition_time=transition)
        elif "brightness" in command and "color_temp" in command:
            await self.node.set_brightness_mireds(
                command["brightness"],
                command["color_temp"],
                transition_time=transition,
            )
        elif "brightness" in command:
            await self.node.set_brightness(command["brightness"], transition_time=transition)
        elif "color_temp" in command:
            await self.node.set_mireds(command["color_temp"], transition_time=transition)
            if command.get("state") == "ON":
                await self.node.turn_on(transition_time=transition)
        else:
            await self.node.turn_on(transition_time=transition)

        self.schedule_readback(transition)

    def schedule_readback(self, transition):
        if self._readback_task is not None:
            self._readback_task.cancel()

        task = None

        async def run():
            try:
                await asyncio.sleep(self.node.readback_delay(transition))
                if await self.node.read_state():
                    await self.publish_state()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                LOGGER.warning("%s readback failed: %s: %s", self.node, type(exc).__name__, exc)
            finally:
                if self._readback_task is task:
                    self._readback_task = None

        task = asyncio.create_task(run())
        self._readback_task = task

    async def listen(self):
        await self.publish_discovery()
        async with self.client.filtered_messages(self.command_topic) as messages:
            await self.client.subscribe(self.command_topic)
            LOGGER.info("Subscribed to %s", self.command_topic)
            async for message in messages:
                try:
                    command = parse_command(message.payload)
                except InvalidMqttCommand as exc:
                    LOGGER.warning("Ignoring malformed MQTT command for %s: %s", self.node, exc)
                    continue
                LOGGER.info("Received MQTT command for %s: %r", self.node, command)
                await self.handle_command(command)

    async def close(self):
        if self._readback_task is not None:
            self._readback_task.cancel()
            await asyncio.gather(self._readback_task, return_exceptions=True)


async def _run_bridge(bridge):
    await bridge.listen()
    raise RuntimeError(f"MQTT bridge exited unexpectedly for {bridge.node}")


async def run_mqtt(nodes):
    settings = await asyncio.to_thread(mqtt_settings)
    retry_delay = 5

    while True:
        client = Client(**settings)
        bridges = [PesetechMqttLightBridge(client, node) for node in nodes]
        try:
            async with client:
                await asyncio.gather(*(_run_bridge(bridge) for bridge in bridges))
            return
        except MqttError as exc:
            LOGGER.warning("MQTT connection lost (%s: %s); reconnecting in %ds", type(exc).__name__, exc, retry_delay)
        finally:
            await asyncio.gather(*(bridge.close() for bridge in bridges), return_exceptions=True)
        await asyncio.sleep(retry_delay)
