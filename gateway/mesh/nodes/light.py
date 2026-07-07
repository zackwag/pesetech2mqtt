import asyncio
import logging
import time

from .generic import Generic

from bluetooth_mesh import models
from bluetooth_mesh.messages.generic.onoff import GenericOnOffOpcode
from bluetooth_mesh.messages.generic.light.ctl import LightCTLOpcode
from bluetooth_mesh.messages.generic.light.lightness import LightLightnessOpcode


PESETECH_VENDOR_OPCODE = 0xE31102
PESETECH_WRITE_RETRANSMISSIONS = 10
PESETECH_WRITE_INTERVAL = 0.075
PESETECH_ACK_TIMEOUT = 10.0
PESETECH_READ_ATTEMPTS = 10
PESETECH_READ_TIMEOUT = 1.25
PESETECH_READ_RETRY_DELAY = 0.15
PESETECH_READBACK_DELAY = 0.75
PESETECH_MIN_KELVIN = 1800
PESETECH_MAX_KELVIN = 10000
PESETECH_MIN_CTL_TEMPERATURE = 800
PESETECH_MAX_CTL_TEMPERATURE = 20000


class LightCTLTemperatureServer:
    """
    python-bluetooth-mesh does not define the SIG Light CTL Temperature Server,
    but Pesetech uses it as the target element for CCT changes.
    """

    MODEL_ID = (None, 0x1306)


