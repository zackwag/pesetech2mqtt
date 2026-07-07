import json
import logging
import asyncio
import inspect


class HassMqttBridge:
    """
    Base class for all MQTT messenger bridges

    Instances of this class are responsible for bridging between a Home Assistant
    device type and a specific type of Bluetooth Mesh node.

    This class provides default implementations, that should work for most common nodes.
    They can however be overriden, if more sophisticated behaviour is required.
    """

    def __init__(self, messenger):
        self._messenger = messenger

    @property
    def component(self):
        return None

    def _property_change(self, node, property, value):
        try:
            # get handler from property name
            handler = getattr(self, f"_notify_{property}")
        except:
            logging.warning(f"Missing handler for property {property}")
            return

        # TODO: track task
        asyncio.create_task(handler(node, value))

    async def listen(self, node):
        """
        Listen for incoming messages and node changes
        """

        # send node configuration for MQTT discovery
        await node.ready.wait()
        await self.config(node)

        # listen for node changes (this will also push the initial state)
        node.subscribe(self._property_change, resend=True)

        # listen for incoming MQTT messages
        async with self._messenger.filtered_messages(self.component, node, "set") as messages:
            await self._messenger.subscribe(self.component, node, "set")
            logging.info(f"Subscribed MQTT command topic for {node}")

            async for message in messages:
                logging.info(f"Received message on {message.topic}:\n{message.payload}")

                # get command from topic and load message
                command = message.topic.split("/")[-1]
                try:
                    payload = json.loads(message.payload.decode())
                except (UnicodeDecodeError, json.JSONDecodeError):
                    logging.exception(f"Invalid MQTT JSON payload on {message.topic}")
                    continue

                try:
                    # get handler from command name
                    handler = getattr(self, f"_mqtt_{command}")
                except:
                    logging.warning(f"Missing handler for command {command}")
                    continue

                try:
                    kwargs = {}
                    try:
                        if "topic" in inspect.signature(handler).parameters:
                            kwargs["topic"] = message.topic
                    except (TypeError, ValueError):
                        pass
                    await handler(node, payload, **kwargs)
                except:
                    logging.exception(f"Failed to handle MQTT command {command}")

    async def config(self, node):
        """
        Send discovery message
        """
        pass
