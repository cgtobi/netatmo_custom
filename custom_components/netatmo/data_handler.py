"""The Netatmo data handler."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from time import time
from typing import Any

import aiohttp

try:
    from . import pyatmo
    from .pyatmo.modules.device_types import (
        DeviceCategory as NetatmoDeviceCategory,
        DeviceType as NetatmoDeviceType,
    )
except Exception:  # pylint: disable=broad-except
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
    CONF_DISABLED_HOMES,
    DATA_PERSONS,
    DATA_SCHEDULES,
    DOMAIN,
    MANUFACTURER,
    NETATMO_CREATE_BATTERY,
    NETATMO_CREATE_CAMERA,
    NETATMO_CREATE_CAMERA_LIGHT,
    NETATMO_CREATE_CLIMATE,
    NETATMO_CREATE_COVER,
    NETATMO_CREATE_ENERGY,
    NETATMO_CREATE_FAN,
    NETATMO_CREATE_GAS,
    NETATMO_CREATE_LIGHT,
    NETATMO_CREATE_ROOM_SENSOR,
    NETATMO_CREATE_SELECT,
    NETATMO_CREATE_SENSOR,
    NETATMO_CREATE_SWITCH,
    NETATMO_CREATE_WATER,
    NETATMO_CREATE_WEATHER_SENSOR,
    PLATFORMS,
    WEBHOOK_ACTIVATION,
    WEBHOOK_DEACTIVATION,
    WEBHOOK_NACAMERA_CONNECTION,
    WEBHOOK_PUSH_TYPE,
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
    ENERGY_MEASURE: "async_update_energy",
}

# Netatmo rate limiting: https://dev.netatmo.com/guideline

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

# the system will ensure that we never overcross neither CALL_PER_HOUR or CALL_PER_TEN_SECONDS
# whatever are the other numbers of the number of devices we have
# (each device need a call for energy, can grow a lot)
# There is a rolling buffer of calls to be sure of what has been called in teh last
# 10s or hour and take decisions based on that

CALL_PER_HOUR = "CALL_PER_HOUR"
CALL_PER_TEN_SECONDS = "CALL_PER_10S"
SCAN_INTERVAL = "SCAN_INTERVAL"

NETATMO_USER_CALL_LIMITS = {
    CALL_PER_HOUR: 20,        # 20 to comply with the global limit of (20 * number of users) requests every hour
    CALL_PER_TEN_SECONDS: 2,  # 2  to comply with the global limit of (2 * number of users) requests every 10 seconds
    ACCOUNT: 10800,
    HOME: 200, # 200s between calls it means 18 calls per hours
    WEATHER: 600,
    AIR_CARE: 300,
    PUBLIC: 600,
    EVENT: 600,
    ENERGY_MEASURE: 1800,
    SCAN_INTERVAL: 60
}
NETATMO_DEV_CALL_LIMITS = {
    CALL_PER_HOUR: 450,        # in this case per user limit is: 500 requests every hour
    CALL_PER_TEN_SECONDS: 45,  # in this case per user limit is: 50 requests every 10 seconds
    ACCOUNT: 3600,
    HOME: 5,
    WEATHER: 200,
    AIR_CARE: 100,
    PUBLIC: 200,
    EVENT: 200,
    ENERGY_MEASURE: 900,
    SCAN_INTERVAL: 5
}

# this is for the dynamic API rate limiting adjustement to deal with rare occasions
# where there may be a need to go lower in API consumption (and then back higher
# to get to an equilibrium)
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
    num_consecutive_errors: int
    data_handler: NetatmoDataHandler

    def __init__(self, name, interval, next_scan, target, subscriptions, method, data_handler,
                 kwargs):
        self.name = name
        self.interval = interval
        self.next_scan = next_scan
        self.target = target
        self.subscriptions = subscriptions
        self.method = method
        self.kwargs = kwargs
        self.num_consecutive_errors = 0
        self.data_handler = data_handler

    def push_emission(self, ts):
        self.num_consecutive_errors = 0

    def set_next_scan(self, ts, wait_time=0):
        # rand_delta = int(self.interval // 8)
        # rnd = random.randint(0 - rand_delta, rand_delta)
        # self.next_scan = ts + max(wait_time + abs(rnd), self.interval + rnd)
        self.next_scan = ts + self.interval + wait_time

    def is_ts_allows_emission(self, ts):
        return self.next_scan <= ts  # + max(self.data_handler._scan_interval, self.interval // 12)


class NetatmoDataHandler:
    """Manages the Netatmo data handling."""

    account: pyatmo.AsyncAccount | None

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize self."""
        self.hass = hass
        self.account = None
        self._init_complete = False
        self._init_topology_complete = False
        self._init_update_status_complete = False
        self.config_entry = config_entry
        self._auth = hass.data[DOMAIN][config_entry.entry_id][AUTH]
        self.publisher: dict[str, NetatmoPublisher] = {}
        self._sorted_publisher: list[NetatmoPublisher] = []
        self._webhook: bool = False
        if config_entry.data["auth_implementation"] == cloud.DOMAIN:
            limits = NETATMO_USER_CALL_LIMITS
            _LOGGER.debug("NETATMO INTEGRATION : USE GLOBAL LIMITS")
        else:
            limits = NETATMO_DEV_CALL_LIMITS
            _LOGGER.debug("NETATMO INTEGRATION : USE DEV LIMITS FOR YOUR OWN APP")

        self._limits = limits
        self._scan_interval = limits[SCAN_INTERVAL]
        self._initial_hourly_rate_limit = limits[CALL_PER_HOUR]

        self._10s_rate_limit = limits[CALL_PER_TEN_SECONDS]

        self.rolling_hour = [] # used to store API calls and have a rolling windws of calls
        self._adjusted_hourly_rate_limit = None
        self._last_cph_change = None

        self._min_call_per_interval = None
        self._max_call_per_interval = None

        self.adjust_per_scan_numbers()

    def add_api_call(self, n):
        """Add an API call to the rolling window of calls."""
        current = time()
        for i in range(n):
            self.rolling_hour.append(current)

        while len(self.rolling_hour) > 0 and current - self.rolling_hour[0] > 3600:
            self.rolling_hour.pop(0)

    def get_current_calls_count_per_hour(self):
        return int(len(self.rolling_hour))

    async def _init_update_topology_if_needed(self):

        if self._init_topology_complete is False:
            disabled_homes = self.config_entry.options.get(CONF_DISABLED_HOMES, [])
            has_error = False
            try:
                await self.account.async_update_topology(disabled_homes_ids=disabled_homes)
                self.add_api_call(1)

            except (pyatmo.NoDevice, pyatmo.ApiError) as err:
                _LOGGER.debug("init account.async_update_topology error NoDevice or ApiError %s", err)
                has_error = True
            except (TimeoutError, aiohttp.ClientConnectorError) as err:
                _LOGGER.debug("init account.async_update_topology error Timeout or ClientConnectorError: %s",  err)
                has_error = True
            except Exception as err:
                _LOGGER.debug("init account.async_update_topology error unknown %s",  err)
                has_error = True

            if has_error is False:
                self._init_topology_complete = True

        return self._init_topology_complete

    async def _init_update_status_if_needed(self):

        if self._init_update_status_complete is False:

            has_error = False
            try:
                num_calls = 0
                for h in self.account.homes:
                    await self.account.async_update_status(h)
                    num_calls += 1

                self.add_api_call(num_calls)

            except (pyatmo.NoDevice, pyatmo.ApiError) as err:
                _LOGGER.debug("init account.async_update_status error NoDevice or ApiError %s", err)
                has_error = True
            except (TimeoutError, aiohttp.ClientConnectorError) as err:
                _LOGGER.debug("init account.async_update_status error Timeout or ClientConnectorError: %s", err)
                has_error = True
            except Exception as err:
                _LOGGER.debug("init account.async_update_status error unknown %s", err)
                has_error = True

            if has_error is False:
                self._init_update_status_complete = True

        return self._init_update_status_complete


    async def _do_complete_init_if_needed(self):
        if self._init_complete is False:
            if await self._init_update_topology_if_needed() and await self._init_update_status_if_needed():
                # we do are in a proper state
                # do update only as async_update_topology will call the APIS, and update topology done already

                # the code below will be run only once
                await self.subscribe_with_target(
                    publisher=ACCOUNT,
                    signal_name=ACCOUNT,
                    target=None,
                    update_callback=None,
                    update_only=True
                )

                # it only registers signals to be emitted later
                await self.hass.config_entries.async_forward_entry_setups(
                    self.config_entry, PLATFORMS
                )

                #perform dispatch to create entities, modules, etc
                await self.async_dispatch()
                _LOGGER.info("Netatmo integration initialized")
                self._init_complete = True

        return self._init_complete

    async def async_setup(self) -> None:

        self._init_complete = False
        self._init_topology_complete = False
        self._init_update_status_complete = False

        self.account = pyatmo.AsyncAccount(self._auth)

        if await self._do_complete_init_if_needed() is False:
            _LOGGER.info("Netatmo integration not properly initialized at startup, trying again in %i seconds",self._scan_interval)

        """Set up the Netatmo data handler. Do that at the end to have a good and proper init before calling it"""
        self.config_entry.async_on_unload(
            async_track_time_interval(
                self.hass, self.async_update, timedelta(seconds=self._scan_interval)
            )
        )

        self.config_entry.async_on_unload(
            async_dispatcher_connect(
                self.hass,
                f"signal-{DOMAIN}-webhook-None",
                self.handle_event,
            )
        )

    def compute_theoretical_call_per_hour(self):
        num_cph = 0.0
        for p in self._sorted_publisher:
            num_cph += 1 * (3600.0 / p.interval)

        return num_cph

    def get_publisher_candidates(self, current, n):
        self._sorted_publisher = sorted(self._sorted_publisher, key=lambda x: x.next_scan)
        # get the ones with the "older" not handled publisher

        candidates = []
        num_predicted_calls = 0
        for p in self._sorted_publisher:
            if p.name is not None:
                if p.is_ts_allows_emission(current):
                    if num_predicted_calls + 1 > n:
                        break
                    num_predicted_calls += 1
                    candidates.append(p)

        return candidates, num_predicted_calls

    def adjust_per_scan_numbers(self):
        hrl = self._adjusted_hourly_rate_limit
        if hrl is None:
            hrl = self._initial_hourly_rate_limit

        scan_limit_per_hour = (hrl * self._scan_interval) // 3600

        self._min_call_per_interval = int(min(scan_limit_per_hour, (self._scan_interval / 10.0) * self._10s_rate_limit))
        self._max_call_per_interval = int(max(scan_limit_per_hour, (self._scan_interval / 10.0) * self._10s_rate_limit))

    def adjust_intervals_to_target(self,
                                   target=None,
                                   force_adjust=False,
                                   redo_next_scan=True,
                                   do_wait_scan_for_cph_to_target=False):

        current = int(time())

        if target is None:
            if self._adjusted_hourly_rate_limit is None:
                target = self._initial_hourly_rate_limit
            else:
                target = self._adjusted_hourly_rate_limit
        else:
            target = min(self._initial_hourly_rate_limit, int(target))

        if (self._adjusted_hourly_rate_limit is not None and force_adjust is False
                and target == self._adjusted_hourly_rate_limit):
            # no need to adjust anything
            return

        if do_wait_scan_for_cph_to_target:
            # wait for a bit longer to reach 80% of the target cph to have 20% of room to breath
            wait_time = self.get_wait_time_to_reach_targets(current, int(target * 0.80))
        else:
            wait_time = 0

        ctph = self.compute_theoretical_call_per_hour()

        self._adjusted_hourly_rate_limit = int(target)

        if force_adjust is True or ctph >= target:
            msg = ("Adapting intervals to comply with the requested rate limit "
                   "from theoretical %f to %i (initial: %i) waiting for : %i s")
            _LOGGER.info(msg, ctph, target, self._initial_hourly_rate_limit, wait_time)

            for p in self._sorted_publisher:
                p.interval = int((p.interval * ctph) / target) + 1

            if redo_next_scan:
                self._spread_next_scans(wait_time=wait_time)

        self.adjust_per_scan_numbers()

    def get_wait_time_to_reach_targets(self, current: int, target: int) -> int:

        delta = int(len(self.rolling_hour) - target)

        if delta <= 0:
            return 0

        if delta > len(self.rolling_hour):
            # just wait for the full cleaning of the rolling one
            return 3600 + 2 * self._scan_interval
        else:
            t_stop = self.rolling_hour[delta - 1]

            return max(self._scan_interval, int(t_stop + 3600 + self._scan_interval - current))

    async def async_update(self, event_time: datetime) -> None:
        """Update device."""

        if await self._do_complete_init_if_needed() is False:
            _LOGGER.info("Netatmo integration not yet initialized, trying again in %i seconds", self._scan_interval)

        # no need all the time but fairly quick
        self.adjust_intervals_to_target()

        # keep cph up to date whatever happens (time increment)
        self.add_api_call(0)

        cph_init = self.get_current_calls_count_per_hour()

        num_call = max(0, min(self._max_call_per_interval, self._adjusted_hourly_rate_limit - cph_init))

        if num_call > 0:
            delta_sleep = self._scan_interval / (3.0 * num_call)
        else:
            _LOGGER.info("Getting 0 approved calls: adjusted limit : %f current cph: %i",
                         self._adjusted_hourly_rate_limit, self.get_current_calls_count_per_hour())
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
                    _LOGGER.debug("Error on publisher: %s, num_errors: %i",
                                  publisher, data_class.num_consecutive_errors)
                    # Try again a bit later, this is not a rate limit
                    data_class.next_scan = current + self._scan_interval  # *(data_class.num_consecutive_errors + 1)
                else:
                    self.publisher[publisher].push_emission(current)
                    self.publisher[publisher].set_next_scan(current)

            if delta_sleep > 0:
                await asyncio.sleep(delta_sleep)

        cph = self.get_current_calls_count_per_hour()
        current = int(time())
        msg = "Calls per hour: %i , num call asked: %i num candidates: %i num call predicted : %i  num pub: %i"
        _LOGGER.debug(msg, cph, num_call, len(candidates), num_predicted_calls, len(self._sorted_publisher))

        if self._last_cph_change is None or current - self._last_cph_change > 3600:

            if (has_been_throttled or
                    (cph > self._adjusted_hourly_rate_limit and cph > cph_init and num_predicted_calls > 0)):
                _LOGGER.info("Calls per hour hit rate limit: %i/%i throttled API: %s",
                             cph, self._adjusted_hourly_rate_limit, has_been_throttled)
                # remove 20% each time ...
                new_target = int(self._adjusted_hourly_rate_limit * CPH_ADJUSTEMENT_DOWN)
                self.adjust_intervals_to_target(new_target,
                                                force_adjust=False,
                                                redo_next_scan=True,
                                                do_wait_scan_for_cph_to_target=True)
                self._last_cph_change = current
            else:
                new_target = int(min(self._initial_hourly_rate_limit,
                                     int(self._adjusted_hourly_rate_limit * CPH_ADJUSTEMENT_BACK_UP)))
                if self._adjusted_hourly_rate_limit != self._initial_hourly_rate_limit:
                    _LOGGER.debug("bumping back rate limit: %i / (initial: %i)",
                                  new_target, self._initial_hourly_rate_limit)
                    # every "good"  hour window, let get the rate limit up (with a limit) going up only by half
                    # what we went down in case of issue (so here 10% up)
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

            try:
                await getattr(self.publisher[signal_name].target, self.publisher[signal_name].method)(
                    **self.publisher[signal_name].kwargs
                )
            except pyatmo.NoDevice as err:
                _LOGGER.debug("fetch error NoDevice: %s", err)
                has_error = True
            except pyatmo.ApiHomeReachabilityError as err:
                _LOGGER.debug("fetch error Not Reachable Home: %s", err)
                has_error = True
            except pyatmo.ApiErrorThrottling as err:
                _LOGGER.debug("fetch error Throttling: %s", err)
                has_throttling_error = True
            except pyatmo.ApiError as err:
                _LOGGER.debug("fetch error ApiError: %s", err)
                has_error = True
            except (TimeoutError, aiohttp.ClientConnectorError) as err:
                _LOGGER.debug("fetch error Timeout or ClientConnectorError: %s", err)
                return True, False
            except Exception as err:
                _LOGGER.debug("fetch error unknown %s", err)
                has_error = True

            self.add_api_call(1)

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
        await self.subscribe_with_target(publisher=publisher,
                                         signal_name=signal_name,
                                         target=None,
                                         update_callback=update_callback,
                                         update_only=False,
                                         **kwargs)

    async def subscribe_with_target(
            self,
            publisher: str,
            signal_name: str,
            target: Any,
            update_callback: CALLBACK_TYPE | None,
            update_only=False,
            **kwargs: Any
    ) -> None:
        """Subscribe to publisher."""
        if signal_name in self.publisher:
            if update_callback not in self.publisher[signal_name].subscriptions:
                self.publisher[signal_name].subscriptions.add(update_callback)
            return

        if target is None:
            target = self.account

        if publisher == PUBLIC:
            kwargs = {"area_id": self.account.register_public_weather_area(**kwargs)}
        elif publisher == ACCOUNT:
            kwargs = {"disabled_homes_ids": self.config_entry.options.get(CONF_DISABLED_HOMES, [])}

        interval = int(self._limits[publisher])
        self.publisher[signal_name] = NetatmoPublisher(
            name=signal_name,
            interval=interval,
            next_scan=time() + interval // 2,  # start sooner at start to get some data points
            target=target,
            subscriptions={update_callback},
            method=PUBLISHERS[publisher],
            data_handler=self,
            kwargs=kwargs,
        )

        try:
            await self.async_fetch_data(signal_name, update_only=update_only)
        except KeyError:
            # in case we have a bad formed response from the API
            self.publisher.pop(signal_name)
            _LOGGER.debug("Publisher %s removed at subscription due to mal formed response!!!!!!", signal_name)
            raise

        self._sorted_publisher.append(self.publisher[signal_name])
        _LOGGER.debug("Publisher %s added", signal_name)

        # do spread each time, not very efficient but done only at start
        self._spread_next_scans()

    def _spread_next_scans(self, wait_time=0):
        intervals = {}
        current = int(time())

        for p in self._sorted_publisher:
            if p.interval not in intervals:
                intervals[p.interval] = [p]
            else:
                intervals[p.interval].append(p)

        for interval, publishers in intervals.items():
            if len(publishers) > 1:
                for i, p in enumerate(publishers):
                    p.next_scan = current + max(wait_time, 1) + int(i * interval // len(publishers))
            else:
                publishers[0].next_scan = current + max(wait_time, 1) + interval//2



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
            NetatmoDeviceCategory.dimmer: [NETATMO_CREATE_LIGHT, NETATMO_CREATE_SENSOR, NETATMO_CREATE_ENERGY],
            NetatmoDeviceCategory.shutter: [NETATMO_CREATE_COVER, NETATMO_CREATE_SENSOR, NETATMO_CREATE_ENERGY],
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

            signals = netatmo_type_signal_map.get(module.device_category, [])


            # unfortunately the ecoounter is handled in a very peculiar way
            # it is its own bridge, and sensor are hardcoded by name
            if (module.device_category == NetatmoDeviceCategory.meter and
                    module.device_type == NetatmoDeviceType.NLE):
                    if module.modules or module.bridge is None:
                        # if we have an ecocounter as bridge, do not add its sensors as it is only its owned modules
                        # that are sporting the real sensors wiht power and energy .... except that power is not
                        # available in the case of this kind of ecocounter
                        continue
                    elif module.bridge:
                        # sensor are encoded by name unfortunately here :(

                        name = module.entity_id
                        sp = name.split("#")
                        if len(sp) != 2:
                            continue
                        num = sp[1]
                        try:
                            num = int(num)
                        except:
                            continue

                        if num > 5:
                            if num == 6:
                                signals = [NETATMO_CREATE_SENSOR, NETATMO_CREATE_GAS]
                            else:
                                signals = [NETATMO_CREATE_SENSOR, NETATMO_CREATE_WATER]

            for signal in signals:
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
