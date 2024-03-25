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


PUBLISHERS_CALL_PROBER = {
    ENERGY_MEASURE: "update_measures_num_calls"
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


CALL_PER_HOUR = "CALL_PER_HOUR"
RATE_LIMIT_FACTOR = "RATE_LIMIT_FACTOR"
CALL_PER_TEN_SECONDS = "CALL_PER_10S"

NETATMO_USER_CALL_LIMITS = {
    CALL_PER_HOUR : 200,
    RATE_LIMIT_FACTOR : 1,
    CALL_PER_TEN_SECONDS : 2 #2 to comply with the global limit of (2 * number of users) requests every 10 seconds
}
NETATMO_DEV_CALL_LIMITS = {
    CALL_PER_HOUR : 400,
    RATE_LIMIT_FACTOR : 3,
    CALL_PER_TEN_SECONDS : 20
}


DEFAULT_INTERVALS = {
    ACCOUNT: 10800,
    HOME: 300, #from netatmo discussion it seems home data is updated every 5mn
    WEATHER: 600,
    AIR_CARE: 300,
    PUBLIC: 600,
    EVENT: 600,
    ENERGY_MEASURE: 2700
}
SCAN_INTERVAL = 60

CPH_ADJUSTEMENT_DOWN = 0.8
CPH_ADJUSTEMENT_BACK_UP = 1.1

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
    num_consecutive_errors : int

    def __init__(self, name, interval, next_scan, target, subscriptions, method, method_num_call_probe, kwargs):
        self.name = name
        self.interval = interval
        self.next_scan = next_scan
        self.target = target
        self.subscriptions = subscriptions
        self.method = method
        self.method_num_call_probe = method_num_call_probe
        self.kwargs = kwargs
        self._emissions = []
        self.num_consecutive_errors = 0

    def push_emission(self, ts):
        self.num_consecutive_errors = 0
        if len(self._emissions) >= MAX_EMISSIONS:
            self._emissions.pop(0)
        self._emissions.append(ts)

    def set_next_randomized_scan(self, ts, wait_time=0):
        rand_delta = int(self.interval // 8)
        rnd = random.randint(0-rand_delta, rand_delta)
        self.next_scan = ts + max(wait_time + abs(rnd), self.interval + rnd)

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



        self._webhook: bool = False
        if config_entry.data["auth_implementation"] == cloud.DOMAIN:
            limits = NETATMO_USER_CALL_LIMITS
        else:
            limits = NETATMO_DEV_CALL_LIMITS

        self._interval_factor = limits[RATE_LIMIT_FACTOR]
        self._initial_hourly_rate_limit = limits[CALL_PER_HOUR]

        self._10s_rate_limit = limits[CALL_PER_TEN_SECONDS]

        self.rolling_hour = []
        self._adjusted_hourly_rate_limit = None
        self._last_cph_change = None



    def add_api_call(self, n):

        current = time()
        for i in range(n):
            self.rolling_hour.append(current)

        while len(self.rolling_hour) > 0 and  current - self.rolling_hour[0] > 3600:
            self.rolling_hour.pop(0)

    def get_current_call_per_hour(self):
        return int(len(self.rolling_hour))


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

        num_calls = 0

        for method in ["async_update_topology", "async_update_status"]:
            for i in range(3):
                has_error = False
                try:
                    num_calls += await getattr(self.account, method)()
                except (pyatmo.NoDevice, pyatmo.ApiError) as err:
                    _LOGGER.debug("init account.%s error NoDevice or ApiError %s", method, err)
                    has_error = True
                except (TimeoutError, aiohttp.ClientConnectorError) as err:
                    _LOGGER.debug("init account.%s error Timeout or ClientConnectorError: %s", method, err)
                    has_error = True
                except Exception as err:
                    _LOGGER.debug("init account.%s error unknown %s", method, err)
                    has_error = True

                if has_error is False:
                    break

                await asyncio.sleep(20)

        self.add_api_call(num_calls)

        #adding this here to have the modules with their correct features, etc

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

    def compute_theoretical_call_per_hour(self):
        num_cph = 0.0
        for p in self._sorted_publisher:

            added_call = 1
            if p.target and p.method_num_call_probe is not None:
                added_call = getattr(p.target, p.method_num_call_probe)()

            num_cph += added_call*(3600.0 / p.interval)

        return num_cph


    def get_publisher_candidates(self, current, n):
        self._sorted_publisher = sorted(self._sorted_publisher,  key=lambda x: x.next_scan)
        #get the ones with the "older" not handled publisher

        candidates = []
        num_predicted_calls = 0
        for p in self._sorted_publisher:
            if p.name is not None:
                if p.is_ts_allows_emission(current):
                    added_call = 1
                    if p.target and p.method_num_call_probe is not None:
                        #this can be highly dynamic and could be 0 (like for a reset)
                        added_call = getattr(p.target, p.method_num_call_probe)()

                    if num_predicted_calls + added_call > n:
                        break

                    num_predicted_calls += added_call
                    candidates.append(p)

        return candidates, num_predicted_calls

    def adjust_per_scan_numbers(self):
        hrl = self._adjusted_hourly_rate_limit
        if hrl is None:
            hrl = self._initial_hourly_rate_limit

        self._min_call_per_interval = int(min((hrl * SCAN_INTERVAL) // 3600, (SCAN_INTERVAL // 10) * self._10s_rate_limit))
        self._max_call_per_interval = int(max((hrl * SCAN_INTERVAL) // 3600, (SCAN_INTERVAL // 10) * self._10s_rate_limit))

    def adjust_intervals_to_target(self, target, force_adjust=False, redo_next_scan=True, do_wait_scan_for_cph_to_target=False):

        current = int(time())

        if do_wait_scan_for_cph_to_target:
            wait_time = self.get_wait_time_to_reach_targets(current, int(target*0.80)) #wait for a bit longer to reach 80% of the target cph to have 20% of room to breath
        else:
            wait_time = 0

        ctph = self.compute_theoretical_call_per_hour()

        self._adjusted_hourly_rate_limit = int(target)

        if force_adjust is True  or ctph >= target:
            _LOGGER.info("Adapting intervals to comply with the requested rate limit from theoretical %f to %i (initial: %i) waiting for : %i s", ctph, target, self._initial_hourly_rate_limit, wait_time)

            for p in self._sorted_publisher:
                p.interval = int((p.interval * ctph) / target) + 1
                if redo_next_scan:
                    p.set_next_randomized_scan(current, wait_time=wait_time)

        self.adjust_per_scan_numbers()

    def get_wait_time_to_reach_targets(self, current:int, target:int) -> int:


        delta = int(len(self.rolling_hour) - target)

        if delta <= 0:
            return 0

        if delta > len(self.rolling_hour):
            #just wait for the full cleaning of the rolling one
            return 3600 + 2*SCAN_INTERVAL
        else:
            tStop = self.rolling_hour[delta - 1]

            return max(SCAN_INTERVAL, int(tStop + 3600 + SCAN_INTERVAL - current))


    async def async_update(self, event_time: datetime) -> None:
        """Update device. """

        #no need all the time but fairly quick
        if self._adjusted_hourly_rate_limit is None:
            self.adjust_intervals_to_target(self._initial_hourly_rate_limit, force_adjust=False)

        #keep cph up to date whatever happens
        self.add_api_call(0)

        cph_init = self.get_current_call_per_hour()

        num_call = max(0, min(self._max_call_per_interval, self._adjusted_hourly_rate_limit - cph_init))

        if num_call > 0:
            delta_sleep = SCAN_INTERVAL // (3*num_call)
        else:
            _LOGGER.info("Getting 0 approved calls: adjusted limit : %f current cph: %i", self._adjusted_hourly_rate_limit, self.get_current_call_per_hour())
            delta_sleep = 0

        current = int(time())

        candidates, num_predicted_calls = self.get_publisher_candidates(current, num_call)

        if len(candidates) <= 1:
            delta_sleep = 0

        has_been_throttled = False
        for data_class in candidates:

            if publisher := data_class.name:
                error, throttling_error = await self.async_fetch_data(publisher)

                if throttling_error:
                    has_been_throttled = True
                    break
                elif error:
                    data_class.num_consecutive_errors += 1
                    _LOGGER.debug("Error on publisher: %s, num_errors: %i", publisher, data_class.num_consecutive_errors)
                    #Try again a bit later, this is not a rate limit
                    data_class.next_scan = current + SCAN_INTERVAL*(data_class.num_consecutive_errors + 1)
                else:
                    self.publisher[publisher].push_emission(current)
                    self.publisher[publisher].set_next_randomized_scan(current)

            if delta_sleep > 0:
                await asyncio.sleep(delta_sleep)


        cph = self.get_current_call_per_hour()
        current = int(time())
        _LOGGER.debug("Calls per hour: %i , num call asked: %i num candidates: %i num call predicted : %i  num pub: %i", cph, num_call, len(candidates), num_predicted_calls, len(self._sorted_publisher))

        if self._last_cph_change is None or current - self._last_cph_change > 3600:

            if has_been_throttled or (cph > self._adjusted_hourly_rate_limit and cph > cph_init and num_predicted_calls > 0):
                _LOGGER.info("Calls per hour hit rate limit: %i/%i throttled API: %s", cph, self._adjusted_hourly_rate_limit, has_been_throttled)
                #remove 20% each time ...
                new_target = int(self._adjusted_hourly_rate_limit * CPH_ADJUSTEMENT_DOWN)
                self.adjust_intervals_to_target(new_target, force_adjust=False, redo_next_scan=True, do_wait_scan_for_cph_to_target=True)
                self._last_cph_change = current
            else:
                new_target = int(min(self._initial_hourly_rate_limit, int(self._adjusted_hourly_rate_limit * CPH_ADJUSTEMENT_BACK_UP)))
                if self._adjusted_hourly_rate_limit != self._initial_hourly_rate_limit:
                    _LOGGER.debug("bumping back rate limit: %i / (initial: %i)", new_target, self._initial_hourly_rate_limit)
                    #every "good"  hour window, let get the rate limit up (with a limit) going up only by half what we went down in case of issue (so here 10% up)
                    self.adjust_intervals_to_target(new_target, force_adjust=True, redo_next_scan=False)
                    self._last_cph_change = current



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

    async def async_fetch_data(self, signal_name: str, update_only=False) -> (bool, bool):
        """Fetch data and notify."""
        has_error = False
        has_throttling_error = False


        if update_only is False:

            num_fetch = 0
            try:
                num_fetch = await getattr(self.publisher[signal_name].target, self.publisher[signal_name].method)(
                    **self.publisher[signal_name].kwargs
                )
            except (pyatmo.NoDevice) as err:
                _LOGGER.debug("fetch error NoDevice: %s", err)
                has_error = True
            except (pyatmo.ApiErrorThrottling) as err:
                _LOGGER.debug("fetch error Throttling: %s", err)
                has_throttling_error = True
            except (pyatmo.ApiError) as err:
                _LOGGER.debug("fetch error ApiError: %s", err)
                has_error = True
            except (TimeoutError, aiohttp.ClientConnectorError) as err:
                _LOGGER.debug("fetch error Timeout or ClientConnectorError: %s", err)
                return True, False
            except Exception as err:
                _LOGGER.debug("fetch error unknown %s", err)
                has_error = True

            try:
                num_fetch = int(num_fetch)
            except:
                num_fetch = 1

            self.add_api_call(num_fetch)

        for update_callback in self.publisher[signal_name].subscriptions:
            if update_callback:
                update_callback()

        return has_error, has_throttling_error

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
        n = len(self._sorted_publisher)
        self.adjust_per_scan_numbers()
        delta_scan = int(SCAN_INTERVAL//(max(self._min_call_per_interval, self._max_call_per_interval)//2 + 1))

        self.publisher[signal_name] = NetatmoPublisher(
            name=signal_name,
            interval=interval,
            next_scan=time() + interval//2 + n*delta_scan, #at init time try to get some data
            target=target,
            subscriptions={update_callback},
            method=PUBLISHERS[publisher],
            method_num_call_probe=PUBLISHERS_CALL_PROBER.get(publisher, None),
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

        #_LOGGER.debug("Publisher %s added current total cph %f / rate limit %i", signal_name, self.compute_theoretical_call_per_hour(), self._adjusted_hourly_rate_limit)

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
