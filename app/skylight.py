import asyncio
import logging
import time
from uuid import UUID

from bluetooth_mesh import models
from bluetooth_mesh.messages.generic.light.ctl import LightCTLOpcode
from bluetooth_mesh.messages.generic.light.lightness import LightLightnessOpcode
from bluetooth_mesh.messages.generic.onoff import GenericOnOffOpcode

LOGGER = logging.getLogger(__name__)

PESETECH_VENDOR_OPCODE = 0xE31102
WRITE_INTERVAL = 0.075
ACK_TIMEOUT = 10.0
VENDOR_REPETITIONS = 10
READ_ATTEMPTS = 10
READ_TIMEOUT = 1.25
READ_RETRY_DELAY = 0.15
READBACK_DELAY = 0.75
BRIGHTNESS_SCALE = 65280
MIN_KELVIN = 1800
MAX_KELVIN = 10000
MIN_MIRED = 100
MAX_MIRED = 556
MIN_CTL_TEMPERATURE = 800
MAX_CTL_TEMPERATURE = 20000

MODEL_ONOFF = "1000"
MODEL_LIGHTNESS = "1300"
MODEL_CTL = "1303"
MODEL_CTL_TEMPERATURE = "1306"
REQUIRED_MODELS = {MODEL_ONOFF, MODEL_LIGHTNESS, MODEL_CTL, MODEL_CTL_TEMPERATURE}

ONOFF = "onoff"
BRIGHTNESS = "brightness"
TEMPERATURE = "temperature"

_READ_LOCK = None


class LightCTLTemperatureServer:
    MODEL_ID = (None, 0x1306)