class Light(Generic):
    """
    Generic interface for light nodes

    Tracks the available feature of the light. Currently supports
        - GenericOnOffServer
            - turn on and off
        - LightLightnessServer
            - set brightness
        - LightCTLServer
            - set color temperature

    Model commands are sent to the element that exposes the matching server.
    """

    OnOffProperty = "onoff"
    BrightnessProperty = "brightness"
    TemperatureProperty = "temperature"
    _confirm_read_lock = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._features = set()
        self._last_nonzero_brightness = None
        self._confirmation_tasks = {}
        self._standard_readback_task = None

    def supports(self, property):
        return property in self._features

    def _nonnegative_int(self, value, label):
        try:
            value = int(value)
        except (TypeError, ValueError):
            logging.warning(f"{self} ignored invalid {label} {value!r}")
            return None
        if value < 0:
            logging.warning(f"{self} ignored invalid {label} {value!r}")
            return None
        return value

    def _default_brightness(self):
        fallback = 65280 if self.type == "pesetech_skylight" else 65535
        if self.type == "pesetech_skylight":
            value = self.config.optional("brightness_scale", fallback)
        else:
            value = self.config.optional("brightness_scale", fallback)

        return self._nonnegative_int(value, "brightness scale") or fallback

    def _current_brightness(self):
        value = self._nonnegative_int(
            self.retained(Light.BrightnessProperty, self._default_brightness()),
            "retained brightness",
        )
        if value is None:
            return self._default_brightness()
        return value

    def _remember_brightness(self, brightness):
        if brightness > 0:
            self._last_nonzero_brightness = brightness

    def _notify_brightness(self, brightness):
        brightness = self._nonnegative_int(brightness, "brightness")
        if brightness is None:
            return None
        self._remember_brightness(brightness)
        self.notify(Light.BrightnessProperty, brightness)
        self.notify(Light.OnOffProperty, brightness > 0)
        return brightness

    def _restore_brightness(self):
        brightness = self._current_brightness()
        if brightness > 0:
            return brightness
        if self._last_nonzero_brightness is not None:
            return self._last_nonzero_brightness
        return self._default_brightness()

    def _mireds_to_ctl_temperature(self, mireds):
        try:
            mireds = int(mireds)
        except (TypeError, ValueError):
            logging.warning(f"{self} ignored invalid mired color temperature {mireds!r}")
            return None
        if mireds <= 0:
            logging.warning(f"{self} ignored invalid mired color temperature {mireds!r}")
            return None
        if self.type == "pesetech_skylight":
            kelvin = 1000000 / mireds
            ratio = (kelvin - PESETECH_MIN_KELVIN) / (PESETECH_MAX_KELVIN - PESETECH_MIN_KELVIN)
            ratio = max(0.0, min(1.0, ratio))
            return int(
                round(
                    PESETECH_MIN_CTL_TEMPERATURE
                    + (ratio * (PESETECH_MAX_CTL_TEMPERATURE - PESETECH_MIN_CTL_TEMPERATURE))
                )
            )
        return 1000000 // mireds

    def _ctl_temperature_to_mireds(self, temperature):
        try:
            temperature = int(temperature)
        except (TypeError, ValueError):
            logging.warning(f"{self} ignored invalid CTL temperature {temperature!r}")
            return None
        if temperature <= 0:
            logging.warning(f"{self} ignored invalid CTL temperature {temperature!r}")
            return None
        if self.type == "pesetech_skylight":
            ratio = (temperature - PESETECH_MIN_CTL_TEMPERATURE) / (
                PESETECH_MAX_CTL_TEMPERATURE - PESETECH_MIN_CTL_TEMPERATURE
            )
            ratio = max(0.0, min(1.0, ratio))
            kelvin = PESETECH_MIN_KELVIN + (ratio * (PESETECH_MAX_KELVIN - PESETECH_MIN_KELVIN))
            return int(round(1000000 / kelvin))
        return 1000000 // temperature

    def _current_ctl_temperature(self):
        temperature = self._mireds_to_ctl_temperature(self.retained(Light.TemperatureProperty, 1250))
        if temperature is not None:
            return temperature
        return self._mireds_to_ctl_temperature(1250)

    def _transition_params(self, transition_time, delay=0.0):
        if transition_time is None:
            return {}
        try:
            transition_time = float(transition_time)
            delay = float(delay)
        except (TypeError, ValueError):
            logging.warning(f"{self} ignored invalid transition {transition_time!r} delay {delay!r}")
            return {}
        if transition_time < 0 or delay < 0:
            logging.warning(f"{self} ignored invalid transition {transition_time!r} delay {delay!r}")
            return {}
        return {"transition_time": transition_time, "delay": delay}

    def _confirmation_lock(self):
        if Light._confirm_read_lock is None:
            Light._confirm_read_lock = asyncio.Lock()
        return Light._confirm_read_lock

    def _opcode_key(self, opcode):
        return getattr(opcode, "name", str(opcode)).lower()

    def _status_payload(self, status, status_opcode):
        if status is None:
            return None

        key = self._opcode_key(status_opcode)
        if hasattr(status, "get"):
            payload = status.get(key)
            if payload is not None:
                return payload

        try:
            return status[key]
        except (KeyError, TypeError):
            return None

    def _status_matches_expected(self, payload, expected):
        if not expected:
            return False, False

        present_matches = []
        target_matches = []
        for label, value, present_key, target_key in expected:
            try:
                value = int(value)
            except (TypeError, ValueError):
                logging.warning(f"{self} cannot validate {label} status against invalid value {value!r}")
                return False, False

            try:
                present_matches.append(present_key in payload and int(payload[present_key]) == value)
            except (TypeError, ValueError):
                present_matches.append(False)

            if target_key is None:
                target_matches.append(False)
                continue

            try:
                target_matches.append(target_key in payload and int(payload[target_key]) == value)
            except (TypeError, ValueError):
                target_matches.append(False)

        return all(present_matches), all(target_matches)

    def _monitor(self):
        monitor = getattr(getattr(self, "_app", None), "diagnostic_monitor", None)
        if monitor is not None and getattr(monitor, "enabled", False):
            return monitor
        return None

    async def _send_acknowledged(
        self,
        *,
        client,
        address,
        request_opcode,
        status_opcode,
        label,
        make_params,
        expected=None,
        diagnostic_command_id=None,
        send_interval=PESETECH_WRITE_INTERVAL,
        timeout=PESETECH_ACK_TIMEOUT,
    ):
        app_index = self._app.app_keys[0][0]
        monitor = self._monitor()
        attempts = 0
        started = time.monotonic()
        status = client.expect_app(
            address,
            app_index=app_index,
            destination=None,
            opcode=status_opcode,
            params={},
        )

        async def request():
            nonlocal attempts
            attempts += 1
            return await client.send_app(
                address,
                app_index=app_index,
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
        except Exception as exc:
            if hasattr(status, "cancel") and not status.done():
                status.cancel()
            logging.warning(
                f"{self} failed acknowledged {label} after {timeout:g}s: {type(exc).__name__}: {exc}"
            )
            if monitor is not None:
                monitor.record_ack_write(
                    command_id=diagnostic_command_id,
                    node=self,
                    label=label,
                    address=address,
                    request_opcode=request_opcode,
                    status_opcode=status_opcode,
                    send_interval=send_interval,
                    timeout=timeout,
                    attempts=attempts,
                    elapsed_seconds=time.monotonic() - started,
                    outcome="timeout",
                    exception=exc,
                )
            return None

        payload = self._status_payload(result, status_opcode)
        if payload is None:
            logging.warning(f"{self} acknowledged {label} returned unexpected status: {result!r}")
            if monitor is not None:
                monitor.record_ack_write(
                    command_id=diagnostic_command_id,
                    node=self,
                    label=label,
                    address=address,
                    request_opcode=request_opcode,
                    status_opcode=status_opcode,
                    send_interval=send_interval,
                    timeout=timeout,
                    attempts=attempts,
                    elapsed_seconds=time.monotonic() - started,
                    outcome="unexpected_status",
                    status_payload=result,
                )
            return None

        present_matches, target_matches = self._status_matches_expected(payload, expected)
        if expected and not (present_matches or target_matches):
            logging.warning(f"{self} acknowledged {label} but status did not prove target: {payload!r}")
            outcome = "accepted_unverified"
        else:
            logging.info(f"{self} acknowledged {label}: {payload!r}")
            outcome = "verified"

        if monitor is not None:
            monitor.record_ack_write(
                command_id=diagnostic_command_id,
                node=self,
                label=label,
                address=address,
                request_opcode=request_opcode,
                status_opcode=status_opcode,
                send_interval=send_interval,
                timeout=timeout,
                attempts=attempts,
                elapsed_seconds=time.monotonic() - started,
                outcome=outcome,
                status_payload=payload,
            )

        return payload

    async def _confirm_read(
        self,
        label,
        read,
        expected=None,
        attempts=PESETECH_READ_ATTEMPTS,
        timeout=PESETECH_READ_TIMEOUT,
        retry_delay=PESETECH_READ_RETRY_DELAY,
    ):
        async with self._confirmation_lock():
            for attempt in range(1, attempts + 1):
                try:
                    result = await read(timeout=timeout)
                except Exception as exc:
                    if attempt == attempts:
                        logging.warning(
                            f"{self} failed {label} confirmation after {attempts} attempts: "
                            f"{type(exc).__name__}: {exc}"
                        )
                        return None
                    logging.info(
                        f"{self} retrying {label} confirmation after attempt {attempt}: {type(exc).__name__}: {exc}"
                    )
                    await asyncio.sleep(retry_delay)
                    continue

                if expected is not None:
                    logging.info(f"{self} confirmed {label}: expected {expected}, read {result}")
                else:
                    logging.info(f"{self} confirmed {label}: {result}")
                return result
        return None

    async def _confirm_ctl_temperature(self):
        if self._is_model_bound(LightCTLTemperatureServer):
            result = await self._confirm_read("CTL temperature", self.get_ctl_temperature)
            if result is not None:
                return result

        if self._is_model_bound(models.LightCTLServer):
            return await self._confirm_read("CTL temperature fallback", self.get_ctl)

        return None

    async def read_standard_state(self, trigger="manual", diagnostic_command_id=None):
        """
        Best-effort physical readback for the native HA light primitives.

        Each field uses the Pesetech retry policy and stops on the first valid
        reply. A failed field is logged by _confirm_read but does not undo the
        retained desired state.
        """
        started = time.monotonic()
        results = {}

        if self._is_model_bound(models.GenericOnOffServer):
            onoff = await self._confirm_read("readback on/off", self.get_onoff)
            if onoff is not None:
                results["onoff"] = onoff

        if self._is_model_bound(models.LightLightnessServer):
            lightness = await self._confirm_read("readback lightness", self.get_lightness)
            if lightness is not None:
                results["lightness"] = lightness

        if "onoff" not in results and "lightness" in results:
            present_lightness = results["lightness"].get("present_lightness")
            if present_lightness is not None:
                derived_onoff = int(present_lightness) > 0
                self.notify(Light.OnOffProperty, derived_onoff)
                results["derived_onoff_from_lightness"] = derived_onoff

        if self._is_model_bound(LightCTLTemperatureServer) or self._is_model_bound(models.LightCTLServer):
            ctl_temperature = await self._confirm_ctl_temperature()
            if ctl_temperature is not None:
                results["ctl_temperature"] = ctl_temperature

        if not results:
            logging.warning(f"{self} standard readback received no valid replies")

        monitor = self._monitor()
        if monitor is not None:
            monitor.record_readback(
                command_id=diagnostic_command_id,
                node=self,
                trigger=trigger,
                results=results,
                elapsed_seconds=time.monotonic() - started,
                outcome="passed" if results else "no_valid_replies",
            )

        return results

    def _command_readback_delay(self, transition_time):
        if transition_time is None:
            return PESETECH_READBACK_DELAY
        try:
            transition_time = float(transition_time)
        except (TypeError, ValueError):
            return PESETECH_READBACK_DELAY
        if transition_time < 0:
            return PESETECH_READBACK_DELAY
        return transition_time + 1.0

    def schedule_standard_readback(self, transition_time=None, diagnostic_command_id=None):
        previous = self._standard_readback_task
        if previous is not None:
            previous.cancel()

        task = None

        async def runner():
            try:
                await asyncio.sleep(self._command_readback_delay(transition_time))
                await self.read_standard_state(
                    trigger="post_command",
                    diagnostic_command_id=diagnostic_command_id,
                )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logging.warning(f"{self} standard readback failed: {type(exc).__name__}: {exc}")
                monitor = self._monitor()
                if monitor is not None:
                    monitor.record_readback(
                        command_id=diagnostic_command_id,
                        node=self,
                        trigger="post_command",
                        results={},
                        elapsed_seconds=0,
                        outcome="error",
                    )
            finally:
                if task is not None and self._standard_readback_task is task:
                    self._standard_readback_task = None

        task = asyncio.create_task(runner())
        self._standard_readback_task = task
        return task

    async def _confirm_onoff(self, expected):
        result = await self._confirm_read("on/off", self.get_onoff, expected=expected)
        if result is None:
            return
        actual = result.get("present_onoff")
        if actual is not None and bool(actual) != bool(expected):
            logging.warning(f"{self} on/off confirmation mismatch: expected {expected}, read {actual}")

    async def _confirm_lightness(self, expected):
        result = await self._confirm_read("lightness", self.get_lightness, expected=expected)
        if result is None:
            return
        actual = result.get("present_lightness")
        if actual is not None and int(actual) != int(expected):
            logging.warning(f"{self} lightness confirmation mismatch: expected {expected}, read {actual}")

    def _schedule_confirmation(self, key, confirm):
        previous = self._confirmation_tasks.get(key)
        if previous is not None:
            previous.cancel()

        task = None

        async def runner():
            try:
                await asyncio.sleep(0.5)
                await confirm()
            except asyncio.CancelledError:
                return
            finally:
                if task is not None and self._confirmation_tasks.get(key) is task:
                    self._confirmation_tasks.pop(key, None)

        task = asyncio.create_task(runner())
        self._confirmation_tasks[key] = task

    def schedule_lightness_confirmation(self, expected):
        self._schedule_confirmation("lightness", lambda: self._confirm_lightness(expected))

    async def turn_on(self, confirm=True, transition_time=None, diagnostic_command_id=None):
        if self._is_model_bound(models.GenericOnOffServer):
            logging.info(f"{self} turning on with Generic OnOff transition={transition_time}")
            return await self.set_onoff(
                True,
                transition_time=transition_time,
                diagnostic_command_id=diagnostic_command_id,
            )
        elif self._is_model_bound(models.LightLightnessServer):
            return await self.set_lightness(
                self._restore_brightness(),
                transition_time=transition_time,
                diagnostic_command_id=diagnostic_command_id,
            )
        elif self._is_model_bound(models.LightCTLServer):
            return await self.set_ctl(
                brightness=self._restore_brightness(),
                transition_time=transition_time,
                diagnostic_command_id=diagnostic_command_id,
            )
        else:
            logging.warning(f"{self} has no bound model capable of turning on")

    async def turn_off(self, confirm=True, transition_time=None, diagnostic_command_id=None):
        if self._is_model_bound(models.GenericOnOffServer):
            logging.info(f"{self} turning off with Generic OnOff transition={transition_time}")
            return await self.set_onoff(
                False,
                transition_time=transition_time,
                diagnostic_command_id=diagnostic_command_id,
            )
        elif self._is_model_bound(models.LightLightnessServer):
            return await self.set_lightness(
                0,
                transition_time=transition_time,
                diagnostic_command_id=diagnostic_command_id,
            )
        elif self._is_model_bound(models.LightCTLServer):
            return await self.set_ctl(
                brightness=0,
                transition_time=transition_time,
                diagnostic_command_id=diagnostic_command_id,
            )
        else:
            logging.warning(f"{self} has no bound model capable of turning off")

    async def set_brightness(self, brightness, confirm=True, transition_time=None, diagnostic_command_id=None):
        if self._is_model_bound(models.LightLightnessServer):
            if self.type == "pesetech_skylight":
                logging.info(
                    f"{self} setting brightness with Pesetech Light Lightness Set: "
                    f"{brightness}, transition={transition_time}"
                )
                status = await self.set_lightness_simple(
                    brightness,
                    transition_time=transition_time,
                    diagnostic_command_id=diagnostic_command_id,
                )
                if status is not None and transition_time in (None, 0, 0.0):
                    logging.info(f"{self} setting brightness with Pesetech vendor runtime command: {brightness}")
                    await self.set_pesetech_runtime_brightness(
                        brightness,
                        diagnostic_command_id=diagnostic_command_id,
                    )
                elif status is None:
                    logging.info(f"{self} skipped Pesetech vendor runtime brightness because Lightness Set was not acked")
                else:
                    logging.info(f"{self} skipped Pesetech vendor runtime brightness during transition")
                return status
            else:
                return await self.set_lightness(
                    brightness,
                    transition_time=transition_time,
                    diagnostic_command_id=diagnostic_command_id,
                )
        elif self._is_model_bound(models.LightCTLServer):
            logging.info(f"{self} setting brightness with Light CTL Set: {brightness}, transition={transition_time}")
            return await self.set_ctl(
                brightness=brightness,
                transition_time=transition_time,
                diagnostic_command_id=diagnostic_command_id,
            )

    async def set_brightness_mireds(
        self,
        brightness,
        mireds,
        confirm=True,
        transition_time=None,
        diagnostic_command_id=None,
    ):
        ctl_temperature = self._mireds_to_ctl_temperature(mireds)
        if ctl_temperature is None:
            return
        if self._is_model_bound(models.LightCTLServer):
            logging.info(
                f"{self} setting brightness+CCT with Light CTL Set: "
                f"brightness={brightness}, mireds={mireds}, transition={transition_time}"
            )
            return await self.set_ctl(
                temperature=ctl_temperature,
                brightness=brightness,
                transition_time=transition_time,
                diagnostic_command_id=diagnostic_command_id,
            )
        else:
            await self.set_mireds(
                mireds,
                transition_time=transition_time,
                diagnostic_command_id=diagnostic_command_id,
            )
            return await self.set_brightness(
                brightness,
                confirm=confirm,
                transition_time=transition_time,
                diagnostic_command_id=diagnostic_command_id,
            )

    async def set_kelvin(self, temperature, transition_time=None, diagnostic_command_id=None):
        if self._is_model_bound(models.LightCTLServer) or self._is_model_bound(LightCTLTemperatureServer):
            await self.set_ctl_temperature(
                temperature,
                transition_time=transition_time,
                diagnostic_command_id=diagnostic_command_id,
            )

    async def set_mireds(self, temperature, transition_time=None, diagnostic_command_id=None):
        if self._is_model_bound(models.LightCTLServer) or self._is_model_bound(LightCTLTemperatureServer):
            ctl_temperature = self._mireds_to_ctl_temperature(temperature)
            if ctl_temperature is not None:
                await self.set_ctl_temperature(
                    ctl_temperature,
                    transition_time=transition_time,
                    diagnostic_command_id=diagnostic_command_id,
                )

    async def bind(self, app):
        await super().bind(app)

        has_onoff = await self.bind_model(models.GenericOnOffServer)
        has_lightness = await self.bind_model(models.LightLightnessServer)
        has_ctl = await self.bind_model(models.LightCTLServer)
        has_ctl_temperature = await self.bind_model(LightCTLTemperatureServer)

        if has_onoff:
            self._features.add(Light.OnOffProperty)
            if not self.using_imported_models():
                await self._try_initial_state_read("on/off", self.get_onoff)

        if has_lightness:
            self._features.add(Light.OnOffProperty)
            self._features.add(Light.BrightnessProperty)
            if not self.using_imported_models():
                await self._try_initial_state_read("lightness", self.get_lightness)

        if has_ctl:
            self._features.add(Light.TemperatureProperty)
            self._features.add(Light.BrightnessProperty)

        if has_ctl_temperature:
            self._features.add(Light.TemperatureProperty)

        if not self._features:
            raise RuntimeError(f"{self} has no bound light models")

        if has_ctl and not self.using_imported_models():
            await self._try_initial_state_read("CTL", self.get_ctl)

    def _ctl_temperature_set_server(self):
        if self._is_model_bound(LightCTLTemperatureServer):
            return LightCTLTemperatureServer

        return models.LightCTLServer

    async def _try_initial_state_read(self, label, read):
        try:
            await read()
        except Exception as exc:
            logging.warning(
                f"{self} failed initial {label} state read; continuing with defaults: "
                f"{type(exc).__name__}: {exc}"
            )

    async def set_onoff(self, onoff, **kwargs):
        self.notify(Light.OnOffProperty, onoff)

        client = self._app.elements[0][models.GenericOnOffClient]
        address = self._model_address(models.GenericOnOffServer)
        tid = client.tid()
        transition_time = kwargs.pop("transition_time", None)
        delay = kwargs.pop("delay", 0.0)
        diagnostic_command_id = kwargs.pop("diagnostic_command_id", None)
        send_interval = kwargs.pop("send_interval", PESETECH_WRITE_INTERVAL)
        timeout = kwargs.pop("timeout", PESETECH_ACK_TIMEOUT)
        current_delay = delay

        def params():
            nonlocal current_delay
            payload = dict(onoff=1 if onoff else 0, tid=tid)
            payload.update(self._transition_params(transition_time, current_delay))
            current_delay = max(0.0, current_delay - send_interval)
            return payload

        return await self._send_acknowledged(
            client=client,
            address=address,
            request_opcode=GenericOnOffOpcode.GENERIC_ONOFF_SET,
            status_opcode=GenericOnOffOpcode.GENERIC_ONOFF_STATUS,
            label="on/off",
            make_params=params,
            expected=[("on/off", 1 if onoff else 0, "present_onoff", "target_onoff")],
            diagnostic_command_id=diagnostic_command_id,
            send_interval=send_interval,
            timeout=timeout,
        )

    async def set_onoff_unack(self, onoff, **kwargs):
        self.notify(Light.OnOffProperty, onoff)

        client = self._app.elements[0][models.GenericOnOffClient]
        address = self._model_address(models.GenericOnOffServer)
        tid = client.tid()
        transition_time = kwargs.pop("transition_time", None)
        delay = kwargs.pop("delay", 0.0)
        retransmissions = kwargs.pop("retransmissions", PESETECH_WRITE_RETRANSMISSIONS)
        send_interval = kwargs.pop("send_interval", PESETECH_WRITE_INTERVAL)
        current_delay = delay

        async def request():
            nonlocal current_delay
            params = dict(onoff=1 if onoff else 0, tid=tid)
            params.update(self._transition_params(transition_time, current_delay))
            result = await client.send_app(
                address,
                app_index=self._app.app_keys[0][0],
                opcode=GenericOnOffOpcode.GENERIC_ONOFF_SET_UNACKNOWLEDGED,
                params=params,
            )
            current_delay = max(0.0, current_delay - send_interval)
            return result

        await client.repeat(request, retransmissions=retransmissions, send_interval=send_interval)

    async def get_onoff(self, timeout=None):
        client = self._app.elements[0][models.GenericOnOffClient]
        address = self._model_address(models.GenericOnOffServer)
        state = await client.get_light_status([address], self._app.app_keys[0][0], timeout=timeout)

        result = state.get(address) if isinstance(state, dict) else None
        if result is None:
            raise TimeoutError(f"No Generic OnOff Status reply from {address:04x}; result was {state!r}")
        if isinstance(result, BaseException):
            raise result
        if "present_onoff" not in result:
            raise ValueError(f"Generic OnOff Status missing present_onoff: {result!r}")

        self.notify(Light.OnOffProperty, result["present_onoff"])
        return result

    async def set_lightness_unack(self, lightness, **kwargs):
        await self.set_lightness_simple_unack(lightness, **kwargs)

    async def set_lightness(self, lightness, **kwargs):
        return await self.set_lightness_simple(lightness, **kwargs)

    async def set_lightness_simple(self, lightness, **kwargs):
        lightness = self._notify_brightness(lightness)
        if lightness is None:
            return None

        client = self._app.elements[0][models.LightLightnessClient]
        address = self._model_address(models.LightLightnessServer)
        tid = client.tid()
        transition_time = kwargs.pop("transition_time", None)
        delay = kwargs.pop("delay", 0.0)
        diagnostic_command_id = kwargs.pop("diagnostic_command_id", None)
        send_interval = kwargs.pop("send_interval", PESETECH_WRITE_INTERVAL)
        timeout = kwargs.pop("timeout", PESETECH_ACK_TIMEOUT)
        current_delay = delay

        def params():
            nonlocal current_delay
            payload = dict(lightness=lightness, tid=tid)
            payload.update(self._transition_params(transition_time, current_delay))
            current_delay = max(0.0, current_delay - send_interval)
            return payload

        return await self._send_acknowledged(
            client=client,
            address=address,
            request_opcode=LightLightnessOpcode.LIGHT_LIGHTNESS_SET,
            status_opcode=LightLightnessOpcode.LIGHT_LIGHTNESS_STATUS,
            label="lightness",
            make_params=params,
            expected=[("lightness", lightness, "present_lightness", "target_lightness")],
            diagnostic_command_id=diagnostic_command_id,
            send_interval=send_interval,
            timeout=timeout,
        )

    async def set_lightness_simple_unack(self, lightness, **kwargs):
        lightness = self._notify_brightness(lightness)
        if lightness is None:
            return

        client = self._app.elements[0][models.LightLightnessClient]
        address = self._model_address(models.LightLightnessServer)
        tid = client.tid()
        transition_time = kwargs.pop("transition_time", None)
        delay = kwargs.pop("delay", 0.0)
        retransmissions = kwargs.pop("retransmissions", PESETECH_WRITE_RETRANSMISSIONS)
        send_interval = kwargs.pop("send_interval", PESETECH_WRITE_INTERVAL)
        current_delay = delay

        async def request():
            nonlocal current_delay
            params = dict(lightness=lightness, tid=tid)
            params.update(self._transition_params(transition_time, current_delay))
            result = await client.send_app(
                address,
                app_index=self._app.app_keys[0][0],
                opcode=LightLightnessOpcode.LIGHT_LIGHTNESS_SET_UNACKNOWLEDGED,
                params=params,
            )
            current_delay = max(0.0, current_delay - send_interval)
            return result

        await client.repeat(request, retransmissions=retransmissions, send_interval=send_interval)

    async def set_ctl(self, temperature=None, brightness=None, **kwargs):
        if temperature is not None:
            mireds = self._ctl_temperature_to_mireds(temperature)
            if mireds is None:
                return None
            temperature = int(temperature)
            self.notify(Light.TemperatureProperty, mireds)
        else:
            temperature = self._current_ctl_temperature()
        if brightness is not None:
            brightness = self._notify_brightness(brightness)
            if brightness is None:
                return None
        else:
            brightness = self._restore_brightness()

        client = self._app.elements[0][models.LightCTLClient]
        address = self._model_address(models.LightCTLServer)
        tid = client.tid()
        transition_time = kwargs.pop("transition_time", None)
        delay = kwargs.pop("delay", 0.0)
        diagnostic_command_id = kwargs.pop("diagnostic_command_id", None)
        send_interval = kwargs.pop("send_interval", PESETECH_WRITE_INTERVAL)
        timeout = kwargs.pop("timeout", PESETECH_ACK_TIMEOUT)
        current_delay = delay

        def params():
            nonlocal current_delay
            payload = dict(
                ctl_temperature=temperature,
                ctl_lightness=brightness,
                ctl_delta_uv=0,
                tid=tid,
            )
            payload.update(self._transition_params(transition_time, current_delay))
            current_delay = max(0.0, current_delay - send_interval)
            return payload

        return await self._send_acknowledged(
            client=client,
            address=address,
            request_opcode=LightCTLOpcode.LIGHT_CTL_SET,
            status_opcode=LightCTLOpcode.LIGHT_CTL_STATUS,
            label="CTL",
            make_params=params,
            expected=[
                ("CTL lightness", brightness, "present_ctl_lightness", "target_ctl_lightness"),
                ("CTL temperature", temperature, "present_ctl_temperature", "target_ctl_temperature"),
            ],
            diagnostic_command_id=diagnostic_command_id,
            send_interval=send_interval,
            timeout=timeout,
        )

    async def set_ctl_unack(self, temperature=None, brightness=None, **kwargs):
        if temperature is not None:
            mireds = self._ctl_temperature_to_mireds(temperature)
            if mireds is None:
                return
            temperature = int(temperature)
            self.notify(Light.TemperatureProperty, mireds)
        else:
            temperature = self._current_ctl_temperature()
        if brightness is not None:
            brightness = self._notify_brightness(brightness)
            if brightness is None:
                return
        else:
            brightness = self._restore_brightness()

        client = self._app.elements[0][models.LightCTLClient]
        address = self._model_address(models.LightCTLServer)
        tid = client.tid()
        transition_time = kwargs.pop("transition_time", None)
        delay = kwargs.pop("delay", 0.0)
        retransmissions = kwargs.pop("retransmissions", PESETECH_WRITE_RETRANSMISSIONS)
        send_interval = kwargs.pop("send_interval", PESETECH_WRITE_INTERVAL)
        current_delay = delay

        async def request():
            nonlocal current_delay
            params = dict(
                ctl_temperature=temperature,
                ctl_lightness=brightness,
                ctl_delta_uv=0,
                tid=tid,
            )
            params.update(self._transition_params(transition_time, current_delay))
            result = await client.send_app(
                address,
                app_index=self._app.app_keys[0][0],
                opcode=LightCTLOpcode.LIGHT_CTL_SET_UNACKNOWLEDGED,
                params=params,
            )
            current_delay = max(0.0, current_delay - send_interval)
            return result

        await client.repeat(request, retransmissions=retransmissions, send_interval=send_interval)

    def _pesetech_runtime_brightness_payload(self, lightness):
        lightness = max(0, min(65535, int(lightness)))
        # Telink app helper builds an "a0ff" runtime payload with the lightness
        # field little-endian. Its displayed opcode "0211e3" is sent as e3 11 02.
        return (
            b"\xa0\xff"
            + bytes([0])
            + b"\x00\x00"
            + b"\x00\x00"
            + lightness.to_bytes(2, byteorder="little")
            + b"\x00\x00"
            + b"\x00\x00"
            + b"\x00\x00"
            + b"\x00\x00"
            + b"\x00\x00"
            + b"\x00\x00\x00\x00"
        )

    async def set_pesetech_runtime_brightness(self, lightness, **kwargs):
        lightness = self._notify_brightness(lightness)
        if lightness is None:
            return

        client = self._app.elements[0][models.LightLightnessClient]
        address = self._model_address(models.LightLightnessServer)
        payload = self._pesetech_runtime_brightness_payload(lightness)
        diagnostic_command_id = kwargs.pop("diagnostic_command_id", None)
        retransmissions = kwargs.pop("retransmissions", PESETECH_WRITE_RETRANSMISSIONS)
        send_interval = kwargs.pop("send_interval", PESETECH_WRITE_INTERVAL)

        async def request():
            return await client.send_app(
                address,
                app_index=self._app.app_keys[0][0],
                opcode=PESETECH_VENDOR_OPCODE,
                params=payload,
            )

        await client.repeat(request, retransmissions=retransmissions, send_interval=send_interval)
        monitor = self._monitor()
        if monitor is not None:
            monitor.record_unack_write(
                command_id=diagnostic_command_id,
                node=self,
                label="pesetech runtime brightness",
                address=address,
                opcode=PESETECH_VENDOR_OPCODE,
                retransmissions=retransmissions,
                send_interval=send_interval,
            )

    async def get_lightness(self, timeout=None):
        client = self._app.elements[0][models.LightLightnessClient]
        address = self._model_address(models.LightLightnessServer)
        state = await client.get_lightness([address], self._app.app_keys[0][0], timeout=timeout)

        result = state.get(address) if isinstance(state, dict) else None
        if result is None:
            raise TimeoutError(f"No Light Lightness Status reply from {address:04x}; result was {state!r}")
        if isinstance(result, BaseException):
            raise result
        if "present_lightness" not in result:
            raise ValueError(f"Light Lightness Status missing present_lightness: {result!r}")

        self._notify_brightness(result["present_lightness"])
        return result

    async def set_ctl_temperature(self, temperature, **kwargs):
        mireds = self._ctl_temperature_to_mireds(temperature)
        if mireds is None:
            return None
        temperature = int(temperature)
        self.notify(Light.TemperatureProperty, mireds)

        client = self._app.elements[0][models.LightCTLClient]
        address = self._model_address(self._ctl_temperature_set_server())
        tid = client.tid()
        transition_time = kwargs.pop("transition_time", None)
        delay = kwargs.pop("delay", 0.0)
        diagnostic_command_id = kwargs.pop("diagnostic_command_id", None)
        send_interval = kwargs.pop("send_interval", PESETECH_WRITE_INTERVAL)
        timeout = kwargs.pop("timeout", PESETECH_ACK_TIMEOUT)
        current_delay = delay

        def params():
            nonlocal current_delay
            payload = dict(ctl_temperature=temperature, ctl_delta_uv=0, tid=tid)
            payload.update(self._transition_params(transition_time, current_delay))
            current_delay = max(0.0, current_delay - send_interval)
            return payload

        return await self._send_acknowledged(
            client=client,
            address=address,
            request_opcode=LightCTLOpcode.LIGHT_CTL_TEMPERATURE_SET,
            status_opcode=LightCTLOpcode.LIGHT_CTL_TEMPERATURE_STATUS,
            label="CTL temperature",
            make_params=params,
            expected=[("CTL temperature", temperature, "present_ctl_temperature", "target_ctl_temperature")],
            diagnostic_command_id=diagnostic_command_id,
            send_interval=send_interval,
            timeout=timeout,
        )

    async def set_ctl_temperature_unack(self, temperature, **kwargs):
        mireds = self._ctl_temperature_to_mireds(temperature)
        if mireds is None:
            return
        temperature = int(temperature)
        self.notify(Light.TemperatureProperty, mireds)

        client = self._app.elements[0][models.LightCTLClient]
        address = self._model_address(self._ctl_temperature_set_server())
        tid = client.tid()
        transition_time = kwargs.pop("transition_time", None)
        delay = kwargs.pop("delay", 0.0)
        retransmissions = kwargs.pop("retransmissions", PESETECH_WRITE_RETRANSMISSIONS)
        send_interval = kwargs.pop("send_interval", PESETECH_WRITE_INTERVAL)
        current_delay = delay

        async def request():
            nonlocal current_delay
            params = dict(ctl_temperature=temperature, ctl_delta_uv=0, tid=tid)
            params.update(self._transition_params(transition_time, current_delay))
            result = await client.send_app(
                address,
                app_index=self._app.app_keys[0][0],
                opcode=LightCTLOpcode.LIGHT_CTL_TEMPERATURE_SET_UNACKNOWLEDGED,
                params=params,
            )
            current_delay = max(0.0, current_delay - send_interval)
            return result

        await client.repeat(request, retransmissions=retransmissions, send_interval=send_interval)

    async def get_ctl_temperature(self, timeout=None):
        if not self._is_model_bound(LightCTLTemperatureServer):
            raise RuntimeError(f"{self} has no Light CTL Temperature Server for state read")

        client = self._app.elements[0][models.LightCTLClient]
        address = self._model_address(LightCTLTemperatureServer)
        state = await client.get_ctl([address], self._app.app_keys[0][0], timeout=timeout)

        result = state.get(address) if isinstance(state, dict) else None
        if result is None:
            raise TimeoutError(f"No Light CTL Temperature Status reply from {address:04x}; result was {state!r}")
        if isinstance(result, BaseException):
            raise result
        if "present_ctl_temperature" not in result:
            raise ValueError(f"Light CTL Temperature Status missing present_ctl_temperature: {result!r}")

        temperature = result.get("present_ctl_temperature")
        mireds = self._ctl_temperature_to_mireds(temperature)
        if mireds is not None:
            self.notify(Light.TemperatureProperty, mireds)

        lightness = result.get("present_ctl_lightness")
        if lightness is not None:
            self._notify_brightness(lightness)
        return result

    async def get_ctl(self, timeout=None):
        if not self._is_model_bound(models.LightCTLServer):
            logging.info(f"{self} has no Light CTL Server for state read")
            return

        client = self._app.elements[0][models.LightCTLClient]
        address = self._model_address(models.LightCTLServer)
        state = await client.get_ctl([address], self._app.app_keys[0][0], timeout=timeout)

        result = state.get(address) if isinstance(state, dict) else None
        if result is None:
            raise TimeoutError(f"No Light CTL Temperature Status reply from {address:04x}; result was {state!r}")
        if isinstance(result, BaseException):
            raise result
        if "present_ctl_temperature" not in result and "present_ctl_lightness" not in result:
            raise ValueError(f"Light CTL Temperature Status missing present state fields: {result!r}")

        temperature = result.get("present_ctl_temperature")
        if temperature is not None:
            mireds = self._ctl_temperature_to_mireds(temperature)
            if mireds is not None:
                self.notify(Light.TemperatureProperty, mireds)

        lightness = result.get("present_ctl_lightness")
        if lightness is not None:
            self._notify_brightness(lightness)
        return result
