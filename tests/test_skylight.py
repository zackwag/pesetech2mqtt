import asyncio
import unittest
from collections import defaultdict
from unittest.mock import AsyncMock

from support import install_stubs

STUBS = install_stubs()

from app import skylight


class FakeClient:
    def __init__(self, ack_after=1, timeout=False, status_payload=None):
        self.ack_after = ack_after
        self.timeout = timeout
        self.status_payload = status_payload
        self.sent = []
        self.query_calls = []
        self.app_message_callbacks = defaultdict(set)
        self._future = None
        self._status_opcode = None

    def tid(self):
        return 42

    def expect_app(self, source, app_index, destination, opcode, params):
        self._future = asyncio.get_running_loop().create_future()
        self._status_opcode = opcode
        self.app_message_callbacks[opcode].add(object())
        return self._future

    def response_payload(self):
        if self.status_payload is not None:
            return self.status_payload
        params = self.sent[-1]["params"]
        name = self._status_opcode.name
        if name == "GENERIC_ONOFF_STATUS":
            return {"present_onoff": params["onoff"], "target_onoff": params["onoff"]}
        if name == "LIGHT_LIGHTNESS_STATUS":
            return {"present_lightness": params["lightness"], "target_lightness": params["lightness"]}
        if name == "LIGHT_CTL_STATUS":
            return {
                "present_ctl_lightness": params["ctl_lightness"],
                "target_ctl_lightness": params["ctl_lightness"],
                "present_ctl_temperature": params["ctl_temperature"],
                "target_ctl_temperature": params["ctl_temperature"],
            }
        return {
            "present_ctl_temperature": params.get("ctl_temperature", 800),
            "target_ctl_temperature": params.get("ctl_temperature", 800),
        }

    async def send_app(self, address, app_index, opcode, params):
        self.sent.append({"address": address, "app_index": app_index, "opcode": opcode, "params": params})
        if opcode in {
            STUBS.GenericOnOffOpcode.GENERIC_ONOFF_GET,
            STUBS.LightLightnessOpcode.LIGHT_LIGHTNESS_GET,
            STUBS.LightCTLOpcode.LIGHT_CTL_TEMPERATURE_GET,
        }:
            self._future.set_result({self._status_opcode.name.lower(): self.response_payload()})

    async def query(self, request, status, send_interval, timeout):
        self.query_calls.append((send_interval, timeout))
        if self.timeout:
            raise asyncio.TimeoutError
        for _ in range(self.ack_after):
            await request()
        status.set_result({self._status_opcode.name.lower(): self.response_payload()})
        return await status


class FakeApp:
    def __init__(self, onoff=None, lightness=None, ctl=None):
        self.app_keys = [(0, 0, object())]
        self.elements = {
            0: {
                STUBS.models.GenericOnOffClient: onoff or FakeClient(),
                STUBS.models.LightLightnessClient: lightness or FakeClient(),
                STUBS.models.LightCTLClient: ctl or FakeClient(),
            }
        }


def make_node(app=None):
    node = skylight.PesetechSkylight(
        "00112233-4455-6677-8899-aabbccddeeff",
        {
            "unicast": 0x0200,
            "count": 4,
            "imported_models": {"1000": 0x0200, "1300": 0x0200, "1303": 0x0201, "1306": 0x0202},
        },
        {"id": "skylight_a", "name": "Skylight A", "default_entity_id": "light.skylight_a"},
    )
    node._app = app or FakeApp()
    return node


