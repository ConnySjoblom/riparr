"""Media file analysis using pymediainfo."""

from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger()


@dataclass
class VideoInfo:
    """Video stream information."""

    codec: str = ""
    width: int = 0
    height: int = 0
    frame_rate: float = 0.0
    bit_depth: int = 8
    hdr_format: str | None = None
    duration_ms: int = 0


@dataclass
class AudioInfo:
    """Audio stream information."""

    codec: str = ""
    channels: int = 2
    sample_rate: int = 48000
    language: str = "und"
    title: str = ""


@dataclass
class SubtitleInfo:
    """Subtitle stream information."""

    codec: str = ""
    language: str = "und"
    forced: bool = False
    title: str = ""


@dataclass
class MediaInfo:
    """Complete media file information."""

    duration_ms: int = 0
    size_bytes: int = 0
    container: str = ""
    video: VideoInfo | None = None
    audio_tracks: list[AudioInfo] | None = None
    subtitle_tracks: list[SubtitleInfo] | None = None

    def __post_init__(self) -> None:
        if self.audio_tracks is None:
            self.audio_tracks = []
        if self.subtitle_tracks is None:
            self.subtitle_tracks = []

    @property
    def duration_str(self) -> str:
        """Duration as HH:MM:SS string."""
        seconds = self.duration_ms // 1000
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    @property
    def resolution(self) -> str:
        """Resolution string (e.g., 1920x1080)."""
        if self.video:
            return f"{self.video.width}x{self.video.height}"
        return "Unknown"

    @property
    def is_hdr(self) -> bool:
        """Check if video has HDR."""
        return self.video is not None and self.video.hdr_format is not None

    @property
    def codec_string(self) -> str:
        """Generate codec string for filename (e.g., H265 DTS-HD)."""
        parts = []

        if self.video:
            video_codec = self.video.codec.upper()
            if "HEVC" in video_codec or "H.265" in video_codec:
                parts.append("H265")
            elif "AVC" in video_codec or "H.264" in video_codec:
                parts.append("H264")
            else:
                parts.append(video_codec.split()[0])

            if self.video.hdr_format:
                if "Dolby Vision" in self.video.hdr_format:
                    parts.append("DV")
                elif "HDR10+" in self.video.hdr_format:
                    parts.append("HDR10+")
                elif "HDR" in self.video.hdr_format:
                    parts.append("HDR")

        if self.audio_tracks:
            audio = self.audio_tracks[0]
            audio_codec = audio.codec.upper()
            if "TRUEHD" in audio_codec or "TrueHD" in audio.codec:
                if "Atmos" in audio.title:
                    parts.append("Atmos")
                else:
                    parts.append("TrueHD")
            elif "DTS-HD" in audio_codec or "DTS:X" in audio_codec:
                parts.append("DTS-HD")
            elif "DTS" in audio_codec:
                parts.append("DTS")
            elif "AC3" in audio_codec or "AC-3" in audio_codec:
                parts.append("DD")
            elif "AAC" in audio_codec:
                parts.append("AAC")

        return " ".join(parts) if parts else "Unknown"


def get_media_info(file_path: Path) -> MediaInfo | None:
    """Analyze a media file using pymediainfo.

    Args:
        file_path: Path to the media file

    Returns:
        MediaInfo object or None if analysis fails
    """
    try:
        from pymediainfo import MediaInfo as PyMediaInfo

        if not file_path.exists():
            log.warning("File not found", path=str(file_path))
            return None

        mi = PyMediaInfo.parse(str(file_path))

        result = MediaInfo(
            size_bytes=file_path.stat().st_size,
        )

        for track in mi.tracks:
            if track.track_type == "General":
                result.duration_ms = int(track.duration or 0)
                result.container = track.format or ""

            elif track.track_type == "Video":
                result.video = VideoInfo(
                    codec=track.format or "",
                    width=int(track.width or 0),
                    height=int(track.height or 0),
                    frame_rate=float(track.frame_rate or 0),
                    bit_depth=int(track.bit_depth or 8),
                    hdr_format=track.hdr_format or track.hdr_format_commercial,
                    duration_ms=int(track.duration or 0),
                )

            elif track.track_type == "Audio":
                audio = AudioInfo(
                    codec=track.format or "",
                    channels=int(track.channel_s or 2),
                    sample_rate=int(track.sampling_rate or 48000),
                    language=track.language or "und",
                    title=track.title or "",
                )
                result.audio_tracks.append(audio)

            elif track.track_type == "Text":
                subtitle = SubtitleInfo(
                    codec=track.format or "",
                    language=track.language or "und",
                    forced=bool(track.forced),
                    title=track.title or "",
                )
                result.subtitle_tracks.append(subtitle)

        log.debug(
            "Media info parsed",
            path=file_path.name,
            duration=result.duration_str,
            video=result.video.codec if result.video else None,
            audio_tracks=len(result.audio_tracks),
        )

        return result

    except ImportError:
        log.error("pymediainfo not installed")
        return None
    except Exception as e:
        log.error("Failed to parse media info", path=str(file_path), error=str(e))
        return None
