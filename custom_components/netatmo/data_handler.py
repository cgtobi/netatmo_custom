"""The Netatmo data handler."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import islice
import logging
from time import time
from typing import Any
import asyncio
import aiohttp
try:
    from . import pyatmo
    from .pyatmo.modules.device_types import (
        DeviceCategory as NetatmoDeviceCategory,
        DeviceType as NetatmoDeviceType,
    )
except:
    import pyatmo
    from pyatmo.modules.device_types import (
        DeviceCategory as NetatmoDeviceCategory,
        DeviceType as NetatmoDeviceType,
    )

from homeassistant.components import cloud
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    AUTH,
    DATA_PERSONS,
    DATA_SCHEDULES,
    DOMAIN,
    MANUFACTURER,
    NETATMO_CREATE_BATTERY,
    NETATMO_CREATE_CAMERA,
    NETATMO_CREATE_CAMERA_LIGHT,
    NETATMO_CREATE_CLIMATE,
    NETATMO_CREATE_COVER,
    NETATMO_CREATE_FAN,
    NETATMO_CREATE_LIGHT,
    NETATMO_CREATE_ROOM_SENSOR,
    NETATMO_CREATE_SELECT,
    NETATMO_CREATE_SENSOR,
    NETATMO_CREATE_ENERGY,
    NETATMO_CREATE_SWITCH,
    NETATMO_CREATE_WEATHER_SENSOR,
    PLATFORMS,
    WEBHOOK_ACTIVATION,
    WEBHOOK_DEACTIVATION,
    WEBHOOK_NACAMERA_CONNECTION,
    WEBHOOK_PUSH_TYPE, CONF_HOMES,
)

_LOGGER = logging.getLogger(__name__)

SIGNAL_NAME = "signal_name"
ACCOUNT = "account"
HOME = "home"
WEATHER = "weather"
AIR_CARE = "air_care"
PUBLIC = NetatmoDeviceType.public
EVENT = "event"
ENERGY_MEASURE = "energy"

PUBLISHERS = {
    ACCOUNT: "async_update_topology",
    HOME: "async_update_status",
    WEATHER: "async_update_weather_stations",
    AIR_CARE: "async_update_air_care",
    PUBLIC: "async_update_public_weather",
    EVENT: "async_update_events",
    ENERGY_MEASURE: "async_update_energy"
}


#Netatmo rate limiting: https://dev.netatmo.com/guideline

# Application limits
#
# If you have less than 100 users:
#
# 200 requests every 10 seconds
# 2000 requests every hour
#
# If you have more than 100 users:
#
# (2 * number of users) requests every 10 seconds
# (20 * number of users) requests every hour


# Per user limits
#
# 50 requests every 10 seconds
# 500 requests every hour


BATCH_SIZE = 3
DEV_FACTOR = 7
DEV_LIMIT = 400
CLOUD_FACTOR = 2
CLOUD_LIMIT = 150

CALL_PER_HOUR = "CALL_PER_HOUR"
RATE_LIMIT_FACTOR = "RATE_LIMIT_FACTOR"
CALL_PER_TEN_SECONDS = "CALL_PER_10S"

NETATMO_USER_CALL_LIMITS = {
    CALL_PER_HOUR : 150,
    RATE_LIMIT_FACTOR : 1,
    CALL_PER_TEN_SECONDS : 2
}
NETATMO_DEV_CALL_LIMITS = {
    CALL_PER_HOUR : 400,
    RATE_LIMIT_FACTOR : 3,
    CALL_PER_TEN_SECONDS : 20
}


DEFAULT_INTERVALS = {
    ACCOUNT: 5400,
    HOME: 150,
    WEATHER: 300,
    AIR_CARE: 150,
    PUBLIC: 300,
    EVENT: 300,
    ENERGY_MEASURE: 5400
}
SCAN_INTERVAL = 60


@dataclass
class NetatmoDevice:
    """Netatmo device class."""

    data_handler: NetatmoDataHandler
    device: pyatmo.modules.Module
    parent_id: str
    signal_name: str


@dataclass
class NetatmoHome:
    """Netatmo home class."""

    data_handler: NetatmoDataHandler
    home: pyatmo.Home
    parent_id: str
    signal_name: str


@dataclass
class NetatmoRoom:
    """Netatmo room class."""

    data_handler: NetatmoDataHandler
    room: pyatmo.Room
    parent_id: str
    signal_name: str


MAX_EMISSIONS = 10

import random

@dataclass
class NetatmoPublisher:
    """Class for keeping track of Netatmo data class metadata."""

    name: str
    interval: int
    next_scan: float
    target: Any
    subscriptions: set[CALLBACK_TYPE | None]
    method: str
    kwargs: dict
    _emissions : list
    _rand_delta: int

    def __init__(self, name, interval, next_scan, target, subscriptions, method, kwargs):
        self.name = name
        self.interval = interval
        self.next_scan = next_scan
        self.target = target
        self.subscriptions = subscriptions
        self.method = method
        self.kwargs = kwargs
        self._emissions = []
        self._rand_delta = interval//4
    def push_emission(self, ts):

        if len(self._emissions) >= MAX_EMISSIONS:
            self._emissions.pop(0)
        self._emissions.append(ts)

    def set_next_randomized_scan(self, ts):
        self.next_scan = ts + self.interval + random.randint(0-self._rand_delta, self._rand_delta)

    def is_ts_allows_emission(self, ts):
        return self.next_scan < ts + max(SCAN_INTERVAL//2, self.interval//8)

class NetatmoDataHandler:
    """Manages the Netatmo data handling."""

    account: pyatmo.AsyncAccount | None
    _interval_factor: int

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize self."""
        self.hass = hass
        self.account = None
        self.config_entry = config_entry
        self._auth = hass.data[DOMAIN][config_entry.entry_id][AUTH]
        self.publisher: dict[str, NetatmoPublisher] = {}
        self._sorted_publisher : list[NetatmoPublisher] = []

        self._queue: deque = deque()


        self._webhook: bool = False
        if config_entry.data["auth_implementation"] == cloud.DOMAIN:
            limits = NETATMO_USER_CALL_LIMITS
        else:
            limits = NETATMO_DEV_CALL_LIMITS

        self._interval_factor = limits[RATE_LIMIT_FACTOR]
        self._hourly_rate_limit = limits[CALL_PER_HOUR]
        self._10s_rate_limit = limits[CALL_PER_TEN_SECONDS]

        self._min_call_per_interval = min((self._hourly_rate_limit*SCAN_INTERVAL)//3600, (SCAN_INTERVAL//10) * self._10s_rate_limit)
        self._max_call_per_interval = max((self._hourly_rate_limit*SCAN_INTERVAL)//3600, (SCAN_INTERVAL//10) * self._10s_rate_limit)

        self.rolling_hour = []


    def add_api_call(self, n):

        for i in range(n):
            self.rolling_hour.append(time())

        while self.rolling_hour[-1] - self.rolling_hour[0] > 3600:
            self.rolling_hour.pop(0)

    def get_current_call_per_hour(self):
        return len(self.rolling_hour)


    async def async_setup(self) -> None:
        """Set up the Netatmo data handler."""
        self.config_entry.async_on_unload(
            async_track_time_interval(
                self.hass, self.async_update, timedelta(seconds=SCAN_INTERVAL)
            )
        )

        self.config_entry.async_on_unload(
            async_dispatcher_connect(
                self.hass,
                f"signal-{DOMAIN}-webhook-None",
                self.handle_event,
            )
        )



        homes = self.config_entry.options.get(CONF_HOMES, [])

        self.account = pyatmo.AsyncAccount(self._auth, support_only_homes=homes)

        await self.account.async_update_topology()

        #adding this here to have the modules with their correct features, etc
        await self.account.async_update_status(home_id=None)

        #do update only as async_update_topology will call the APIS
        await self.subscribe_with_target(
            publisher=ACCOUNT,
            signal_name=ACCOUNT,
            target=None,
            update_callback=None,
            update_only=True
        )

        await self.hass.config_entries.async_forward_entry_setups(
            self.config_entry, PLATFORMS
        )
        await self.async_dispatch()


    def get_publisher_candidates(self, current, n):
        self._sorted_publisher = sorted(self._sorted_publisher,  key=lambda x: x.next_scan)
        #get the ones with the "older" not handled publisher

        candidates = []
        for p in self._sorted_publisher:
            if p.name is not None:
                if p.is_ts_allows_emission(current):
                   candidates.append(p)
                   if len(candidates) >= n:
                        break

        return candidates


    async def async_update(self, event_time: datetime) -> None:
        """Update device. """

        num_call = self._min_call_per_interval

        if self.get_current_call_per_hour() < self._hourly_rate_limit - num_call:
            num_call = min(self._min_call_per_interval, self._hourly_rate_limit - self.get_current_call_per_hour())

        delta_sleep = SCAN_INTERVAL // (3*num_call)

        current = time()

        candidates = self.get_publisher_candidates(current, num_call)

        has_error = False
        for data_class in candidates:

            if delta_sleep > 0:
                await asyncio.sleep(delta_sleep)

            if publisher := data_class.name:
                error = await self.async_fetch_data(publisher)

                if error:
                    has_error = True
                    _LOGGER.debug("Error on publisher: %s", publisher)
                    #this may be due to a rate limit!
                    for p in self._sorted_publisher:
                        p.next_scan += SCAN_INTERVAL*5
                    break
                else:
                    self.publisher[publisher].push_emission(current)
                    self.publisher[publisher].set_next_randomized_scan(current)


        cph = self.get_current_call_per_hour()
        _LOGGER.debug("Calls per hour: %i , num call asked: %i num call candidates: %i num pub: %i", cph, num_call, len(candidates), len(self._sorted_publisher))
        if cph > self._hourly_rate_limit and has_error is False:
            _LOGGER.debug("Calls per hour hit rate limit: %i/%i", cph, self._hourly_rate_limit)
            for publisher in self.publisher.values():
                publisher.next_scan += 60

    @callback
    def async_force_update(self, signal_name: str) -> None:
        """Prioritize data retrieval for given data class entry."""
        self.publisher[signal_name].next_scan = 0

    async def handle_event(self, event: dict) -> None:
        """Handle webhook events."""
        if event["data"][WEBHOOK_PUSH_TYPE] == WEBHOOK_ACTIVATION:
            _LOGGER.info("%s webhook successfully registered", MANUFACTURER)
            self._webhook = True

        elif event["data"][WEBHOOK_PUSH_TYPE] == WEBHOOK_DEACTIVATION:
            _LOGGER.info("%s webhook unregistered", MANUFACTURER)
            self._webhook = False

        elif event["data"][WEBHOOK_PUSH_TYPE] == WEBHOOK_NACAMERA_CONNECTION:
            _LOGGER.debug("%s camera reconnected", MANUFACTURER)
            self.async_force_update(ACCOUNT)

    async def async_fetch_data(self, signal_name: str, update_only=False) -> bool:
        """Fetch data and notify."""
        has_error = False

        if update_only is False:

            num_fetch = None
            try:
                num_fetch = await getattr(self.publisher[signal_name].target, self.publisher[signal_name].method)(
                    **self.publisher[signal_name].kwargs
                )
            except (pyatmo.NoDevice, pyatmo.ApiError) as err:
                _LOGGER.debug(err)
                has_error = True

            except (TimeoutError, aiohttp.ClientConnectorError) as err:
                _LOGGER.debug(err)
                return True

            try:
                num_fetch = int(num_fetch)
            except:
                num_fetch = 1

            self.add_api_call(num_fetch)

        for update_callback in self.publisher[signal_name].subscriptions:
            if update_callback:
                update_callback()

        return has_error

    async def subscribe(
        self,
        publisher: str,
        signal_name: str,
        update_callback: CALLBACK_TYPE | None,
        **kwargs: Any,
    ) -> None:
        await self.subscribe_with_target(publisher=publisher, signal_name=signal_name, target=None, update_callback=update_callback, update_only = False, **kwargs)

    async def subscribe_with_target(
        self,
        publisher: str,
        signal_name: str,
        target: Any,
        update_callback: CALLBACK_TYPE | None,
        update_only = False,
        **kwargs: Any
    ) -> None:
        """Subscribe to publisher."""
        if signal_name in self.publisher:
            if update_callback not in self.publisher[signal_name].subscriptions:
                self.publisher[signal_name].subscriptions.add(update_callback)
            return

        if target is None:
            target = self.account

        if publisher == "public":
            kwargs = {"area_id": self.account.register_public_weather_area(**kwargs)}


        interval = int(DEFAULT_INTERVALS[publisher] / self._interval_factor)
        self.publisher[signal_name] = NetatmoPublisher(
            name=signal_name,
            interval=interval,
            next_scan=time() + interval,
            target=target,
            subscriptions={update_callback},
            method=PUBLISHERS[publisher],
            kwargs=kwargs,
        )



        #do that only if it is on account, is get measure or other ... don't do too much here has it will kill the number of calls
        try:
            await self.async_fetch_data(signal_name, update_only=update_only)
        except KeyError:
            #in case we have a bad formed response from the API
            self.publisher.pop(signal_name)
            _LOGGER.debug("Publisher %s removed at subscription due to mal formed response!!!!!!", signal_name)
            raise

        self._sorted_publisher.append(self.publisher[signal_name])
        num_cph = 0.0
        for p in self._sorted_publisher:
            num_cph += p.interval/3600.0

        _LOGGER.debug("Publisher %s added current total cph %f / rate limit %i", signal_name, num_cph, self._hourly_rate_limit)

    async def unsubscribe(
        self, signal_name: str, update_callback: CALLBACK_TYPE | None
    ) -> None:
        """Unsubscribe from publisher."""
        if update_callback not in self.publisher[signal_name].subscriptions:
            return

        self.publisher[signal_name].subscriptions.remove(update_callback)

        if not self.publisher[signal_name].subscriptions:
            self._sorted_publisher = [p for p in self._sorted_publisher if p.name != signal_name]
            self.publisher.pop(signal_name)
            _LOGGER.debug("Publisher %s removed", signal_name)

    @property
    def webhook(self) -> bool:
        """Return the webhook state."""
        return self._webhook

    async def async_dispatch(self) -> None:
        """Dispatch the creation of entities."""
        await self.subscribe(WEATHER, WEATHER, None)
        await self.subscribe(AIR_CARE, AIR_CARE, None)

        self.setup_air_care()

        for home in self.account.homes.values():
            signal_home = f"{HOME}-{home.entity_id}"

            await self.subscribe(HOME, signal_home, None, home_id=home.entity_id)
            await self.subscribe(EVENT, signal_home, None, home_id=home.entity_id)

            self.setup_climate_schedule_select(home, signal_home)
            self.setup_rooms(home, signal_home)
            self.setup_modules(home, signal_home)

            self.hass.data[DOMAIN][DATA_PERSONS][home.entity_id] = {
                person.entity_id: person.pseudo for person in home.persons.values()
            }

        await self.unsubscribe(WEATHER, None)
        await self.unsubscribe(AIR_CARE, None)

    def setup_air_care(self) -> None:
        """Set up home coach/air care modules."""
        for module in self.account.modules.values():
            if module.device_category is NetatmoDeviceCategory.air_care:
                async_dispatcher_send(
                    self.hass,
                    NETATMO_CREATE_WEATHER_SENSOR,
                    NetatmoDevice(
                        self,
                        module,
                        AIR_CARE,
                        AIR_CARE,
                    ),
                )

    def setup_modules(self, home: pyatmo.Home, signal_home: str) -> None:
        """Set up modules."""
        netatmo_type_signal_map = {
            NetatmoDeviceCategory.camera: [
                NETATMO_CREATE_CAMERA,
                NETATMO_CREATE_CAMERA_LIGHT,
            ],
            NetatmoDeviceCategory.dimmer: [NETATMO_CREATE_LIGHT, NETATMO_CREATE_SENSOR,  NETATMO_CREATE_ENERGY],
            NetatmoDeviceCategory.shutter: [NETATMO_CREATE_COVER, NETATMO_CREATE_SENSOR,  NETATMO_CREATE_ENERGY],
            NetatmoDeviceCategory.switch: [
                NETATMO_CREATE_LIGHT,
                NETATMO_CREATE_SWITCH,
                NETATMO_CREATE_SENSOR,
                NETATMO_CREATE_ENERGY,
            ],
            NetatmoDeviceCategory.meter: [NETATMO_CREATE_SENSOR, NETATMO_CREATE_ENERGY],
            NetatmoDeviceCategory.fan: [NETATMO_CREATE_FAN, NETATMO_CREATE_SENSOR, NETATMO_CREATE_ENERGY],
        }
        for module in home.modules.values():
            if not module.device_category:
                continue

            for signal in netatmo_type_signal_map.get(module.device_category, []):
                async_dispatcher_send(
                    self.hass,
                    signal,
                    NetatmoDevice(
                        self,
                        module,
                        home.entity_id,
                        signal_home,
                    ),
                )
            if module.device_category is NetatmoDeviceCategory.weather:
                async_dispatcher_send(
                    self.hass,
                    NETATMO_CREATE_WEATHER_SENSOR,
                    NetatmoDevice(
                        self,
                        module,
                        home.entity_id,
                        WEATHER,
                    ),
                )

    def setup_rooms(self, home: pyatmo.Home, signal_home: str) -> None:
        """Set up rooms."""
        for room in home.rooms.values():
            if NetatmoDeviceCategory.climate in room.features:
                async_dispatcher_send(
                    self.hass,
                    NETATMO_CREATE_CLIMATE,
                    NetatmoRoom(
                        self,
                        room,
                        home.entity_id,
                        signal_home,
                    ),
                )

                for module in room.modules.values():
                    if module.device_category is NetatmoDeviceCategory.climate:
                        async_dispatcher_send(
                            self.hass,
                            NETATMO_CREATE_BATTERY,
                            NetatmoDevice(
                                self,
                                module,
                                room.entity_id,
                                signal_home,
                            ),
                        )

                if "humidity" in room.features:
                    async_dispatcher_send(
                        self.hass,
                        NETATMO_CREATE_ROOM_SENSOR,
                        NetatmoRoom(
                            self,
                            room,
                            room.entity_id,
                            signal_home,
                        ),
                    )

    def setup_climate_schedule_select(
        self, home: pyatmo.Home, signal_home: str
    ) -> None:
        """Set up climate schedule per home."""
        if NetatmoDeviceCategory.climate in [
            next(iter(x)) for x in [room.features for room in home.rooms.values()] if x
        ]:
            self.hass.data[DOMAIN][DATA_SCHEDULES][home.entity_id] = self.account.homes[
                home.entity_id
            ].schedules

            async_dispatcher_send(
                self.hass,
                NETATMO_CREATE_SELECT,
                NetatmoHome(
                    self,
                    home,
                    home.entity_id,
                    signal_home,
                ),
            )
