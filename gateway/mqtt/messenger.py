import json
import logging

from asyncio_mqtt.client import Client, MqttError
from contextlib import AsyncExitStack

from mesh import Node
from tools import Tasks

from .bridges import light
from .bridges.skylight_programs import SkylightProgramsMqttBridge


BRIDGES = {
    "light": light.GenericLightBridge,
    "pesetech_skylight": light.PesetechSkylightBridge,
}


class HassMqttMessenger:
    """
    Provides home assistant specific MQTT functionality

    Manages a set of bridges for specific device types and
    manages tasks to receive and handle incoming messages.
    """

    def __init__(self, config, nodes, diagnostic_monitor=None):
        self._config = config
        self._nodes = nodes
        self._diagnostic_monitor = diagnostic_monitor
        self._bridges = {}
        self._paths = {}

        self._client = Client(
            self._config.require("mqtt.broker"),
            port=int(self._config.optional("mqtt.port", 1883)),
            username=self._config.optional("mqtt.username"),
            password=self._config.optional("mqtt.password"),
        )
        self._discovery_prefix = config.optional("mqtt.discovery_prefix", "homeassistant")
        self._topic = config.optional("mqtt.topic", config.optional("mqtt.node_id", "mqtt_mesh"))

        # initialize bridges
        for name, constructor in BRIDGES.items():
            self._bridges[name] = constructor(self)
        self._skylight_programs = SkylightProgramsMqttBridge(self)

    @property
    def client(self):
        return self._client

    @property
    def config(self):
        return self._config

    @property
    def diagnostic_monitor(self):
        return self._diagnostic_monitor

    @property
    def topic(self):
        return self._topic

    @property
    def discovery_prefix(self):
        return self._discovery_prefix

    def shutdown(self):
        self._skylight_programs.shutdown()

    def skylight_programs_enabled(self):
        return bool(self._config.optional("legacy_skylight_programs_enabled", False))

    def node_topic(self, component, node):
        """
        Return base topic for a specific node
        """
        if isinstance(node, Node):
            node = node.config.require("id")

        return f"{self._discovery_prefix}/{component}/{self._topic}/{node}"

    def filtered_messages(self, component, node, topic="#"):
        """
        Shorthand to get messages for a specific node
        """
        return self._client.filtered_messages(f"{self.node_topic(component, node)}/{topic}")

    def command_topic(self, component, node):
        return f"{self.node_topic(component, node)}/set"

    async def subscribe(self, component, node, topic):
        await self._client.subscribe(f"{self.node_topic(component, node)}/{topic}")

    async def publish(self, component, node, topic, message, **kwargs):
        """
        Send a state update for a specific nde
        """
        if isinstance(message, dict):
            message = json.dumps(message)

        await self._client.publish(f"{self.node_topic(component, node)}/{topic}", str(message).encode(), **kwargs)

    async def run(self, app):
        async with AsyncExitStack() as stack:
            tasks = await stack.enter_async_context(Tasks())

            # connect to MQTT broker
            await stack.enter_async_context(self._client)

            # spawn tasks for every node
            for node in self._nodes.all():
                bridge = self._bridges.get(node.type)

                if bridge is None:
                    logging.warning(f"No MQTT bridge for node {node} ({node.type})")
                    return

                tasks.spawn(bridge.listen(node), f"bridge {node}")

            if self.skylight_programs_enabled():
                tasks.spawn(self._skylight_programs.listen(app), "bridge skylight programs")
            else:
                await self._skylight_programs.clear_discovery()
                logging.info("Pesetech skylight programs MQTT discovery cleared because the legacy programs UI is disabled")

            # wait for all tasks
            await tasks.gather()
