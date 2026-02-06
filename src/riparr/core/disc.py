"""Disc and title models."""

from datetime import timedelta
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, computed_field


class DiscType(StrEnum):
    """Type of optical disc."""

    DVD = "dvd"
    BLURAY = "bluray"
    UHD = "uhd"
    UNKNOWN = "unknown"


class MediaType(StrEnum):
    """Type of media content."""

    MOVIE = "movie"
    TV = "tv"
    UNKNOWN = "unknown"


class AudioTrack(BaseModel):
    """Audio track information."""

    index: int
    codec: str = ""
    channels: int = 2
    language: str = "und"
    name: str = ""
    default: bool = False

    @computed_field
    @property
    def channel_layout(self) -> str:
        """Human-readable channel layout."""
        layouts = {
            1: "Mono",
            2: "Stereo",
            6: "5.1",
            8: "7.1",
        }
        return layouts.get(self.channels, f"{self.channels}ch")


class SubtitleTrack(BaseModel):
    """Subtitle track information."""

    index: int
    codec: str = ""
    language: str = "und"
    name: str = ""
    forced: bool = False
    default: bool = False


class Title(BaseModel):
    """A title/track on the disc."""

    index: int
    duration: int = Field(default=0, description="Duration in seconds")
    chapters: int = 0
    size_bytes: int = 0
    video_codec: str = ""
    resolution: str = ""
    frame_rate: float = 0.0
    audio_tracks: list[AudioTrack] = Field(default_factory=list)
    subtitle_tracks: list[SubtitleTrack] = Field(default_factory=list)
    segment_count: int = 1
    segment_map: str = ""
    source_file: str = ""
    output_file: str = ""

    @computed_field
    @property
    def duration_str(self) -> str:
        """Duration as HH:MM:SS string."""
        td = timedelta(seconds=self.duration)
        hours, remainder = divmod(int(td.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    @computed_field
    @property
    def size_str(self) -> str:
        """Human-readable size string."""
        if self.size_bytes == 0:
            return "N/A"
        size = float(self.size_bytes)
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    @computed_field
    @property
    def is_hdr(self) -> bool:
        """Check if title has HDR content."""
        hdr_indicators = ["HDR", "HDR10", "Dolby Vision", "DV", "HLG"]
        return any(ind in self.video_codec for ind in hdr_indicators)


class DiscMetadata(BaseModel):
    """Metadata from external sources."""

    title: str
    year: int | None = None
    media_type: MediaType = MediaType.UNKNOWN
    imdb_id: str | None = None
    tmdb_id: int | None = None
    poster_url: str | None = None
    overview: str | None = None
    season: int | None = None
    episode_count: int | None = None


class Disc(BaseModel):
    """Represents an optical disc."""

    name: str = ""
    device: str = ""
    disc_type: DiscType = DiscType.UNKNOWN
    dvd_id: str | None = None
    label: str | None = None
    titles: list[Title] = Field(default_factory=list)
    metadata: DiscMetadata | None = None

    @computed_field
    @property
    def total_duration(self) -> int:
        """Total duration of all titles in seconds."""
        return sum(t.duration for t in self.titles)

    @computed_field
    @property
    def total_size(self) -> int:
        """Total size of all titles in bytes."""
        return sum(t.size_bytes for t in self.titles)

    @classmethod
    def from_label(cls, label: str, device: str = "") -> Self:
        """Create disc from label string."""
        return cls(name=label, label=label, device=device)
