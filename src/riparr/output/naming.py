"""Output path generation for movies and TV shows.

Generates Plex/Jellyfin compatible directory structures:
- Movies: Title (Year) {imdb-ttXXXX}/Title (Year) [codecs].mkv
- TV: Series {imdb-ttXXXX}/Season XX/Series - SXXEXX - Episode [codecs].mkv
"""

import re
from pathlib import Path

import structlog

from riparr.config.settings import Settings
from riparr.core.disc import Disc, MediaType

log = structlog.get_logger()


class OutputNamer:
    """Generate output file paths for encoded media."""

    def __init__(self, settings: Settings) -> None:
        """Initialize output namer.

        Args:
            settings: Application settings
        """
        self.output_dir = settings.output_dir

    def get_output_path(
        self,
        disc: Disc,
        source_file: Path,
        title_num: int | None = None,
        codec_string: str | None = None,
    ) -> Path:
        """Generate output path for an encoded file.

        Args:
            disc: Disc object with metadata
            source_file: Source MKV file path
            title_num: Optional title number for TV episodes
            codec_string: Optional codec string (e.g., "H265 DTS-HD")

        Returns:
            Output file path
        """
        metadata = disc.metadata

        if metadata is None:
            # No metadata, use disc name
            return self._path_from_disc_name(disc, source_file, codec_string)

        if metadata.media_type == MediaType.TV:
            return self._tv_path(disc, source_file, title_num, codec_string)
        else:
            return self._movie_path(disc, source_file, codec_string)

    def _movie_path(
        self,
        disc: Disc,
        source_file: Path,
        codec_string: str | None = None,
    ) -> Path:
        """Generate movie output path.

        Format: Movies/Title (Year) {imdb-ttXXXX}/Title (Year) [codecs].mkv

        Args:
            disc: Disc with metadata
            source_file: Source file path
            codec_string: Codec string for filename

        Returns:
            Movie output path
        """
        metadata = disc.metadata
        assert metadata is not None

        title = self._sanitize_filename(metadata.title)
        year = metadata.year

        # Build folder name
        if year:
            folder_name = f"{title} ({year})"
        else:
            folder_name = title

        if metadata.imdb_id:
            folder_name += f" {{imdb-{metadata.imdb_id}}}"

        # Build filename
        if codec_string:
            filename = f"{title} ({year}) [{codec_string}].mkv" if year else f"{title} [{codec_string}].mkv"
        else:
            filename = f"{title} ({year}).mkv" if year else f"{title}.mkv"

        return self.output_dir / "Movies" / folder_name / filename

    def _tv_path(
        self,
        disc: Disc,
        source_file: Path,
        title_num: int | None = None,
        codec_string: str | None = None,
    ) -> Path:
        """Generate TV show output path.

        Format: TV/Series {imdb-ttXXXX}/Season XX/Series - SXXEXX [codecs].mkv

        Args:
            disc: Disc with metadata
            source_file: Source file path
            title_num: Title number (used to determine episode number)
            codec_string: Codec string for filename

        Returns:
            TV output path
        """
        metadata = disc.metadata
        assert metadata is not None

        title = self._sanitize_filename(metadata.title)

        # Build series folder name
        folder_name = title
        if metadata.imdb_id:
            folder_name += f" {{imdb-{metadata.imdb_id}}}"

        # Determine season (default to 1 if unknown)
        season = metadata.season or 1
        season_folder = f"Season {season:02d}"

        # Determine episode number from title number or filename
        episode_num = self._extract_episode_number(source_file, title_num)

        # Build filename
        episode_code = f"S{season:02d}E{episode_num:02d}"

        if codec_string:
            filename = f"{title} - {episode_code} [{codec_string}].mkv"
        else:
            filename = f"{title} - {episode_code}.mkv"

        return self.output_dir / "TV" / folder_name / season_folder / filename

    def _path_from_disc_name(
        self,
        disc: Disc,
        source_file: Path,
        codec_string: str | None = None,
    ) -> Path:
        """Generate path when no metadata is available.

        Uses disc name/label to create a reasonable path.

        Args:
            disc: Disc object
            source_file: Source file path
            codec_string: Codec string for filename

        Returns:
            Output path
        """
        # Use disc name or label, falling back to source filename
        name = disc.name or disc.label or source_file.stem
        name = self._sanitize_filename(name)

        # Try to parse year from name
        year_match = re.search(r"[\._\s](\d{4})[\._\s]?", name)
        year = year_match.group(1) if year_match else None

        # Clean up the name
        name = re.sub(r"[\._]", " ", name)
        name = re.sub(r"\s+", " ", name).strip()

        if year:
            folder_name = f"{name} ({year})"
        else:
            folder_name = name

        # Use source filename for the actual file
        if codec_string:
            filename = f"{source_file.stem} [{codec_string}].mkv"
        else:
            filename = f"{source_file.stem}.mkv"

        return self.output_dir / "Unknown" / folder_name / filename

    def _sanitize_filename(self, name: str) -> str:
        """Sanitize a string for use as a filename.

        Removes or replaces characters that are invalid on most filesystems.

        Args:
            name: Original filename

        Returns:
            Sanitized filename
        """
        # Replace invalid characters
        invalid_chars = r'[<>:"/\\|?*]'
        name = re.sub(invalid_chars, "", name)

        # Replace multiple spaces with single space
        name = re.sub(r"\s+", " ", name)

        # Strip leading/trailing whitespace and dots
        name = name.strip(" .")

        # Limit length
        if len(name) > 200:
            name = name[:200]

        return name

    def _extract_episode_number(self, source_file: Path, title_num: int | None = None) -> int:
        """Extract episode number from filename or title number.

        Args:
            source_file: Source file path
            title_num: Optional title number from disc

        Returns:
            Episode number (defaults to 1)
        """
        # Try to extract from filename patterns
        patterns = [
            r"[Ee](\d{1,2})",  # E01, e01
            r"[Xx](\d{1,2})",  # x01
            r"_(\d{1,2})(?:_|\.)",  # _01_ or _01.
            r"[Tt](\d{1,2})",  # T01, t01 (title number)
            r"(\d{1,2})(?:of|OF)\d+",  # 01of10
        ]

        filename = source_file.stem

        for pattern in patterns:
            match = re.search(pattern, filename)
            if match:
                return int(match.group(1))

        # Fall back to title number
        if title_num is not None:
            return title_num

        # Default to 1
        return 1


