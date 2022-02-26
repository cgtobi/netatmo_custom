"""The Netatmo data handler."""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import islice
import logging
from time import time
from typing import Any

from . import pyatmo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    AUTH,
    DOMAIN,
    MANUFACTURER,
    WEBHOOK_ACTIVATION,
    WEBHOOK_DEACTIVATION,
    WEBHOOK_NACAMERA_CONNECTION,
    WEBHOOK_PUSH_TYPE,
)

_LOGGER = logging.getLogger(__name__)

ACCOUNT = "account"
HOME = "home"
WEATHER = "weather"
AIR_CARE = "air_care"
PUBLIC = "public"

PUBLISHERS = {
    "account": "async_update_topology",
    "home": "async_update_status",
    "weather": "async_update_weather_stations",
    "air_care": "async_update_air_care",
    "public": "async_update_public_weather",
}

BATCH_SIZE = 3
DEFAULT_INTERVALS = {
    ACCOUNT: 3600,
    HOME: 300,
    WEATHER: 600,
    AIR_CARE: 300,
    PUBLIC: 600,
}
SCAN_INTERVAL = 60


@dataclass
class NetatmoDevice:
    """Netatmo device class."""

    data_handler: NetatmoDataHandler
    device: pyatmo.NetatmoModule
    parent_id: str
    state_class_name: str


@dataclass
class NetatmoPublisher:
    """Class for keeping track of Netatmo data class metadata."""

    name: str
    interval: int
    next_scan: float
    subscriptions: list[CALLBACK_TYPE | None]
    method: str
    kwargs: dict


class NetatmoDataHandler:
    """Manages the Netatmo data handling."""

    account: pyatmo.AsyncAccount

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize self."""
        self.hass = hass
        self.config_entry = config_entry
        self._auth = hass.data[DOMAIN][config_entry.entry_id][AUTH]
        self.publisher: dict = {}
        self.data: dict = {}
        self._queue: deque = deque()
        self._webhook: bool = False

    async def async_setup(self) -> None:
        """Set up the Netatmo data handler."""
        print("""Set up the Netatmo data handler.""")
        async_track_time_interval(
            self.hass, self.async_update, timedelta(seconds=SCAN_INTERVAL)
        )

        self.config_entry.async_on_unload(
            async_dispatcher_connect(
                self.hass,
                f"signal-{DOMAIN}-webhook-None",
                self.handle_event,
            )
        )

        self.account = pyatmo.AsyncAccount(self._auth)

        await self.subscribe(ACCOUNT, ACCOUNT, None)

    async def async_update(self, event_time: datetime) -> None:
        """
        Update device.

        We do up to BATCH_SIZE calls in one update in order
        to minimize the calls on the api service.
        """
        for data_class in islice(self._queue, 0, BATCH_SIZE):
            if data_class.next_scan > time():
                continue

            if data_class_name := data_class.name:
                self.publisher[data_class_name].next_scan = time() + data_class.interval

                await self.async_fetch_data(data_class_name)
                print("Update device.", data_class_name)

        self._queue.rotate(BATCH_SIZE)

    @callback
    def async_force_update(self, data_class_entry: str) -> None:
        """Prioritize data retrieval for given data class entry."""
        self.publisher[data_class_entry].next_scan = time()
        self._queue.rotate(-(self._queue.index(self.publisher[data_class_entry])))

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

    async def async_fetch_data(self, signal_name: str) -> None:
        """Fetch data and notify."""
        try:
            await getattr(self.account, self.publisher[signal_name].method)(
                **self.publisher[signal_name].kwargs
            )

        except pyatmo.NoDevice as err:
            _LOGGER.debug(err)
            self.data[signal_name] = None

        except pyatmo.ApiError as err:
            _LOGGER.debug(err)

        except asyncio.TimeoutError as err:
            _LOGGER.debug(err)
            return

        for update_callback in self.publisher[signal_name].subscriptions:
            if update_callback:
                update_callback()

    async def subscribe(
        self,
        publisher: str,
        signal_name: str,
        update_callback: CALLBACK_TYPE | None,
        **kwargs: Any,
    ) -> None:
        """Subscribe to publisher."""
        print("""Subscribe to publisher.""", publisher, signal_name)
        if signal_name in self.publisher:
            if update_callback not in self.publisher[signal_name].subscriptions:
                self.publisher[signal_name].subscriptions.append(update_callback)
            return

        self.publisher[signal_name] = NetatmoPublisher(
            name=signal_name,
            interval=DEFAULT_INTERVALS[publisher],
            next_scan=time() + DEFAULT_INTERVALS[publisher],
            subscriptions=[update_callback],
            method=PUBLISHERS[publisher],
            kwargs=kwargs,
        )

        try:
            await self.async_fetch_data(signal_name)
        except KeyError:
            self.publisher.pop(signal_name)
            raise

        self._queue.append(self.publisher[signal_name])
        _LOGGER.debug("Publisher %s added", signal_name)

    async def unsubscribe(
        self, signal_name: str, update_callback: CALLBACK_TYPE | None
    ) -> None:
        """Unsubscribe from publisher."""
        self.publisher[signal_name].subscriptions.remove(update_callback)

        if not self.publisher[signal_name].subscriptions:
            self._queue.remove(self.publisher[signal_name])
            self.publisher.pop(signal_name)
            self.data.pop(signal_name)
            _LOGGER.debug("Publisher %s removed", signal_name)

    @property
    def webhook(self) -> bool:
        """Return the webhook state."""
        return self._webhook
