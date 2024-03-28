"""Base class for Netatmo entities."""
from __future__ import annotations

from typing import Any

try:
    from .pyatmo import DeviceType
    from .pyatmo.modules.device_types import (
        DEVICE_DESCRIPTION_MAP,
        DeviceType as NetatmoDeviceType,
    )
except:
    from pyatmo import DeviceType
    from pyatmo.modules.device_types import (
        DEVICE_DESCRIPTION_MAP,
        DeviceType as NetatmoDeviceType,
    )

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DATA_DEVICE_IDS, DEFAULT_ATTRIBUTION, DOMAIN, SIGNAL_NAME
from .data_handler import PUBLIC, NetatmoDataHandler


class NetatmoBaseEntity(Entity):
    """Netatmo entity base class."""

    _attr_attribution = DEFAULT_ATTRIBUTION

    def __init__(self, data_handler: NetatmoDataHandler) -> None:
        """Set up Netatmo entity base."""
        self.data_handler = data_handler
        self._publishers: list[dict[str, Any]] = []

        self._device_name: str = ""
        self._id: str = ""
        self._model: DeviceType
        self._config_url: str | None = None
        self._attr_name = None
        self._attr_unique_id = None
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        """Entity created."""
        for publisher in self._publishers:
            signal_name = publisher[SIGNAL_NAME]

            if "target_module" in publisher:
                    await self.data_handler.subscribe_with_target(
                    publisher=publisher["name"],
                    signal_name=signal_name,
                    target=publisher["target_module"],
                    update_callback=self.async_update_callback,
                    update_only=True
                )
            elif "home_id" in publisher:
                await self.data_handler.subscribe(
                    publisher=publisher["name"],
                    signal_name=signal_name,
                    update_callback=self.async_update_callback,
                    home_id=publisher["home_id"],
                )
            elif "data_handler" in publisher:
                await self.data_handler.subscribe_with_target(
                    publisher=publisher["name"],
                    signal_name=signal_name,
                    target=publisher["data_handler"],
                    update_callback=self.async_update_callback,
                    update_only=True
                )
            elif publisher["name"] == PUBLIC:
                await self.data_handler.subscribe(
                    publisher["name"],
                    signal_name,
                    self.async_update_callback,
                    lat_ne=publisher["lat_ne"],
                    lon_ne=publisher["lon_ne"],
                    lat_sw=publisher["lat_sw"],
                    lon_sw=publisher["lon_sw"],
                )

            else:
                await self.data_handler.subscribe(
                    publisher["name"], signal_name, self.async_update_callback
                )

            if any(
                sub is None
                for sub in self.data_handler.publisher[signal_name].subscriptions
            ):
                await self.data_handler.unsubscribe(signal_name, None)

        registry = dr.async_get(self.hass)
        if device := registry.async_get_device(identifiers={(DOMAIN, self._id)}):
            self.hass.data[DOMAIN][DATA_DEVICE_IDS][self._id] = device.id

        self.async_update_callback()

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed from hass."""
        await super().async_will_remove_from_hass()

        for publisher in self._publishers:
            await self.data_handler.unsubscribe(
                publisher[SIGNAL_NAME], self.async_update_callback
            )

    @callback
    def async_update_callback(self) -> None:
        """Update the entity's state."""
        raise NotImplementedError

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info for the sensor."""
        if "." in self._model:
            netatmo_device = NetatmoDeviceType(self._model.partition(".")[2])
        else:
            netatmo_device = getattr(NetatmoDeviceType, self._model)
        manufacturer, model = DEVICE_DESCRIPTION_MAP[netatmo_device]
        return DeviceInfo(
            configuration_url=self._config_url,
            identifiers={(DOMAIN, self._id)},
            name=self._device_name,
            manufacturer=manufacturer,
            model=model,
        )