def generate_codec_string(video_codec: str, audio_codec: str, hdr: bool = False) -> str:
    """Generate a codec string for filenames.

    Args:
        video_codec: Video codec (e.g., "H.265", "AVC")
        audio_codec: Audio codec (e.g., "DTS-HD MA", "TrueHD")
        hdr: Whether the video has HDR

    Returns:
        Codec string (e.g., "H265 HDR DTS-HD")
    """
    parts = []

    # Video codec
    if "265" in video_codec or "hevc" in video_codec.lower():
        parts.append("H265")
    elif "264" in video_codec or "avc" in video_codec.lower():
        parts.append("H264")
    else:
        parts.append(video_codec.upper()[:6])

    # HDR
    if hdr:
        parts.append("HDR")

    # Audio codec
    audio_upper = audio_codec.upper()
    if "TRUEHD" in audio_upper:
        if "ATMOS" in audio_upper:
            parts.append("Atmos")
        else:
            parts.append("TrueHD")
    elif "DTS-HD" in audio_upper or "DTS:X" in audio_upper:
        parts.append("DTS-HD")
    elif "DTS" in audio_upper:
        parts.append("DTS")
    elif "AC3" in audio_upper or "AC-3" in audio_upper:
        parts.append("DD")
    elif "AAC" in audio_upper:
        parts.append("AAC")

    return " ".join(parts)
