"""Module to represent somfy modules."""
from __future__ import annotations

import logging

from ..modules.module import FirmwareMixin, Module, RfMixin, ShutterMixin

LOG = logging.getLogger(__name__)


class TPSRS(FirmwareMixin, RfMixin, ShutterMixin, Module):
    ...
