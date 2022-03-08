"""Support for Netatmo/Bubendorff covers."""
from __future__ import annotations

import logging
from typing import Any

from .pyatmo import modules as NaModules
from .pyatmo.modules.device_types import DeviceCategory as NetatmoDeviceCategory

from homeassistant.components.cover import (  # ATTR_TILT_POSITION,; SUPPORT_CLOSE_TILT,; SUPPORT_OPEN_TILT,; SUPPORT_SET_POSITION,; SUPPORT_SET_TILT_POSITION,; SUPPORT_STOP_TILT,
    ATTR_POSITION,
    SUPPORT_CLOSE,
    SUPPORT_OPEN,
    SUPPORT_STOP,
    CoverDeviceClass,
    CoverEntity,
)
from homeassistant.config_entries import ConfigEntry

# from homeassistant.const import CONF_OPTIMISTIC, STATE_CLOSED, STATE_OPEN
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (  # ATTR_HEATING_POWER_REQUEST,; ATTR_SCHEDULE_NAME,; ATTR_SELECTED_SCHEDULE,; CONF_URL_ENERGY,; DATA_HOMES,; DATA_SCHEDULES,; EVENT_TYPE_CANCEL_SET_POINT,; EVENT_TYPE_SCHEDULE,; EVENT_TYPE_SET_POINT,; EVENT_TYPE_THERM_MODE,; NETATMO_CREATE_BATTERY,; SERVICE_SET_SCHEDULE,
    CONF_URL_CONTROL,
    DATA_HANDLER,
    DOMAIN,
)
from .data_handler import HOME, SIGNAL_NAME, NetatmoDataHandler
from .netatmo_entity_base import NetatmoBase

# from homeassistant.helpers.restore_state import RestoreEntity


_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Netatmo cover platform."""
    data_handler = hass.data[DOMAIN][entry.entry_id][DATA_HANDLER]

    account_topology = data_handler.account

    if not account_topology or account_topology.raw_data == {}:
        raise PlatformNotReady

    entities = []
    for home_id in account_topology.homes:
        signal_name = f"{HOME}-{home_id}"

        await data_handler.subscribe(HOME, signal_name, None, home_id=home_id)

        for module in account_topology.homes[home_id].modules.values():
            if module.device_category is NetatmoDeviceCategory.shutter:
                entities.append(NetatmoCover(data_handler, module))

    _LOGGER.debug("Adding covers %s", entities)
    async_add_entities(entities, True)


class NetatmoCover(NetatmoBase, CoverEntity):
    """Representation of a Netatmo cover device."""

    def __init__(self, data_handler: NetatmoDataHandler, module: NaModules.NBR) -> None:
        """Initialize the Netatmo device."""
        CoverEntity.__init__(self)
        super().__init__(data_handler)
        # self.categories = set(self.device.categories)
        self.optimistic = True

        self._cover = module

        self._id = module.entity_id
        self._attr_name = self._device_name = module.name
        self._model = module.device_type
        self._config_url = CONF_URL_CONTROL

        self._home_id = module.home.entity_id
        self._closed: bool | None = None
        # self._is_opening: bool | None = None
        # self._is_closing: bool | None = None
        self._attr_is_closed = None

        self._signal_name = f"{HOME}-{self._home_id}"
        self._publishers.extend(
            [
                {
                    "name": HOME,
                    "home_id": self._home_id,
                    SIGNAL_NAME: self._signal_name,
                },
            ]
        )
        self._attr_unique_id = f"{module.entity_id}-{self._model}"

    @property
    def supported_features(self) -> int:
        """Flag supported features."""
        supported_features = 0
        # if self.has_capability("open"):
        supported_features |= SUPPORT_OPEN
        # if self.has_capability("close"):
        supported_features |= SUPPORT_CLOSE
        # if self.has_capability("stop"):
        supported_features |= SUPPORT_STOP
        # if self.has_capability("position"):
        #     supported_features |= SUPPORT_SET_POSITION
        # if self.has_capability("rotation"):
        #     supported_features |= (
        #         SUPPORT_OPEN_TILT
        #         | SUPPORT_CLOSE_TILT
        #         | SUPPORT_STOP_TILT
        #         | SUPPORT_SET_TILT_POSITION
        #     )

        return supported_features

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        self._attr_is_closing = True
        self.async_write_ha_state()
        try:
            await self._cover.async_close()
            if self.optimistic:
                self._attr_is_closed = True
        finally:
            self._attr_is_closing = None
            self.async_write_ha_state()

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        self._attr_is_opening = True
        self.async_write_ha_state()
        try:
            await self._cover.async_open()
            if self.optimistic:
                self._attr_is_closed = False
        finally:
            self._attr_is_opening = None
            self.async_write_ha_state()

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        await self._cover.async_stop()

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover shutter to a specific position."""
        await self._cover.async_set_target_position(100 - kwargs[ATTR_POSITION])

    @property
    def device_class(self) -> str:
        """Return the device class."""
        return CoverDeviceClass.SHUTTER

    # @property
    # def current_cover_position(self):
    #     """Return the current position of cover shutter."""
    #     if not self.has_state("position"):
    #         return None
    #     return 100 - self._cover.get_position()

    # @property
    # def is_opening(self) -> bool | None:
    #     """Return if the cover is opening."""
    #     # if not self.optimistic:
    #     #     return None
    #     return self._is_opening

    # @property
    # def is_closing(self) -> bool | None:
    #     """Return if the cover is closing."""
    #     # if not self.optimistic:
    #     #     return None
    #     return self._is_closing

    # @property
    # def is_closed(self) -> bool | None:
    #     """Return if the cover is closed."""
    #     if self._cover.current_position:
    #         return self._cover.current_position > 0
    #     return None
    # is_closed = None
    # if self.has_state("position"):
    #     is_closed = self._cover.is_closed()
    # elif self.optimistic:
    #     is_closed = self._closed
    # return is_closed

    # @property
    # def current_cover_tilt_position(self) -> int | None:
    #     """Return current position of cover tilt.

    #     None is unknown, 0 is closed, 100 is fully open.
    #     """
    #     # if not self.has_state("orientation"):
    #     #     return None
    #     return 100 - self._cover.orientation

    # def set_cover_tilt_position(self, **kwargs):
    #     """Move the cover tilt to a specific position."""
    #     self._cover.orientation = 100 - kwargs[ATTR_TILT_POSITION]

    # def open_cover_tilt(self, **kwargs):
    #     """Open the cover tilt."""
    #     self._cover.orientation = 0

    # def close_cover_tilt(self, **kwargs):
    #     """Close the cover tilt."""
    #     self._cover.orientation = 100

    # def stop_cover_tilt(self, **kwargs):
    #     """Stop the cover."""
    #     self._cover.stop()

    async def async_added_to_hass(self) -> None:
        """Complete the initialization."""
        await super().async_added_to_hass()
        # if not self.optimistic:
        #     return
        # Restore the last state if we use optimistic
        # last_state = await self.async_get_last_state()

        # if last_state is not None and last_state.state in (
        #     STATE_OPEN,
        #     STATE_CLOSED,
        # ):
        #     self._closed = last_state.state == STATE_CLOSED

    @callback
    def async_update_callback(self) -> None:
        """Update the entity's state."""
        self._attr_is_closed = self._cover.current_position == 0
        self._attr_current_cover_position = self._cover.current_position
