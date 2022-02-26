"""Module to represent a Netatmo person."""
from .future__ import annotations

import logging
from .taclasses import dataclass
from .ping import TYPE_CHECKING

from .modules.base_class import NetatmoBase

if TYPE_CHECKING:
    from .ome import Home

LOG = logging.getLogger(__name__)


@dataclass
class NetatmoPerson(NetatmoBase):
    """Class to represent a Netatmo person."""

    pseudo: str | None
    url: str | None

    def __init__(self, home: Home, raw_data) -> None:
        super().__init__(raw_data)
        self.home = home
        self.pseudo = raw_data.get("pseudo")
        self.url = raw_data.get("url")
