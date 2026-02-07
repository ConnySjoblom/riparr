"""Pydantic settings for riparr configuration."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    model_config = SettingsConfigDict(
        env_prefix="RIPARR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Directories
    raw_dir: Path = Field(
        default=Path("/data/raw"),
        description="Directory for raw MKV output from MakeMKV",
    )
    output_dir: Path = Field(
        default=Path("/data/media"),
        description="Final output directory for encoded files",
    )
    temp_dir: Path = Field(
        default=Path("/tmp/riparr"),
        description="Temporary working directory",
    )

    # Device settings
    default_device: str = Field(
        default="/dev/sr0",
        description="Default optical drive device",
    )
    eject_after_rip: bool = Field(
        default=True,
        description="Eject disc after successful rip",
    )

    # MakeMKV settings
    makemkv_path: str = Field(
        default="makemkvcon",
        description="Path to makemkvcon binary",
    )
    makemkv_key: str | None = Field(
        default=None,
        description="MakeMKV license key (optional)",
    )

    # Title selection
    min_movie_duration: int = Field(
        default=600,
        description="Minimum title duration for movies in seconds (default 10 min)",
    )
    min_tv_duration: int = Field(
        default=300,
        description="Minimum title duration for TV episodes in seconds (default 5 min)",
    )
    max_titles: int = Field(
        default=50,
        description="Maximum number of titles to rip from a disc",
    )

    # Encoding settings
    encode_enabled: bool = Field(
        default=True,
        description="Enable encoding after ripping",
    )
    delete_raw_after_encode: bool = Field(
        default=True,
        description="Delete raw MKV files after successful encoding",
    )
    handbrake_path: str = Field(
        default="HandBrakeCLI",
        description="Path to HandBrakeCLI binary",
    )
    handbrake_preset: str = Field(
        default="HQ 576p25 Surround",
        description="HandBrake preset to use",
    )
    video_codec: Literal["x264", "x265", "nvenc_h264", "nvenc_h265"] = Field(
        default="x265",
        description="Video encoder to use",
    )
    video_quality: int = Field(
        default=19,
        ge=0,
        le=51,
        description="Video quality (CRF/CQ value, lower is better)",
    )
    encoder_preset: str = Field(
        default="slow",
        description="Encoder speed preset (ultrafast to placebo)",
    )
    deinterlace: bool = Field(
        default=True,
        description="Enable comb detection and decomb filter",
    )
    subtitle_scan: bool = Field(
        default=True,
        description="Scan for forced subtitles and burn in",
    )

    # Metadata
    tmdb_api_key: str | None = Field(
        default=None,
        description="TMDB API key for metadata lookups",
    )
    arm_api_url: str = Field(
        default="https://1337server.pythonanywhere.com",
        description="ARM metadata API base URL",
    )

    # Detection
    detection_method: Literal["auto", "udev", "polling"] = Field(
        default="auto",
        description="Disc detection method (auto, udev, polling)",
    )
    poll_interval: float = Field(
        default=5.0,
        ge=1.0,
        description="Polling interval in seconds when using polling detection",
    )

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Logging level",
    )
    log_json: bool = Field(
        default=False,
        description="Output logs in JSON format",
    )

    # Queue settings
    max_concurrent_rips: int = Field(
        default=1,
        ge=1,
        description="Maximum concurrent rip operations",
    )
    max_concurrent_encodes: int = Field(
        default=2,
        ge=1,
        description="Maximum concurrent encode operations",
    )

    @field_validator("raw_dir", "output_dir", "temp_dir", mode="before")
    @classmethod
    def expand_path(cls, v: str | Path) -> Path:
        """Expand user home directory in paths."""
        return Path(v).expanduser()


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
