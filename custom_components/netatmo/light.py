"""Support for the Netatmo camera lights."""
from __future__ import annotations

import logging
from typing import Any

from . import pyatmo
from .pyatmo.modules.device_types import NetatmoDeviceType

from homeassistant.components.light import LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DATA_HANDLER,
    DOMAIN,
    EVENT_TYPE_LIGHT_MODE,
    MANUFACTURER,
    TYPE_SECURITY,
    WEBHOOK_LIGHT_MODE,
    WEBHOOK_PUSH_TYPE,
)
from .data_handler import HOME, NetatmoDataHandler
from .netatmo_entity_base import NetatmoBase

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Netatmo camera light platform."""
    data_handler = hass.data[DOMAIN][entry.entry_id][DATA_HANDLER]

    camera_topology = data_handler.account

    if not camera_topology or not camera_topology.raw_data:
        raise PlatformNotReady

    entities = []
    for home_id in camera_topology.homes:
        signal_name = f"{HOME}-{home_id}"

        await data_handler.subscribe(HOME, signal_name, None, home_id=home_id)

        if (camera_state := data_handler.account) is None:
            continue

        for camera in camera_state.homes[home_id].modules.values():
            if camera.device_type is NetatmoDeviceType.NOC:
                entities.append(NetatmoLight(data_handler, camera))

    _LOGGER.debug("Adding camera lights %s", entities)
    async_add_entities(entities, True)


class NetatmoLight(NetatmoBase, LightEntity):
    """Representation of a Netatmo Presence camera light."""

    def __init__(
        self,
        data_handler: NetatmoDataHandler,
        camera: pyatmo.modules.NOC,
    ) -> None:
        """Initialize a Netatmo Presence camera light."""
        LightEntity.__init__(self)
        super().__init__(data_handler)

        self._camera = camera
        self._id = self._camera.entity_id
        self._home_id = self._camera.home.entity_id
        self._device_name = self._camera.name
        self._attr_name = f"{MANUFACTURER} {self._device_name}"
        self._model = self._camera.device_type
        self._netatmo_type = TYPE_SECURITY
        self._is_on = False
        self._attr_unique_id = f"{self._id}-light"

    async def async_added_to_hass(self) -> None:
        """Entity created."""
        await super().async_added_to_hass()

        self.data_handler.config_entry.async_on_unload(
            async_dispatcher_connect(
                self.hass,
                f"signal-{DOMAIN}-webhook-{EVENT_TYPE_LIGHT_MODE}",
                self.handle_event,
            )
        )

    @callback
    def handle_event(self, event: dict) -> None:
        """Handle webhook events."""
        data = event["data"]

        if not data.get("camera_id"):
            return

        if (
            data["home_id"] == self._home_id
            and data["camera_id"] == self._id
            and data[WEBHOOK_PUSH_TYPE] == WEBHOOK_LIGHT_MODE
        ):
            self._is_on = bool(data["sub_type"] == "on")

            self.async_write_ha_state()
            return

    # @property
    # def _data(self) -> pyatmo.AsyncCameraData:
    #     """Return data for this entity."""
    #     return cast(
    #         pyatmo.AsyncCameraData,
    #         self.data_handler.data[self._data_classes[0]["name"]],
    #     )

    @property
    def available(self) -> bool:
        """If the webhook is not established, mark as unavailable."""
        return bool(self.data_handler.webhook)

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        return self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn camera floodlight on."""
        _LOGGER.debug("Turn camera '%s' on", self.name)
        await self._camera.async_floodlight_on()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn camera floodlight into auto mode."""
        _LOGGER.debug("Turn camera '%s' to auto mode", self.name)
        await self._camera.async_floodlight_on()

    @callback
    def async_update_callback(self) -> None:
        """Update the entity's state."""
        self._is_on = bool(self._camera.floodlight == "on")