class SkylightTest(unittest.IsolatedAsyncioTestCase):
    async def test_proven_routes_use_acknowledged_opcodes_and_addresses(self):
        app = FakeApp()
        node = make_node(app)

        await node.turn_off(transition_time=3)
        await node.set_brightness(1234, transition_time=4)
        await node.set_mireds(200, transition_time=5)
        await node.set_brightness_mireds(2345, 300, transition_time=6)

        onoff = app.elements[0][STUBS.models.GenericOnOffClient].sent[0]
        lightness = app.elements[0][STUBS.models.LightLightnessClient].sent[0]
        ctl_messages = app.elements[0][STUBS.models.LightCTLClient].sent
        self.assertEqual((onoff["opcode"], onoff["address"]), (STUBS.GenericOnOffOpcode.GENERIC_ONOFF_SET, 0x0200))
        self.assertEqual(
            (lightness["opcode"], lightness["address"]),
            (STUBS.LightLightnessOpcode.LIGHT_LIGHTNESS_SET, 0x0200),
        )
        self.assertEqual(
            (ctl_messages[0]["opcode"], ctl_messages[0]["address"]),
            (STUBS.LightCTLOpcode.LIGHT_CTL_TEMPERATURE_SET, 0x0202),
        )
        self.assertEqual(
            (ctl_messages[1]["opcode"], ctl_messages[1]["address"]),
            (STUBS.LightCTLOpcode.LIGHT_CTL_SET, 0x0201),
        )
        self.assertEqual([onoff["params"]["transition_time"], lightness["params"]["transition_time"]], [3.0, 4.0])
        self.assertEqual([message["params"]["transition_time"] for message in ctl_messages], [5.0, 6.0])

    async def test_ack_policy_retries_every_75ms_and_stops_on_reply(self):
        lightness = FakeClient(ack_after=3)
        node = make_node(FakeApp(lightness=lightness))

        await node.set_brightness(1234, transition_time=1)

        self.assertEqual(len(lightness.sent), 3)
        self.assertEqual(lightness.query_calls, [(0.075, 10.0)])

    async def test_ack_timeout_is_fatal(self):
        node = make_node(FakeApp(onoff=FakeClient(timeout=True)))

        with self.assertRaisesRegex(TimeoutError, "received no on/off acknowledgement after 10s"):
            await node.turn_on()

    async def test_unverified_status_is_still_accepted(self):
        node = make_node(FakeApp(onoff=FakeClient(status_payload={"present_onoff": 0})))

        result = await node.turn_on()

        self.assertEqual(result, {"present_onoff": 0})

    async def test_immediate_brightness_sends_vendor_packet_after_ack(self):
        lightness = FakeClient()
        node = make_node(FakeApp(lightness=lightness))

        await node.set_brightness(0x1234)

        self.assertEqual(lightness.sent[0]["opcode"], STUBS.LightLightnessOpcode.LIGHT_LIGHTNESS_SET)
        vendor = lightness.sent[1:]
        self.assertEqual(len(vendor), 10)
        self.assertTrue(all(message["opcode"] == skylight.PESETECH_VENDOR_OPCODE for message in vendor))
        self.assertEqual(vendor[0]["params"], bytes.fromhex("a0ff000000000034120000000000000000000000000000"))

    async def test_readback_uses_direct_temperature_and_derives_onoff(self):
        node = make_node()
        node.get_onoff = AsyncMock(side_effect=TimeoutError("quiet"))
        node.get_lightness = AsyncMock(return_value={"present_lightness": 100})
        node.get_ctl_temperature = AsyncMock(return_value={"present_ctl_temperature": 800})
        original_delay = skylight.READ_RETRY_DELAY
        skylight.READ_RETRY_DELAY = 0
        try:
            result = await node.read_state()
        finally:
            skylight.READ_RETRY_DELAY = original_delay

        self.assertTrue(result["derived_onoff_from_brightness"])
        self.assertEqual(node.retained(skylight.ONOFF, None), True)
        self.assertEqual(node.get_onoff.await_count, 10)
        node.get_ctl_temperature.assert_awaited_once()


    async def test_mireds_to_ctl_temperature_roundtrip(self):
        convert = skylight.PesetechSkylight.mireds_to_ctl_temperature
        inverse = skylight.PesetechSkylight.ctl_temperature_to_mireds
        for mireds in (153, 250, 370, 500):
            ctl = convert(mireds)
            back = inverse(ctl)
            self.assertAlmostEqual(
                back, mireds, delta=1,
                msg=f"roundtrip failed for mireds={mireds}: ctl={ctl}, back={back}",
            )

    async def test_mireds_to_ctl_temperature_boundary(self):
        convert = skylight.PesetechSkylight.mireds_to_ctl_temperature
        ctl_at_min_mired = convert(skylight.MIN_MIRED)
        self.assertGreaterEqual(ctl_at_min_mired, skylight.MIN_CTL_TEMPERATURE)
        self.assertLessEqual(ctl_at_min_mired, skylight.MAX_CTL_TEMPERATURE)
        ctl_at_max_mired = convert(skylight.MAX_MIRED)
        self.assertGreaterEqual(ctl_at_max_mired, skylight.MIN_CTL_TEMPERATURE)
        self.assertLessEqual(ctl_at_max_mired, skylight.MAX_CTL_TEMPERATURE)

    async def test_ctl_temperature_to_mireds_boundary(self):
        inverse = skylight.PesetechSkylight.ctl_temperature_to_mireds
        mireds_at_min = inverse(skylight.MIN_CTL_TEMPERATURE)
        self.assertEqual(mireds_at_min, round(1_000_000 / skylight.MIN_KELVIN))
        mireds_at_max = inverse(skylight.MAX_CTL_TEMPERATURE)
        self.assertEqual(mireds_at_max, round(1_000_000 / skylight.MAX_KELVIN))

    async def test_vendor_brightness_payload_clamping(self):
        payload_fn = skylight.PesetechSkylight._vendor_brightness_payload
        p0 = payload_fn(0)
        self.assertEqual(p0[7:9], (0).to_bytes(2, "little"))
        p255 = payload_fn(255)
        self.assertEqual(p255[7:9], (255).to_bytes(2, "little"))
        p_neg = payload_fn(-10)
        self.assertEqual(p_neg[7:9], (0).to_bytes(2, "little"))
        p_max = payload_fn(65535)
        self.assertEqual(p_max[7:9], (65535).to_bytes(2, "little"))
        p_over = payload_fn(70000)
        self.assertEqual(p_over[7:9], (65535).to_bytes(2, "little"))

    async def test_set_desired_off_state(self):
        node = make_node()
        node.set_desired(onoff=False)
        payload = node.state_payload()
        self.assertEqual(payload["state"], "OFF")

    async def test_set_desired_zero_brightness_implies_off(self):
        node = make_node()
        node.set_desired(brightness=0)
        self.assertFalse(node.retained(skylight.ONOFF, True))
        payload = node.state_payload()
        self.assertEqual(payload["state"], "OFF")

    async def test_state_payload_when_off(self):
        node = make_node()
        node.set_desired(onoff=False)
        payload = node.state_payload()
        self.assertEqual(payload["state"], "OFF")
        self.assertEqual(payload["color_mode"], "color_temp")
        self.assertNotIn("brightness", payload)
        self.assertNotIn("color_temp", payload)


if __name__ == "__main__":
    unittest.main()
