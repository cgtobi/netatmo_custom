"""Support for the Netatmo climate schedule selector."""
from __future__ import annotations

import logging

from . import pyatmo
from .pyatmo.modules.device_types import DeviceCategory as NetatmoDeviceCategory

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_URL_ENERGY,
    DATA_HANDLER,
    DATA_SCHEDULES,
    DOMAIN,
    EVENT_TYPE_SCHEDULE,
    MANUFACTURER,
)
from .data_handler import HOME, SIGNAL_NAME, NetatmoDataHandler
from .netatmo_entity_base import NetatmoBase

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Netatmo energy platform schedule selector."""
    data_handler = hass.data[DOMAIN][entry.entry_id][DATA_HANDLER]

    account_topology = data_handler.account

    if not account_topology or account_topology.raw_data == {}:
        raise PlatformNotReady

    entities = []
    for home in account_topology.homes.values():
        if NetatmoDeviceCategory.climate not in [
            next(iter(x)) for x in [room.features for room in home.rooms.values()] if x
        ]:
            continue

        signal_name = f"{HOME}-{home.entity_id}"

        await data_handler.subscribe(HOME, signal_name, None, home_id=home.entity_id)

        if (climate_state := data_handler.account) is None:
            continue

        hass.data[DOMAIN][DATA_SCHEDULES][home.entity_id] = climate_state.homes[
            home.entity_id
        ].schedules

        entities.append(
            NetatmoScheduleSelect(
                data_handler,
                home,
                [schedule.name for schedule in home.schedules.values()],
            )
        )

    _LOGGER.debug("Adding climate schedule select entities %s", entities)
    async_add_entities(entities, True)


class NetatmoScheduleSelect(NetatmoBase, SelectEntity):
    """Representation a Netatmo thermostat schedule selector."""

    def __init__(
        self, data_handler: NetatmoDataHandler, home: pyatmo.Home, options: list
    ) -> None:
        """Initialize the select entity."""
        SelectEntity.__init__(self)
        super().__init__(data_handler)

        self._home = home
        self._home_id = home.entity_id

        self._signal_name = f"{HOME}-{self._home_id}"
        self._publishers.extend(
            [
                {
                    "name": HOME,
                    "home_id": self._home.entity_id,
                    SIGNAL_NAME: self._signal_name,
                },
            ]
        )

        self._device_name = self._home.name
        self._attr_name = f"{MANUFACTURER} {self._device_name}"

        self._model: str = "NATherm1"
        self._netatmo_type = CONF_URL_ENERGY

        self._attr_unique_id = f"{self._home_id}-schedule-select"

        self._attr_current_option = getattr(self._home.get_selected_schedule(), "name")
        self._attr_options = options

    async def async_added_to_hass(self) -> None:
        """Entity created."""
        await super().async_added_to_hass()

        for event_type in (EVENT_TYPE_SCHEDULE,):
            self.data_handler.config_entry.async_on_unload(
                async_dispatcher_connect(
                    self.hass,
                    f"signal-{DOMAIN}-webhook-{event_type}",
                    self.handle_event,
                )
            )

    @callback
    def handle_event(self, event: dict) -> None:
        """Handle webhook events."""
        data = event["data"]

        if self._home_id != data["home_id"]:
            return

        if data["event_type"] == EVENT_TYPE_SCHEDULE and "schedule_id" in data:
            self._attr_current_option = getattr(
                self.hass.data[DOMAIN][DATA_SCHEDULES][self._home_id].get(
                    data["schedule_id"]
                ),
                "name",
            )
            self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        for sid, schedule in self.hass.data[DOMAIN][DATA_SCHEDULES][
            self._home_id
        ].items():
            if schedule.name != option:
                continue
            _LOGGER.debug(
                "Setting %s schedule to %s (%s)",
                self._home_id,
                option,
                sid,
            )
            await self._home.async_switch_schedule(schedule_id=sid)
            break

    @callback
    def async_update_callback(self) -> None:
        """Update the entity's state."""
        self._attr_current_option = getattr(self._home.get_selected_schedule(), "name")
        self.hass.data[DOMAIN][DATA_SCHEDULES][self._home_id] = self._home.schedules
        self._attr_options = [
            schedule.name
            for schedule in self.hass.data[DOMAIN][DATA_SCHEDULES][
                self._home_id
            ].values()
        ]
