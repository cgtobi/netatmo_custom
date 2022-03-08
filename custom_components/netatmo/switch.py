"""Support for Netatmo/BTicino/Legrande switches."""
from __future__ import annotations

import logging
from typing import Any

from .pyatmo import modules as NaModules
from .pyatmo.modules.device_types import DeviceCategory as NetatmoDeviceCategory

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
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

# from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType


_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Netatmo switch platform."""
    data_handler = hass.data[DOMAIN][entry.entry_id][DATA_HANDLER]

    account_topology = data_handler.account

    if not account_topology or account_topology.raw_data == {}:
        raise PlatformNotReady

    entities = []
    for home_id in account_topology.homes:
        signal_name = f"{HOME}-{home_id}"

        await data_handler.subscribe(HOME, signal_name, None, home_id=home_id)

        for module in account_topology.homes[home_id].modules.values():
            if module.device_category is NetatmoDeviceCategory.plug:
                entities.append(NetatmoSwitch(data_handler, module))

    _LOGGER.debug("Adding covers %s", entities)
    async_add_entities(entities, True)


class NetatmoSwitch(NetatmoBase, SwitchEntity):
    """Representation of a Netatmo switch device."""

    def __init__(
        self, data_handler: NetatmoDataHandler, module: NaModules.Plug
    ) -> None:
        """Initialize the Netatmo device."""
        SwitchEntity.__init__(self)
        super().__init__(data_handler)

        self._switch = module

        self._id = module.entity_id
        self._attr_name = self._device_name = module.name
        self._model = module.device_type
        self._config_url = CONF_URL_CONTROL

        self._home_id = module.home.entity_id

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

    async def async_added_to_hass(self) -> None:
        """Entity created."""
        await super().async_added_to_hass()

    @property
    def is_on(self) -> bool | None:
        """Return the state of the sensor."""
        return self._switch.on

    @callback
    def async_update_callback(self) -> None:
        """Update the entity's state."""
        self._attr_is_on = self._switch.on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the zone on."""
        await self._switch.async_on()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the zone off."""
        await self._switch.async_off()
