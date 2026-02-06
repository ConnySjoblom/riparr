"""Metadata services for disc identification and lookup."""

from riparr.metadata.arm_api import lookup_disc
from riparr.metadata.dvdid import compute_dvd_id
from riparr.metadata.mediainfo import get_media_info

__all__ = ["compute_dvd_id", "get_media_info", "lookup_disc"]
