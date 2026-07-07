import asyncio
import logging

from mesh import Node
from mesh.composition import Composition, Element

from bluetooth_mesh import models


class Generic(Node):
    """
    Generic Bluetooth Mesh node

    Provides additional functionality compared to the very basic Node class,
    like composition model helpers and node configuration.
    """

    OnlineProperty = "online"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # stores the node's composition data
        self._composition = None
        # lists all bound model
        self._bound_models = set()
        # maps bound models to their element addresses
        self._model_addresses = {}
        self._using_imported_models = False

    def using_imported_models(self):
        return self._using_imported_models

    def _imported_model_address(self, model):
        imported = self.imported_models if isinstance(self.imported_models, dict) else {}
        for model_id in self._candidate_model_ids(model):
            if model_id not in imported:
                continue
            try:
                address = imported[model_id]
                if isinstance(address, str):
                    return int(address[2:], 16) if address.lower().startswith("0x") else int(address, 0)
                return int(address)
            except (TypeError, ValueError):
                logging.warning(f"Ignoring invalid imported model address for {self}: {model_id}={imported[model_id]!r}")
                return None
        return None

    def _candidate_model_ids(self, model):
        name = getattr(model, "__name__", "")
        known = {
            "GenericOnOffServer": ["1000"],
            "LightLightnessServer": ["1300"],
            "LightCTLServer": ["1303"],
            "LightCTLTemperatureServer": ["1306"],
        }
        if name in known:
            return known[name]

        result = []

        def add(value):
            if value is None:
                return
            if isinstance(value, int):
                result.append(f"{value:04X}" if value <= 0xFFFF else f"{value:08X}")
            elif isinstance(value, str):
                text = value.strip()
                if text:
                    result.append(text.replace("0x", "").replace("0X", "").upper())
            elif isinstance(value, (tuple, list)):
                if len(value) == 2 and isinstance(value[1], int):
                    add(value[1])
                else:
                    for item in value:
                        add(item)

        add(getattr(model, "MODEL_ID", None))
        return result

    def _is_model_bound(self, model):
        """
        Check if the given model is supported and bound
        """
        return model in self._bound_models

    def _model_address(self, model):
        """
        Return the element address for a bound model.
        """
        return self._model_addresses[model]

    async def fetch_composition(self):
        """
        Fetch the composition data

        This data contains information about the node's capabilities.
        Use the helper functions to retrieve information.
        """
        client = self._app.elements[0][models.ConfigClient]
        data = await client.get_composition_data([self.unicast], net_index=self._app.primary_net_key[0], timeout=30)
        # TODO: multi page composition data support
        page_zero = data.get(self.unicast, {}).get("zero")
        self._composition = Composition(page_zero)

    async def bind(self, app):
        await super().bind(app)

        if self.imported_models:
            self._using_imported_models = True
            logging.info(f"Using imported model bindings for {self}")
            return

        # update the composition data
        await self.fetch_composition()

        logging.debug(f"Node composition:\n{self._composition}")

    async def bind_model(self, model):
        """
        Bind the given model to the application key

        If the node supports the given model, it is bound to the appliaction key
        and listed within the supported models.

        If the node does not support the given model, the request is skipped.
        """

        imported_address = self._imported_model_address(model)
        if imported_address is not None:
            self._bound_models.add(model)
            self._model_addresses[model] = imported_address
            logging.info(f"{self} using imported {model} binding at {imported_address:04}")
            return True

        if self._composition is None:
            logging.info(f"No composition data for {self}")
            return False

        element_index = self._composition.element_index(model)
        if element_index is None:
            logging.info(f"{self} does not support {model}")
            return False

        element_address = self.unicast + element_index

        # configure model
        client = self._app.elements[0][models.ConfigClient]
        try:
            await client.bind_app_key(
                self.unicast,
                net_index=self._app.primary_net_key[0],
                element_address=element_address,
                app_key_index=self._app.app_keys[0][0],
                model=model,
            )
        except Exception:
            logging.exception(f"{self} failed to bind {model} at element {element_index} ({element_address:04})")
            return False

        self._bound_models.add(model)
        self._model_addresses[model] = element_address

        logging.info(f"{self} bound {model} at element {element_index} ({element_address:04})")
        return True
