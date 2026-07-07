import logging

from mqtt.bridge import HassMqttBridge
from mesh.nodes.light import Light


class GenericLightBridge(HassMqttBridge):
    """
    Generic bridge for lights
    """

    DEFAULT_BRIGHTNESS_SCALE = 50
    DEFAULT_MIN_MIREDS = None
    DEFAULT_MAX_MIREDS = None
    DEFAULT_MANUFACTURER = "Bluetooth Mesh"
    DEFAULT_MODEL = "Light"
    DEFAULT_EXPOSE_COLOR_TEMP = True
    DEFAULT_MAX_TRANSITION_SECONDS = 37200.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @property
    def component(self):
        return "light"

    def _brightness_scale(self, node):
        return node.config.optional("brightness_scale", self.DEFAULT_BRIGHTNESS_SCALE)

    def _min_mireds(self, node):
        return node.config.optional("min_mireds", self.DEFAULT_MIN_MIREDS)

    def _max_mireds(self, node):
        return node.config.optional("max_mireds", self.DEFAULT_MAX_MIREDS)

    def _expose_color_temp(self, node):
        return bool(node.config.optional("expose_color_temp", self.DEFAULT_EXPOSE_COLOR_TEMP))

    def _device_identifier(self, node):
        node_uuid = getattr(node, "uuid", None)
        if node_uuid is not None:
            return f"bluetooth_mesh_{node_uuid}"

        return f"{self._messenger.topic}_{node.config.require('id')}"

    def _unique_id(self, node):
        return f"{self._messenger.topic}_{node.config.require('id')}"

    def _command_int(self, value, label):
        if isinstance(value, bool):
            logging.warning(f"Ignoring invalid MQTT {label} value {value!r}")
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            logging.warning(f"Ignoring invalid MQTT {label} value {value!r}")
            return None

    def _clamp_int(self, value, minimum, maximum, label):
        value = self._command_int(value, label)
        if value is None:
            return None
        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        return value

    def _command_brightness(self, node, value):
        return self._clamp_int(value, 0, self._brightness_scale(node), "brightness")

    def _command_mireds(self, node, value):
        return self._clamp_int(value, self._min_mireds(node), self._max_mireds(node), "color_temp")

    def _command_transition(self, value):
        if isinstance(value, bool):
            logging.warning(f"Ignoring invalid MQTT transition value {value!r}")
            return None
        try:
            value = float(value)
        except (TypeError, ValueError):
            logging.warning(f"Ignoring invalid MQTT transition value {value!r}")
            return None
        if value < 0 or value > self.DEFAULT_MAX_TRANSITION_SECONDS:
            logging.warning(f"Ignoring out-of-range MQTT transition value {value!r}")
            return None
        return value

    def _schedule_readback(self, node, transition_time):
        schedule = getattr(node, "schedule_standard_readback", None)
        if callable(schedule):
            schedule(transition_time=transition_time)

    def _monitor(self):
        monitor = getattr(self._messenger, "diagnostic_monitor", None)
        if monitor is not None and getattr(monitor, "enabled", False):
            return monitor
        return None

    def _command_kwargs(self, command_id):
        if command_id:
            return {"diagnostic_command_id": command_id}
        return {}

    def _record_route(self, monitor, command_id, node, route, **details):
        if monitor is not None:
            monitor.record_mqtt_light_route(command_id, node, route, **details)

    def _schedule_command_readback(self, node, transition_time, command_id):
        schedule = getattr(node, "schedule_standard_readback", None)
        if not callable(schedule):
            return
        if command_id:
            schedule(transition_time=transition_time, diagnostic_command_id=command_id)
        else:
            schedule(transition_time=transition_time)

    async def config(self, node):
        color_modes = set()
        message = {
            "~": self._messenger.node_topic(self.component, node),
            "name": node.config.optional("name"),
            "unique_id": self._unique_id(node),
            "default_entity_id": node.config.optional(
                "default_entity_id", f"{self.component}.{node.config.require('id')}"
            ),
            "command_topic": "~/set",
            "state_topic": "~/state",
            "schema": "json",
            "device": {
                "identifiers": [self._device_identifier(node)],
                "name": node.config.optional("name", node.config.require("id")),
                "manufacturer": node.config.optional("manufacturer", self.DEFAULT_MANUFACTURER),
                "model": node.config.optional("model", self.DEFAULT_MODEL),
            },
            "origin": {
                "name": "pesetech-home-assistant",
                "support_url": "https://github.com/hrdwdmrbl/pesetech-home-assistant",
            },
        }

        has_brightness = node.supports(Light.BrightnessProperty)
        has_temperature = node.supports(Light.TemperatureProperty) and self._expose_color_temp(node)

        if has_brightness:
            message["brightness_scale"] = self._brightness_scale(node)
            message["brightness"] = True

        if has_temperature:
            color_modes.add("color_temp")

            min_mireds = self._min_mireds(node)
            max_mireds = self._max_mireds(node)
            if min_mireds is not None:
                message["min_mireds"] = min_mireds
            if max_mireds is not None:
                message["max_mireds"] = max_mireds

        if has_brightness and not color_modes:
            color_modes.add("brightness")

        if color_modes:
            message["supported_color_modes"] = sorted(color_modes)

        await self._messenger.publish(self.component, node, "config", message, retain=True)

    async def _state(self, node, onoff):
        """
        Send a generic state message covering the nodes full state

        Always include the color mode for capable lights so Home Assistant
        can restore a retained OFF state without treating the light as
        missing color-mode state.
        """
        message = {"state": "ON" if onoff else "OFF"}

        if node.supports(Light.TemperatureProperty) and self._expose_color_temp(node):
            message["color_mode"] = "color_temp"
        elif node.supports(Light.BrightnessProperty):
            message["color_mode"] = "brightness"

        if onoff and node.supports(Light.BrightnessProperty):
            message["brightness"] = node.retained(Light.BrightnessProperty, self._brightness_scale(node))
        if onoff and node.supports(Light.TemperatureProperty) and self._expose_color_temp(node):
            message["color_temp"] = node.retained(Light.TemperatureProperty, self._min_mireds(node) or 100)

        await self._messenger.publish(self.component, node, "state", message, retain=True)

    async def _mqtt_set(self, node, payload, topic=None):
        monitor = self._monitor()
        command_id = monitor.command_id() if monitor is not None else None
        if monitor is not None:
            monitor.record_mqtt_light_command(command_id, node, topic, payload)

        state = payload.get("state")
        if isinstance(state, str):
            state = state.upper()

        transition_time = None
        if "transition" in payload:
            transition_time = self._command_transition(payload.get("transition"))

        has_brightness_command = "brightness" in payload
        has_color_temp_command = "color_temp" in payload and self._expose_color_temp(node)
        has_value_command = has_brightness_command or has_color_temp_command
        command_sent = False

        if state == "ON" and not has_value_command:
            self._record_route(monitor, command_id, node, "turn_on", state=state, transition=transition_time)
            await node.turn_on(
                confirm=False,
                transition_time=transition_time,
                **self._command_kwargs(command_id),
            )
            command_sent = True
        elif state == "OFF":
            self._record_route(monitor, command_id, node, "turn_off", state=state, transition=transition_time)
            await node.turn_off(
                confirm=False,
                transition_time=transition_time,
                **self._command_kwargs(command_id),
            )
            self._schedule_command_readback(node, transition_time, command_id)
            return

        brightness = None
        color_temp = None
        if has_brightness_command:
            brightness = self._command_brightness(node, payload["brightness"])
        if has_color_temp_command:
            color_temp = self._command_mireds(node, payload["color_temp"])

        if brightness is not None and color_temp is not None:
            self._record_route(
                monitor,
                command_id,
                node,
                "brightness_color_temp",
                state=state,
                brightness=brightness,
                color_temp=color_temp,
                transition=transition_time,
            )
            await node.set_brightness_mireds(
                brightness,
                color_temp,
                confirm=False,
                transition_time=transition_time,
                **self._command_kwargs(command_id),
            )
            self._schedule_command_readback(node, transition_time, command_id)
            return

        if color_temp is not None:
            self._record_route(
                monitor,
                command_id,
                node,
                "color_temp",
                state=state,
                color_temp=color_temp,
                transition=transition_time,
            )
            await node.set_mireds(color_temp, transition_time=transition_time, **self._command_kwargs(command_id))
            command_sent = True
        if brightness is not None:
            self._record_route(
                monitor,
                command_id,
                node,
                "brightness",
                state=state,
                brightness=brightness,
                transition=transition_time,
            )
            await node.set_brightness(
                brightness,
                confirm=False,
                transition_time=transition_time,
                **self._command_kwargs(command_id),
            )
            command_sent = True
        elif color_temp is not None and state == "ON":
            self._record_route(monitor, command_id, node, "turn_on_after_color_temp", state=state, transition=transition_time)
            await node.turn_on(
                confirm=False,
                transition_time=transition_time,
                **self._command_kwargs(command_id),
            )
            command_sent = True

        if command_sent:
            self._schedule_command_readback(node, transition_time, command_id)

    async def _notify_onoff(self, node, onoff):
        await self._state(node, onoff)

    async def _notify_brightness(self, node, brightness):
        await self._state(node, brightness > 0)

    async def _notify_temperature(self, node, temperature):
        await self._state(node, node.retained(Light.OnOffProperty, True))


class PesetechSkylightBridge(GenericLightBridge):
    """
    Pesetech/Lepu skylights are standard BLE Mesh CTL lights, but the
    values exposed by the working Home Assistant setup are the raw CTL
    scale rather than normal Kelvin or 0-255 brightness values.
    """

    DEFAULT_BRIGHTNESS_SCALE = 65280
    DEFAULT_MIN_MIREDS = 100
    DEFAULT_MAX_MIREDS = 556
    DEFAULT_MANUFACTURER = "Pesetech/Lepu"
    DEFAULT_MODEL = "Artificial Skylight"
    DEFAULT_EXPOSE_COLOR_TEMP = True
