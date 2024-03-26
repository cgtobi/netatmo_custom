"""Support for the Netatmo sensors."""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
import logging
from typing import cast
from datetime import datetime
from datetime import timedelta
from time import time

try:
    from .pyatmo.const import MeasureInterval
except:
    from pyatmo.const import MeasureInterval

try:
    from . import pyatmo
except:
    import pyatmo

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONCENTRATION_PARTS_PER_MILLION,
    DEGREE,
    PERCENTAGE,
    EntityCategory,
    UnitOfPower,
    UnitOfEnergy,
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfSoundPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import async_entries_for_config_entry
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_URL_ENERGY,
    CONF_URL_PUBLIC_WEATHER,
    CONF_URL_WEATHER,
    CONF_WEATHER_AREAS,
    DATA_HANDLER,
    DOMAIN,
    NETATMO_CREATE_BATTERY,
    NETATMO_CREATE_ENERGY,
    NETATMO_CREATE_ROOM_SENSOR,
    NETATMO_CREATE_SENSOR,
    NETATMO_CREATE_WEATHER_SENSOR,
    SIGNAL_NAME,
)
from .data_handler import HOME, PUBLIC, NetatmoDataHandler, NetatmoDevice, NetatmoRoom, ENERGY_MEASURE
from .entity import NetatmoBaseEntity
from .helper import NetatmoArea

_LOGGER = logging.getLogger(__name__)

SUPPORTED_PUBLIC_SENSOR_TYPES: tuple[str, ...] = (
    "temperature",
    "pressure",
    "humidity",
    "rain",
    "wind_strength",
    "gust_strength",
    "sum_rain_1",
    "sum_rain_24",
    "wind_angle",
    "gust_angle",
)


@dataclass(frozen=True)
class NetatmoRequiredKeysMixin:
    """Mixin for required keys."""

    netatmo_name: str


@dataclass(frozen=True)
class NetatmoSensorEntityDescription(SensorEntityDescription, NetatmoRequiredKeysMixin):
    """Describes Netatmo sensor entity."""


