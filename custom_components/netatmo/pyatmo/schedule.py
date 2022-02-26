"""Module to represent a Netatmo schedule."""
from .future__ import annotations

import logging
from .taclasses import dataclass
from .ping import TYPE_CHECKING

from .modules.base_class import NetatmoBase

if TYPE_CHECKING:
    from .ome import Home

LOG = logging.getLogger(__name__)


@dataclass
class NetatmoSchedule(NetatmoBase):
    """Class to represent a Netatmo schedule."""

    selected: bool
    away_temp: float | None
    hg_temp: float | None

    def __init__(self, home: Home, raw_data) -> None:
        super().__init__(raw_data)
        self.home = home
        self.selected = raw_data.get("selected", False)
        self.hg_temp = raw_data.get("hg_temp")
        self.away_temp = raw_data.get("away_temp")