class PesetechSkylight:
    def __init__(self, node_uuid, node_data, config):
        self.uuid = UUID(str(node_uuid))
        self.unicast = int(node_data["unicast"])
        self.count = int(node_data["count"])
        self.id = config["id"]
        self.name = config["name"]
        self.default_entity_id = config.get("default_entity_id", f"light.{self.id}")
        self._models = self._load_models(node_data.get("imported_models"))
        self._state = {}
        self._last_nonzero_brightness = BRIGHTNESS_SCALE
        self._app = None

    def __str__(self):
        return f"{self.name} ({self.unicast:04X})"

    @staticmethod
    def _load_models(value):
        if not isinstance(value, dict):
            raise ValueError("imported_models must be a mapping")
        normalized = {}
        for key, address in value.items():
            model = str(key).removeprefix("0x").upper().zfill(4)
            normalized[model] = int(address, 0) if isinstance(address, str) else int(address)
        missing = sorted(REQUIRED_MODELS - normalized.keys())
        if missing:
            raise ValueError(f"missing required imported models: {', '.join(missing)}")
        return {key: normalized[key] for key in REQUIRED_MODELS}

    async def bind(self, app):
        self._app = app
        for model, address in self._models.items():
            if not 1 <= address <= 0x7FFF:
                raise ValueError(f"invalid address {address!r} for model {model}")
        LOGGER.info("Initialized %s with imported model addresses %s", self, self._models)

    def retained(self, key, fallback):
        return self._state.get(key, fallback)

    def set_desired(self, *, onoff=None, brightness=None, temperature=None):
        if onoff is not None:
            self._state[ONOFF] = bool(onoff)
        if brightness is not None:
            brightness = int(brightness)
            self._state[BRIGHTNESS] = brightness
            self._state[ONOFF] = brightness > 0
            if brightness > 0:
                self._last_nonzero_brightness = brightness
        if temperature is not None:
            self._state[TEMPERATURE] = int(temperature)

    def state_payload(self):
        onoff = bool(self.retained(ONOFF, True))
        payload = {
            "state": "ON" if onoff else "OFF",
            "color_mode": "color_temp",
        }
        if onoff:
            payload["brightness"] = self.retained(BRIGHTNESS, BRIGHTNESS_SCALE)
            payload["color_temp"] = self.retained(TEMPERATURE, MIN_MIRED)
        return payload

    @property
    def app_index(self):
        return self._app.app_keys[0][0]

    def _client(self, model):
        return self._app.elements[0][model]

    def _address(self, model):
        return self._models[model]

    @staticmethod
    def _transition_params(transition_time, delay):
        if transition_time is None:
            return {}
        return {"transition_time": float(transition_time), "delay": float(delay)}

    @staticmethod
    def _status_payload(status, opcode):
        if status is None:
            return None
        key = getattr(opcode, "name", str(opcode)).lower()
        try:
            return status[key]
        except (KeyError, TypeError):
            return None

    @staticmethod
    def _status_matches(payload, expected):
        present = []
        target = []
        for value, present_key, target_key in expected:
            try:
                present.append(present_key in payload and int(payload[present_key]) == int(value))
            except (TypeError, ValueError):
                present.append(False)
            try:
                target.append(target_key in payload and int(payload[target_key]) == int(value))
            except (TypeError, ValueError):
                target.append(False)
        return all(present) or all(target)

    @staticmethod
    def _expect(client, address, app_index, opcode):
        callbacks = getattr(client, "app_message_callbacks", None)
        bucket = callbacks[opcode] if callbacks is not None else None
        before = set(bucket) if bucket is not None else set()
        future = client.expect_app(
            address,
            app_index=app_index,
            destination=None,
            opcode=opcode,
            params={},
        )
        added = set(bucket) - before if bucket is not None else set()
        return future, bucket, added

    async def _send_acknowledged(
        self,
        *,
        client,
        address,
        request_opcode,
        status_opcode,
        label,
        make_params,
        expected,
        timeout=ACK_TIMEOUT,
        send_interval=WRITE_INTERVAL,
    ):
        status, callback_bucket, callbacks = self._expect(client, address, self.app_index, status_opcode)
        attempts = 0
        started = time.monotonic()

        async def request():
            nonlocal attempts
            attempts += 1
            await client.send_app(
                address,
                app_index=self.app_index,
                opcode=request_opcode,
                params=make_params(),
            )

        try:
            result = await client.query(
                request,
                status,
                send_interval=send_interval,
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"{self} received no {label} acknowledgement after {timeout:g}s") from exc
        finally:
            if not status.done():
                status.cancel()
            if callback_bucket is not None:
                callback_bucket.difference_update(callbacks)

        payload = self._status_payload(result, status_opcode)
        if payload is None:
            raise ValueError(f"{self} received an invalid {label} status: {result!r}")
        if not self._status_matches(payload, expected):
            LOGGER.warning("%s accepted %s but returned an unverified status: %r", self, label, payload)
        else:
            LOGGER.info(
                "%s acknowledged %s after %d attempt(s) in %.3fs",
                self,
                label,
                attempts,
                time.monotonic() - started,
            )
        return payload

    def _ack_params(self, values, transition_time, delay):
        current_delay = float(delay)

        def make_params():
            nonlocal current_delay
            payload = dict(values)
            payload.update(self._transition_params(transition_time, current_delay))
            current_delay = max(0.0, current_delay - WRITE_INTERVAL)
            return payload

        return make_params

    async def turn_on(self, transition_time=None):
        self.set_desired(onoff=True)
        return await self._set_onoff(True, transition_time)

    async def turn_off(self, transition_time=None):
        self.set_desired(onoff=False)
        return await self._set_onoff(False, transition_time)

    async def _set_onoff(self, onoff, transition_time=None):
        client = self._client(models.GenericOnOffClient)
        value = 1 if onoff else 0
        return await self._send_acknowledged(
            client=client,
            address=self._address(MODEL_ONOFF),
            request_opcode=GenericOnOffOpcode.GENERIC_ONOFF_SET,
            status_opcode=GenericOnOffOpcode.GENERIC_ONOFF_STATUS,
            label="on/off",
            make_params=self._ack_params(
                {"onoff": value, "tid": client.tid()},
                transition_time,
                0,
            ),
            expected=[(value, "present_onoff", "target_onoff")],
        )

    async def set_brightness(self, brightness, transition_time=None):
        brightness = int(brightness)
        self.set_desired(brightness=brightness)
        client = self._client(models.LightLightnessClient)
        result = await self._send_acknowledged(
            client=client,
            address=self._address(MODEL_LIGHTNESS),
            request_opcode=LightLightnessOpcode.LIGHT_LIGHTNESS_SET,
            status_opcode=LightLightnessOpcode.LIGHT_LIGHTNESS_STATUS,
            label="lightness",
            make_params=self._ack_params(
                {"lightness": brightness, "tid": client.tid()},
                transition_time,
                0,
            ),
            expected=[(brightness, "present_lightness", "target_lightness")],
        )
        if transition_time in (None, 0, 0.0):
            await self._set_vendor_brightness(brightness)
        return result

    async def _set_vendor_brightness(self, brightness):
        client = self._client(models.LightLightnessClient)
        payload = self._vendor_brightness_payload(brightness)
        for attempt in range(VENDOR_REPETITIONS):
            await client.send_app(
                self._address(MODEL_LIGHTNESS),
                app_index=self.app_index,
                opcode=PESETECH_VENDOR_OPCODE,
                params=payload,
            )
            if attempt + 1 < VENDOR_REPETITIONS:
                await asyncio.sleep(WRITE_INTERVAL)

    @staticmethod
    def _vendor_brightness_payload(brightness):
        brightness = max(0, min(65535, int(brightness)))
        return (
            b"\xa0\xff\x00\x00\x00\x00\x00"
            + brightness.to_bytes(2, "little")
            + b"\x00" * 14
        )

    async def set_mireds(self, mireds, transition_time=None):
        mireds = int(mireds)
        ctl_temperature = self.mireds_to_ctl_temperature(mireds)
        self.set_desired(temperature=mireds)
        client = self._client(models.LightCTLClient)
        return await self._send_acknowledged(
            client=client,
            address=self._address(MODEL_CTL_TEMPERATURE),
            request_opcode=LightCTLOpcode.LIGHT_CTL_TEMPERATURE_SET,
            status_opcode=LightCTLOpcode.LIGHT_CTL_TEMPERATURE_STATUS,
            label="CTL temperature",
            make_params=self._ack_params(
                {"ctl_temperature": ctl_temperature, "ctl_delta_uv": 0, "tid": client.tid()},
                transition_time,
                0,
            ),
            expected=[(ctl_temperature, "present_ctl_temperature", "target_ctl_temperature")],
        )

    async def set_brightness_mireds(self, brightness, mireds, transition_time=None):
        brightness = int(brightness)
        mireds = int(mireds)
        ctl_temperature = self.mireds_to_ctl_temperature(mireds)
        self.set_desired(brightness=brightness, temperature=mireds)
        client = self._client(models.LightCTLClient)
        return await self._send_acknowledged(
            client=client,
            address=self._address(MODEL_CTL),
            request_opcode=LightCTLOpcode.LIGHT_CTL_SET,
            status_opcode=LightCTLOpcode.LIGHT_CTL_STATUS,
            label="CTL",
            make_params=self._ack_params(
                {
                    "ctl_temperature": ctl_temperature,
                    "ctl_lightness": brightness,
                    "ctl_delta_uv": 0,
                    "tid": client.tid(),
                },
                transition_time,
                0,
            ),
            expected=[
                (brightness, "present_ctl_lightness", "target_ctl_lightness"),
                (ctl_temperature, "present_ctl_temperature", "target_ctl_temperature"),
            ],
        )

    @staticmethod
    def mireds_to_ctl_temperature(mireds):
        kelvin = 1_000_000 / int(mireds)
        ratio = (kelvin - MIN_KELVIN) / (MAX_KELVIN - MIN_KELVIN)
        ratio = max(0.0, min(1.0, ratio))
        return round(MIN_CTL_TEMPERATURE + ratio * (MAX_CTL_TEMPERATURE - MIN_CTL_TEMPERATURE))

    @staticmethod
    def ctl_temperature_to_mireds(temperature):
        ratio = (int(temperature) - MIN_CTL_TEMPERATURE) / (MAX_CTL_TEMPERATURE - MIN_CTL_TEMPERATURE)
        ratio = max(0.0, min(1.0, ratio))
        kelvin = MIN_KELVIN + ratio * (MAX_KELVIN - MIN_KELVIN)
        return round(1_000_000 / kelvin)

    async def _read_once(self, client, address, request_opcode, status_opcode, timeout=READ_TIMEOUT):
        status, callback_bucket, callbacks = self._expect(client, address, self.app_index, status_opcode)
        try:
            await client.send_app(address, app_index=self.app_index, opcode=request_opcode, params={})
            result = await asyncio.wait_for(status, timeout)
        finally:
            if not status.done():
                status.cancel()
            if callback_bucket is not None:
                callback_bucket.difference_update(callbacks)
        payload = self._status_payload(result, status_opcode)
        if payload is None:
            raise ValueError(f"unexpected {status_opcode} response: {result!r}")
        return payload

    async def get_onoff(self, timeout=READ_TIMEOUT):
        result = await self._read_once(
            self._client(models.GenericOnOffClient),
            self._address(MODEL_ONOFF),
            GenericOnOffOpcode.GENERIC_ONOFF_GET,
            GenericOnOffOpcode.GENERIC_ONOFF_STATUS,
            timeout,
        )
        self._state[ONOFF] = bool(result["present_onoff"])
        return result

    async def get_lightness(self, timeout=READ_TIMEOUT):
        result = await self._read_once(
            self._client(models.LightLightnessClient),
            self._address(MODEL_LIGHTNESS),
            LightLightnessOpcode.LIGHT_LIGHTNESS_GET,
            LightLightnessOpcode.LIGHT_LIGHTNESS_STATUS,
            timeout,
        )
        brightness = int(result["present_lightness"])
        self._state[BRIGHTNESS] = brightness
        if brightness > 0:
            self._last_nonzero_brightness = brightness
        return result

    async def get_ctl_temperature(self, timeout=READ_TIMEOUT):
        result = await self._read_once(
            self._client(models.LightCTLClient),
            self._address(MODEL_CTL_TEMPERATURE),
            LightCTLOpcode.LIGHT_CTL_TEMPERATURE_GET,
            LightCTLOpcode.LIGHT_CTL_TEMPERATURE_STATUS,
            timeout,
        )
        self._state[TEMPERATURE] = self.ctl_temperature_to_mireds(result["present_ctl_temperature"])
        return result

    async def _read_with_retries(self, label, read):
        global _READ_LOCK
        if _READ_LOCK is None:
            _READ_LOCK = asyncio.Lock()
        async with _READ_LOCK:
            last_error = None
            for attempt in range(1, READ_ATTEMPTS + 1):
                try:
                    result = await read()
                    LOGGER.info("%s read %s on attempt %d", self, label, attempt)
                    return result
                except Exception as exc:
                    last_error = exc
                    if attempt < READ_ATTEMPTS:
                        await asyncio.sleep(READ_RETRY_DELAY)
            LOGGER.warning(
                "%s received no valid %s reply after %d attempts: %s: %s",
                self,
                label,
                READ_ATTEMPTS,
                type(last_error).__name__,
                last_error,
            )
            return None

    async def read_state(self):
        results = {}
        onoff = await self._read_with_retries("on/off", self.get_onoff)
        if onoff is not None:
            results[ONOFF] = onoff
        lightness = await self._read_with_retries("lightness", self.get_lightness)
        if lightness is not None:
            results[BRIGHTNESS] = lightness
        if onoff is None and lightness is not None:
            derived = int(lightness["present_lightness"]) > 0
            self._state[ONOFF] = derived
            results["derived_onoff_from_brightness"] = derived
        temperature = await self._read_with_retries("CTL temperature", self.get_ctl_temperature)
        if temperature is not None:
            results[TEMPERATURE] = temperature
        return results

    @staticmethod
    def readback_delay(transition_time):
        return READBACK_DELAY if transition_time is None else float(transition_time) + 1.0