SENSOR_TYPES: tuple[NetatmoSensorEntityDescription, ...] = (
    NetatmoSensorEntityDescription(
        key="temperature",
        name="Temperature",
        netatmo_name="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.TEMPERATURE,
    ),
    NetatmoSensorEntityDescription(
        key="temp_trend",
        name="Temperature trend",
        netatmo_name="temp_trend",
        entity_registry_enabled_default=False,
        icon="mdi:trending-up",
    ),
    NetatmoSensorEntityDescription(
        key="co2",
        name="CO2",
        netatmo_name="co2",
        native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.CO2,
    ),
    NetatmoSensorEntityDescription(
        key="pressure",
        name="Pressure",
        netatmo_name="pressure",
        native_unit_of_measurement=UnitOfPressure.MBAR,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.ATMOSPHERIC_PRESSURE,
    ),
    NetatmoSensorEntityDescription(
        key="pressure_trend",
        name="Pressure trend",
        netatmo_name="pressure_trend",
        entity_registry_enabled_default=False,
        icon="mdi:trending-up",
    ),
    NetatmoSensorEntityDescription(
        key="noise",
        name="Noise",
        netatmo_name="noise",
        native_unit_of_measurement=UnitOfSoundPressure.DECIBEL,
        device_class=SensorDeviceClass.SOUND_PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    NetatmoSensorEntityDescription(
        key="humidity",
        name="Humidity",
        netatmo_name="humidity",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.HUMIDITY,
    ),
    NetatmoSensorEntityDescription(
        key="rain",
        name="Rain",
        netatmo_name="rain",
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    NetatmoSensorEntityDescription(
        key="sum_rain_1",
        name="Rain last hour",
        netatmo_name="sum_rain_1",
        entity_registry_enabled_default=False,
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=SensorStateClass.TOTAL,
    ),
    NetatmoSensorEntityDescription(
        key="sum_rain_24",
        name="Rain today",
        netatmo_name="sum_rain_24",
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        device_class=SensorDeviceClass.PRECIPITATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    NetatmoSensorEntityDescription(
        key="battery_percent",
        name="Battery Percent",
        netatmo_name="battery",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.BATTERY,
    ),
    NetatmoSensorEntityDescription(
        key="windangle",
        name="Direction",
        netatmo_name="wind_direction",
        icon="mdi:compass-outline",
    ),
    NetatmoSensorEntityDescription(
        key="windangle_value",
        name="Angle",
        netatmo_name="wind_angle",
        entity_registry_enabled_default=False,
        native_unit_of_measurement=DEGREE,
        icon="mdi:compass-outline",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    NetatmoSensorEntityDescription(
        key="windstrength",
        name="Wind Strength",
        netatmo_name="wind_strength",
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        device_class=SensorDeviceClass.WIND_SPEED,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    NetatmoSensorEntityDescription(
        key="gustangle",
        name="Gust Direction",
        netatmo_name="gust_direction",
        entity_registry_enabled_default=False,
        icon="mdi:compass-outline",
    ),
    NetatmoSensorEntityDescription(
        key="gustangle_value",
        name="Gust Angle",
        netatmo_name="gust_angle",
        entity_registry_enabled_default=False,
        native_unit_of_measurement=DEGREE,
        icon="mdi:compass-outline",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    NetatmoSensorEntityDescription(
        key="guststrength",
        name="Gust Strength",
        netatmo_name="gust_strength",
        entity_registry_enabled_default=False,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        device_class=SensorDeviceClass.WIND_SPEED,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    NetatmoSensorEntityDescription(
        key="reachable",
        name="Reachability",
        netatmo_name="reachable",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:signal",
    ),
    NetatmoSensorEntityDescription(
        key="rf_status",
        name="Radio",
        netatmo_name="rf_strength",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:signal",
    ),
    NetatmoSensorEntityDescription(
        key="wifi_status",
        name="Wifi",
        netatmo_name="wifi_strength",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:wifi",
    ),
    NetatmoSensorEntityDescription(
        key="health_idx",
        name="Health",
        netatmo_name="health_idx",
        icon="mdi:cloud",
    ),
    NetatmoSensorEntityDescription(
        key="power",
        name="Power",
        netatmo_name="power",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
    ),
)
SENSOR_TYPES_KEYS = [desc.key for desc in SENSOR_TYPES]

BATTERY_SENSOR_DESCRIPTION = NetatmoSensorEntityDescription(
    key="battery",
    name="Battery Percent",
    netatmo_name="battery",
    entity_category=EntityCategory.DIAGNOSTIC,
    native_unit_of_measurement=PERCENTAGE,
    state_class=SensorStateClass.MEASUREMENT,
    device_class=SensorDeviceClass.BATTERY,
)

ENERGY_SENSOR_DESCRIPTION = NetatmoSensorEntityDescription(
    key="sum_energy_elec",
    name="Energy Sum",
    netatmo_name="sum_energy_elec",
    native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
    state_class=SensorStateClass.TOTAL_INCREASING,
    device_class=SensorDeviceClass.ENERGY,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Netatmo sensor platform."""

    @callback
    def _create_battery_entity(netatmo_device: NetatmoDevice) -> None:
        if not hasattr(netatmo_device.device, "battery"):
            return
        entity = NetatmoClimateBatterySensor(netatmo_device)
        async_add_entities([entity])

    entry.async_on_unload(
        async_dispatcher_connect(hass, NETATMO_CREATE_BATTERY, _create_battery_entity)
    )
    
    @callback
    def _create_energy_entity(netatmo_device: NetatmoDevice) -> None:

        if "sum_energy_elec" in netatmo_device.device.features or hasattr(netatmo_device.device, "sum_energy_elec"):
            _LOGGER.debug(
                "Adding %s energy sum sensor %s",
                netatmo_device.device.device_category,
                netatmo_device.device.name,
            )
            entity = NetatmoEnergySensor(netatmo_device)
            async_add_entities([entity])

    entry.async_on_unload(
        async_dispatcher_connect(hass, NETATMO_CREATE_ENERGY, _create_energy_entity)
    )

    @callback
    def _create_weather_sensor_entity(netatmo_device: NetatmoDevice) -> None:
        async_add_entities(
            NetatmoWeatherSensor(netatmo_device, description)
            for description in SENSOR_TYPES
            if description.netatmo_name in netatmo_device.device.features
        )

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, NETATMO_CREATE_WEATHER_SENSOR, _create_weather_sensor_entity
        )
    )

    @callback
    def _create_sensor_entity(netatmo_device: NetatmoDevice) -> None:
        _LOGGER.debug(
            "Adding %s sensor %s with features: %s",
            netatmo_device.device.device_category,
            netatmo_device.device.name,
            netatmo_device.device.features
        )
        async_add_entities(
            [
                NetatmoSensor(netatmo_device, description)
                for description in SENSOR_TYPES
                if description.key in netatmo_device.device.features
            ]
        )

    entry.async_on_unload(
        async_dispatcher_connect(hass, NETATMO_CREATE_SENSOR, _create_sensor_entity)
    )

    @callback
    def _create_room_sensor_entity(netatmo_device: NetatmoRoom) -> None:
        if not netatmo_device.room.climate_type:
            msg = f"No climate type found for this room: {netatmo_device.room.name}"
            _LOGGER.debug(msg)
            return
        async_add_entities(
            NetatmoRoomSensor(netatmo_device, description)
            for description in SENSOR_TYPES
            if description.key in netatmo_device.room.features
        )

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, NETATMO_CREATE_ROOM_SENSOR, _create_room_sensor_entity
        )
    )

    device_registry = dr.async_get(hass)
    data_handler = hass.data[DOMAIN][entry.entry_id][DATA_HANDLER]

    async def add_public_entities(update: bool = True) -> None:
        """Retrieve Netatmo public weather entities."""
        entities = {
            device.name: device.id
            for device in async_entries_for_config_entry(
                device_registry, entry.entry_id
            )
            if device.model == "Public Weather station"
        }

        new_entities = []
        for area in [
            NetatmoArea(**i) for i in entry.options.get(CONF_WEATHER_AREAS, {}).values()
        ]:
            signal_name = f"{PUBLIC}-{area.uuid}"

            if area.area_name in entities:
                entities.pop(area.area_name)

                if update:
                    async_dispatcher_send(
                        hass,
                        f"netatmo-config-{area.area_name}",
                        area,
                    )
                    continue

            await data_handler.subscribe(
                PUBLIC,
                signal_name,
                None,
                lat_ne=area.lat_ne,
                lon_ne=area.lon_ne,
                lat_sw=area.lat_sw,
                lon_sw=area.lon_sw,
                area_id=str(area.uuid),
            )

            new_entities.extend(
                [
                    NetatmoPublicSensor(data_handler, area, description)
                    for description in SENSOR_TYPES
                    if description.netatmo_name in SUPPORTED_PUBLIC_SENSOR_TYPES
                ]
            )

        for device_id in entities.values():
            device_registry.async_remove_device(device_id)

        async_add_entities(new_entities)

    async_dispatcher_connect(
        hass, f"signal-{DOMAIN}-public-update-{entry.entry_id}", add_public_entities
    )

    await add_public_entities(False)


class NetatmoWeatherSensor(NetatmoBaseEntity, SensorEntity):
    """Implementation of a Netatmo weather/home coach sensor."""

    _attr_has_entity_name = True
    entity_description: NetatmoSensorEntityDescription

    def __init__(
        self,
        netatmo_device: NetatmoDevice,
        description: NetatmoSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(netatmo_device.data_handler)
        self.entity_description = description

        self._module = netatmo_device.device
        self._id = self._module.entity_id
        self._station_id = (
            self._module.bridge if self._module.bridge is not None else self._id
        )
        self._device_name = self._module.name
        category = getattr(self._module.device_category, "name")
        self._publishers.extend(
            [
                {
                    "name": category,
                    SIGNAL_NAME: category,
                },
            ]
        )

        self._attr_name = f"{description.name}"
        self._model = self._module.device_type
        self._config_url = CONF_URL_WEATHER
        self._attr_unique_id = f"{self._id}-{description.key}"

        if hasattr(self._module, "place"):
            place = cast(
                pyatmo.modules.base_class.Place, getattr(self._module, "place")
            )
            if hasattr(place, "location") and place.location is not None:
                self._attr_extra_state_attributes.update(
                    {
                        ATTR_LATITUDE: place.location.latitude,
                        ATTR_LONGITUDE: place.location.longitude,
                    }
                )

    @callback
    def async_update_callback(self) -> None:
        """Update the entity's state."""
        if (
            not self._module.reachable
            or (state := getattr(self._module, self.entity_description.netatmo_name))
            is None
        ):
            if self.available:
                self._attr_available = False
            return

        if self.entity_description.netatmo_name in {
            "temperature",
            "pressure",
            "sum_rain_1",
        }:
            self._attr_native_value = round(state, 1)
        elif self.entity_description.netatmo_name == "rf_strength":
            self._attr_native_value = process_rf(state)
        elif self.entity_description.netatmo_name == "wifi_strength":
            self._attr_native_value = process_wifi(state)
        elif self.entity_description.netatmo_name == "health_idx":
            self._attr_native_value = process_health(state)
        else:
            self._attr_native_value = state

        self._attr_available = True
        self.async_write_ha_state()


class NetatmoClimateBatterySensor(NetatmoBaseEntity, SensorEntity):
    """Implementation of a Netatmo sensor."""

    entity_description: NetatmoSensorEntityDescription

    def __init__(
        self,
        netatmo_device: NetatmoDevice,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(netatmo_device.data_handler)
        self.entity_description = BATTERY_SENSOR_DESCRIPTION

        self._module = cast(pyatmo.modules.NRV, netatmo_device.device)
        self._id = netatmo_device.parent_id

        self._publishers.extend(
            [
                {
                    "name": HOME,
                    "home_id": netatmo_device.device.home.entity_id,
                    SIGNAL_NAME: netatmo_device.signal_name,
                },
            ]
        )

        self._attr_name = f"{self._module.name} {self.entity_description.name}"
        self._room_id = self._module.room_id
        self._model = getattr(self._module.device_type, "value")
        self._config_url = CONF_URL_ENERGY

        self._attr_unique_id = (
            f"{self._id}-{self._module.entity_id}-{self.entity_description.key}"
        )

    @callback
    def async_update_callback(self) -> None:
        """Update the entity's state."""
        if not self._module.reachable:
            if self.available:
                self._attr_available = False
            return

        self._attr_available = True
        self._attr_native_value = self._module.battery


class NetatmoBaseSensor(NetatmoBaseEntity, SensorEntity):
    """Implementation of a Netatmo sensor."""

    entity_description: NetatmoSensorEntityDescription

    def __init__(
        self,
        netatmo_device: NetatmoDevice,
        description: NetatmoSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(netatmo_device.data_handler)
        self.entity_description = description

        self._module = netatmo_device.device
        self._id = self._module.entity_id

        self._attr_name = f"{self._module.name} {self.entity_description.name}"
        self._room_id = self._module.room_id
        self._model = getattr(self._module.device_type, "value")
        self._config_url = CONF_URL_ENERGY

        self._attr_unique_id = (
            f"{self._id}-{self._module.entity_id}-{self.entity_description.key}"
        )

        self.complement_publishers(netatmo_device)

    @abstractmethod
    def complement_publishers(self, netatmo_device):
        """abstract method to fill publishers"""

    @callback
    def async_update_callback(self) -> None:
        """Update the entity's state."""

        if self.entity_description.key != "reachable":

            if not self._module.reachable:
                if self.available:
                    self._attr_available = False
                return

            if (state := getattr(self._module, self.entity_description.key)) is None:
                return
        else:
            state = self._module.reachable
            if state is None:
                state = False

        self._attr_available = True
        self._attr_native_value = state


        self.async_write_ha_state()

class NetatmoSensor(NetatmoBaseSensor):
    """Implementation of a generic Netatmo sensor."""

    def complement_publishers(self, netatmo_device):
        self._publishers.extend(
            [
                {
                    "name": HOME,
                    "home_id": netatmo_device.device.home.entity_id,
                    SIGNAL_NAME: netatmo_device.signal_name,
                },
            ]
        )

class NetatmoEnergySensor(NetatmoBaseSensor):
    """Implementation of an energy Netatmo sensor."""

    _last_end: datetime | None
    _last_start: datetime | None
    _current_start_anchor: datetime | None
    _last_val_sent: float | None = None

    def __init__(
        self,
        netatmo_device: NetatmoDevice
    ) -> None:
        """Initialize the sensor."""
        super().__init__(netatmo_device, ENERGY_SENSOR_DESCRIPTION)
        self._current_start_anchor = None
        self._next_need_reset = False
        self._last_val_sent = None
    
    def complement_publishers(self, netatmo_device):
        self._publishers.extend(
            [
                {
                    "name": ENERGY_MEASURE,
                    "target_module": self,
                    SIGNAL_NAME: self._attr_unique_id,
                },
            ]
        )

    def update_measures_num_calls(self):
        if self._next_need_reset is True:
            return 0

        return self._module.update_measures_num_calls()
    #to be called on the object itself

    #doing this allows to have a clen reboot of the system without loosing anything
    def _compute_current_anchor_point(self, current):

        #The monday stuff below are not working anymore : now energy for 30mn or 1h can be only probed for 2.5 days ...hence reset every days
        return datetime(current.year, current.month, current.day)

    def _OLD_compute_current_anchor_point(self, current):
        #first monday of the month and third monday of the month
        month_beg = datetime(current.year, current.month, 1) # + timedelta(hours=1)

        #weekday() : from 0 (monday) to 6(sunday)
        beg_day_in_week =  month_beg.weekday()
        first_monday_day = 1
        if beg_day_in_week > 0:
            first_monday_day = 1 + (7 - month_beg.weekday())

        if current.day < first_monday_day:
            #we have to take the previous month
            current  = current - timedelta(days=current.days + 1)
            return self._compute_current_anchor_point(current)


        second_monday_day = first_monday_day + 14

        if current.day < second_monday_day:
            return datetime(year=current.year, month=current.month, day=first_monday_day)
        else:
            return datetime(year=current.year, month=current.month, day=second_monday_day)

    def _need_reset(self, current):
        prev_anchor = self._current_start_anchor
        curr_anchor = self._compute_current_anchor_point(current)
        if prev_anchor is not None and curr_anchor != prev_anchor:
            _LOGGER.debug("=====> Need Reset is TRUE!!!!!: prev %s cur %s",
                          prev_anchor, curr_anchor)
            return True, prev_anchor, curr_anchor
        return False, prev_anchor, curr_anchor


    async def async_update_energy(self, **kwargs):

        if self._next_need_reset:
            #value reset to 0 for a cycle vs what was asked before, next time we come here we will go in the next else
            self._module.reset_measures()
            self._next_need_reset = False
            # leave self._last_end so it is a "point" update, next time the measure will be done at the former last_end
            _LOGGER.debug("=====> RESET ENERGY: forcing reset of %s new anchor: %s", self.name,
                          self._current_start_anchor)
            return 0
        else:
            end = datetime.now()
            need_reset, prev_anchor, self._current_start_anchor = self._need_reset(end)

            if prev_anchor is not None and need_reset:
                start = prev_anchor
                end = self._current_start_anchor
                self._next_need_reset = True
                _LOGGER.debug("=====> TRUNCATE RESET ENERGY FOR RESET: truncate %s from anchor: %s to %s", self.name, prev_anchor, self._current_start_anchor)
            else:
                start = self._current_start_anchor #at start use the "boot" time of the integration as a first measure
                self._next_need_reset = False

            end_time = int(end.timestamp())
            start_time = int(start.timestamp())

            num_calls =  await self._module.async_update_measures(start_time=start_time, end_time=end_time, interval= MeasureInterval.HALF_HOUR)
            # let the subsequent callback update the state energy data  and the availability


            return num_calls

    @callback
    def async_update_callback(self) -> None:
        """Update the entity's state."""

        if (state := getattr(self._module, self.entity_description.key, None)) is None:
            return

        if self._module.in_reset is False:

            delta_energy = 0
            current = time()

            if self._module.last_computed_end is not None and self._module.last_computed_end < current:

                if self._next_need_reset is True and self._current_start_anchor is not None:
                    to_ts = int(self._current_start_anchor.timestamp())
                else:
                    to_ts = current

                power_data = self._module.get_history_data("power", from_ts=self._module.last_computed_end, to_ts=to_ts)

                if len(power_data) > 1:

                    #compute a rieman sum, as best as possible , trapezoidal, taking pessimistic asumption as we don't want to artifically go up the previous one (except in rare exceptions like reset, 0 , etc)

                    for i in range(len(power_data) - 1):

                        dt_h = float(power_data[i+1][0] - power_data[i][0])/3600.0

                        dP_W = abs(float(power_data[i+1][1] - power_data[i][1]))

                        dNrj_Wh = dt_h*( min(power_data[i+1][1], power_data[i][1]) + 0.5*dP_W)

                        delta_energy += dNrj_Wh


            current_val = state
            new_val = state + delta_energy
            prev_energy = self._last_val_sent
            if prev_energy is not None and prev_energy > new_val:
                new_val = prev_energy
            state = new_val

            if delta_energy > 0:
                _LOGGER.debug("<<<< DELTA ENERGY ON: %s delta: %s nrjAPI %s nrj+delta %s prev %s RETAINED: %s",
                              self.name, delta_energy, current_val, current_val + delta_energy, prev_energy, state)

        self._attr_available = True
        self._attr_native_value = state
        self._last_val_sent = state
        self.async_write_ha_state()


def process_health(health: int) -> str:
    """Process health index and return string for display."""
    if health == 0:
        return "Healthy"
    if health == 1:
        return "Fine"
    if health == 2:
        return "Fair"
    if health == 3:
        return "Poor"
    return "Unhealthy"


def process_rf(strength: int) -> str:
    """Process wifi signal strength and return string for display."""
    if strength >= 90:
        return "Low"
    if strength >= 76:
        return "Medium"
    if strength >= 60:
        return "High"
    return "Full"


def process_wifi(strength: int) -> str:
    """Process wifi signal strength and return string for display."""
    if strength >= 86:
        return "Low"
    if strength >= 71:
        return "Medium"
    if strength >= 56:
        return "High"
    return "Full"


class NetatmoRoomSensor(NetatmoBaseEntity, SensorEntity):
    """Implementation of a Netatmo room sensor."""

    entity_description: NetatmoSensorEntityDescription

    def __init__(
        self,
        netatmo_room: NetatmoRoom,
        description: NetatmoSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(netatmo_room.data_handler)
        self.entity_description = description

        self._room = netatmo_room.room
        self._id = self._room.entity_id

        self._publishers.extend(
            [
                {
                    "name": HOME,
                    "home_id": netatmo_room.room.home.entity_id,
                    SIGNAL_NAME: netatmo_room.signal_name,
                },
            ]
        )

        self._attr_name = f"{self._room.name} {self.entity_description.name}"
        self._room_id = self._room.entity_id
        self._config_url = CONF_URL_ENERGY

        assert self._room.climate_type
        self._model = self._room.climate_type

        self._attr_unique_id = (
            f"{self._id}-{self._room.entity_id}-{self.entity_description.key}"
        )

    @callback
    def async_update_callback(self) -> None:
        """Update the entity's state."""
        if (state := getattr(self._room, self.entity_description.key)) is None:
            return

        self._attr_native_value = state

        self.async_write_ha_state()


class NetatmoPublicSensor(NetatmoBaseEntity, SensorEntity):
    """Represent a single sensor in a Netatmo."""

    _attr_has_entity_name = True
    entity_description: NetatmoSensorEntityDescription

    def __init__(
        self,
        data_handler: NetatmoDataHandler,
        area: NetatmoArea,
        description: NetatmoSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(data_handler)
        self.entity_description = description

        self._signal_name = f"{PUBLIC}-{area.uuid}"
        self._publishers.append(
            {
                "name": PUBLIC,
                "lat_ne": area.lat_ne,
                "lon_ne": area.lon_ne,
                "lat_sw": area.lat_sw,
                "lon_sw": area.lon_sw,
                "area_name": area.area_name,
                SIGNAL_NAME: self._signal_name,
            }
        )

        self._station = data_handler.account.public_weather_areas[str(area.uuid)]

        self.area = area
        self._mode = area.mode
        self._area_name = area.area_name
        self._id = self._area_name
        self._device_name = f"{self._area_name}"
        self._attr_name = f"{description.name}"
        self._show_on_map = area.show_on_map
        self._config_url = CONF_URL_PUBLIC_WEATHER
        self._attr_unique_id = (
            f"{self._device_name.replace(' ', '-')}-{description.key}"
        )
        self._model = PUBLIC

        self._attr_extra_state_attributes.update(
            {
                ATTR_LATITUDE: (self.area.lat_ne + self.area.lat_sw) / 2,
                ATTR_LONGITUDE: (self.area.lon_ne + self.area.lon_sw) / 2,
            }
        )

    async def async_added_to_hass(self) -> None:
        """Entity created."""
        await super().async_added_to_hass()

        assert self.device_info and "name" in self.device_info
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"netatmo-config-{self.device_info['name']}",
                self.async_config_update_callback,
            )
        )

    async def async_config_update_callback(self, area: NetatmoArea) -> None:
        """Update the entity's config."""
        if self.area == area:
            return

        await self.data_handler.unsubscribe(
            self._signal_name, self.async_update_callback
        )

        self.area = area
        self._signal_name = f"{PUBLIC}-{area.uuid}"
        self._mode = area.mode
        self._show_on_map = area.show_on_map
        await self.data_handler.subscribe(
            PUBLIC,
            self._signal_name,
            self.async_update_callback,
            lat_ne=area.lat_ne,
            lon_ne=area.lon_ne,
            lat_sw=area.lat_sw,
            lon_sw=area.lon_sw,
        )

    @callback
    def async_update_callback(self) -> None:
        """Update the entity's state."""
        data = None

        if self.entity_description.netatmo_name == "temperature":
            data = self._station.get_latest_temperatures()
        elif self.entity_description.netatmo_name == "pressure":
            data = self._station.get_latest_pressures()
        elif self.entity_description.netatmo_name == "humidity":
            data = self._station.get_latest_humidities()
        elif self.entity_description.netatmo_name == "rain":
            data = self._station.get_latest_rain()
        elif self.entity_description.netatmo_name == "sum_rain_1":
            data = self._station.get_60_min_rain()
        elif self.entity_description.netatmo_name == "sum_rain_24":
            data = self._station.get_24_h_rain()
        elif self.entity_description.netatmo_name == "wind_strength":
            data = self._station.get_latest_wind_strengths()
        elif self.entity_description.netatmo_name == "gust_strength":
            data = self._station.get_latest_gust_strengths()
        elif self.entity_description.netatmo_name == "wind_angle":
            data = self._station.get_latest_wind_angles()
        elif self.entity_description.netatmo_name == "gust_angle":
            data = self._station.get_latest_gust_angles()

        if not data:
            if self.available:
                _LOGGER.error(
                    "No station provides %s data in the area %s",
                    self.entity_description.key,
                    self._area_name,
                )

            self._attr_available = False
            return

        if values := [x for x in data.values() if x is not None]:
            if self._mode == "avg":
                self._attr_native_value = round(sum(values) / len(values), 1)
            elif self._mode == "max":
                self._attr_native_value = max(values)

        self._attr_available = self.state is not None
        self.async_write_ha_state()
