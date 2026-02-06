"""MakeMKV robot mode output parser.

MakeMKV's robot mode (-r) outputs structured data:
- MSG:code,flags,count,message,format,param1,param2,...
- PRGT:code,id,name
- PRGC:code,id,name
- PRGV:current,total,max
- CINFO:id,code,value (disc info)
- TINFO:id,code,value (title info)
- SINFO:id,code,value (stream/track info)
- DRV:index,visible,enabled,flags,drive_name,disc_name
"""

import re
from dataclasses import dataclass, field

from riparr.core.disc import AudioTrack, Disc, DiscType, SubtitleTrack, Title


# MakeMKV attribute codes
class AttrCode:
    """MakeMKV attribute codes from apdefs.h."""

    # Common
    TYPE = 1
    NAME = 2
    LANG_CODE = 3
    LANG_NAME = 4
    CODEC_ID = 5
    CODEC_SHORT = 6
    CODEC_LONG = 7
    CHAPTER_COUNT = 8
    DURATION = 9
    DISK_SIZE = 10
    DISK_SIZE_BYTES = 11
    SEGMENT_COUNT = 12
    SEGMENT_MAP = 13
    OUTPUT_FILE_NAME = 14
    METADATA_LANG_CODE = 15
    METADATA_LANG_NAME = 16
    TREE_INFO = 17
    PANEL_TITLE = 18
    VOLUME_NAME = 19
    ORDER_WEIGHT = 20

    # Video specific
    VIDEO_SIZE = 21
    VIDEO_ASPECT_RATIO = 22
    VIDEO_FRAME_RATE = 23
    VIDEO_STREAM_FLAGS = 24

    # Audio specific
    AUDIO_CHANNELS_COUNT = 25
    AUDIO_SAMPLE_RATE = 26
    AUDIO_SAMPLE_SIZE = 27
    AUDIO_STREAM_FLAGS = 28

    # Subtitle specific
    STREAM_FLAGS = 30

    # File info
    SOURCE_FILE_NAME = 33


@dataclass
class ParseState:
    """State maintained during parsing."""

    disc: Disc = field(default_factory=Disc)
    current_title_idx: int = -1
    current_stream_idx: int = -1
    titles: dict[int, Title] = field(default_factory=dict)
    audio_tracks: dict[tuple[int, int], AudioTrack] = field(default_factory=dict)
    subtitle_tracks: dict[tuple[int, int], SubtitleTrack] = field(default_factory=dict)
    progress_current: int = 0
    progress_total: int = 0
    progress_max: int = 0


def parse_line(line: str, state: ParseState) -> None:
    """Parse a single line of MakeMKV output.

    Args:
        line: Line to parse
        state: Current parse state to update
    """
    line = line.strip()
    if not line:
        return

    # Parse message type and content
    if ":" not in line:
        return

    msg_type, content = line.split(":", 1)

    if msg_type == "MSG":
        _parse_msg(content, state)
    elif msg_type == "PRGV":
        _parse_progress(content, state)
    elif msg_type == "CINFO":
        _parse_disc_info(content, state)
    elif msg_type == "TINFO":
        _parse_title_info(content, state)
    elif msg_type == "SINFO":
        _parse_stream_info(content, state)
    elif msg_type == "DRV":
        _parse_drive_info(content, state)


def _parse_csv(content: str) -> list[str]:
    """Parse CSV content, handling quoted strings."""
    result = []
    current = ""
    in_quotes = False

    for char in content:
        if char == '"':
            in_quotes = not in_quotes
        elif char == "," and not in_quotes:
            result.append(current.strip('"'))
            current = ""
        else:
            current += char

    result.append(current.strip('"'))
    return result


def _parse_msg(content: str, state: ParseState) -> None:
    """Parse MSG line."""
    # MSG lines contain status messages, we mainly use them for error detection
    pass


def _parse_progress(content: str, state: ParseState) -> None:
    """Parse PRGV (progress) line."""
    parts = content.split(",")
    if len(parts) >= 3:
        state.progress_current = int(parts[0])
        state.progress_total = int(parts[1])
        state.progress_max = int(parts[2])


def _parse_disc_info(content: str, state: ParseState) -> None:
    """Parse CINFO (disc info) line."""
    parts = _parse_csv(content)
    if len(parts) < 3:
        return

    attr_id = int(parts[0])
    # code = int(parts[1])  # Not used currently
    value = parts[2] if len(parts) > 2 else ""

    if attr_id == AttrCode.NAME:
        state.disc.name = value
    elif attr_id == AttrCode.TYPE:
        state.disc.disc_type = _parse_disc_type(value)
    elif attr_id == AttrCode.VOLUME_NAME:
        state.disc.label = value


def _parse_title_info(content: str, state: ParseState) -> None:
    """Parse TINFO (title info) line."""
    parts = _parse_csv(content)
    if len(parts) < 4:
        return

    title_idx = int(parts[0])
    attr_id = int(parts[1])
    # code = int(parts[2])  # Not used currently
    value = parts[3] if len(parts) > 3 else ""

    # Ensure title exists
    if title_idx not in state.titles:
        state.titles[title_idx] = Title(index=title_idx)

    title = state.titles[title_idx]

    if attr_id == AttrCode.DURATION:
        title.duration = _parse_duration(value)
    elif attr_id == AttrCode.CHAPTER_COUNT:
        title.chapters = int(value) if value else 0
    elif attr_id == AttrCode.DISK_SIZE_BYTES:
        title.size_bytes = int(value) if value else 0
    elif attr_id == AttrCode.SEGMENT_COUNT:
        title.segment_count = int(value) if value else 1
    elif attr_id == AttrCode.SEGMENT_MAP:
        title.segment_map = value
    elif attr_id == AttrCode.OUTPUT_FILE_NAME:
        title.output_file = value
    elif attr_id == AttrCode.SOURCE_FILE_NAME:
        title.source_file = value


def _parse_stream_info(content: str, state: ParseState) -> None:
    """Parse SINFO (stream info) line."""
    parts = _parse_csv(content)
    if len(parts) < 5:
        return

    title_idx = int(parts[0])
    stream_idx = int(parts[1])
    attr_id = int(parts[2])
    # code = int(parts[3])  # Not used currently
    value = parts[4] if len(parts) > 4 else ""

    # Determine stream type from first attribute
    key = (title_idx, stream_idx)

    if attr_id == AttrCode.TYPE:
        stream_type = value.lower()
        if "video" in stream_type:
            # Video stream - update title
            pass
        elif "audio" in stream_type:
            if key not in state.audio_tracks:
                state.audio_tracks[key] = AudioTrack(index=stream_idx)
        elif "subtitle" in stream_type:
            if key not in state.subtitle_tracks:
                state.subtitle_tracks[key] = SubtitleTrack(index=stream_idx)

    elif attr_id == AttrCode.CODEC_SHORT:
        if key in state.audio_tracks:
            state.audio_tracks[key].codec = value
        elif key in state.subtitle_tracks:
            state.subtitle_tracks[key].codec = value
        elif title_idx in state.titles:
            state.titles[title_idx].video_codec = value

    elif attr_id == AttrCode.CODEC_LONG:
        if title_idx in state.titles and key not in state.audio_tracks:
            # Video codec details
            title = state.titles[title_idx]
            if not title.video_codec:
                title.video_codec = value

    elif attr_id == AttrCode.LANG_CODE:
        if key in state.audio_tracks:
            state.audio_tracks[key].language = value
        elif key in state.subtitle_tracks:
            state.subtitle_tracks[key].language = value

    elif attr_id == AttrCode.NAME:
        if key in state.audio_tracks:
            state.audio_tracks[key].name = value
        elif key in state.subtitle_tracks:
            state.subtitle_tracks[key].name = value

    elif attr_id == AttrCode.AUDIO_CHANNELS_COUNT:
        if key in state.audio_tracks:
            state.audio_tracks[key].channels = int(value) if value else 2

    elif attr_id == AttrCode.VIDEO_SIZE:
        if title_idx in state.titles:
            state.titles[title_idx].resolution = value

    elif attr_id == AttrCode.VIDEO_FRAME_RATE:
        if title_idx in state.titles:
            # Parse frame rate like "23.976 (24000/1001)"
            match = re.match(r"([\d.]+)", value)
            if match:
                state.titles[title_idx].frame_rate = float(match.group(1))


def _parse_drive_info(content: str, state: ParseState) -> None:
    """Parse DRV (drive info) line."""
    parts = _parse_csv(content)
    if len(parts) >= 6:
        # disc_name is in position 5
        disc_name = parts[5] if parts[5] else ""
        if disc_name and not state.disc.name:
            state.disc.name = disc_name


def _parse_disc_type(value: str) -> DiscType:
    """Parse disc type from string."""
    value = value.lower()
    if "dvd" in value:
        return DiscType.DVD
    elif "blu-ray" in value or "bluray" in value:
        return DiscType.BLURAY
    elif "uhd" in value or "4k" in value:
        return DiscType.UHD
    return DiscType.UNKNOWN


def _parse_duration(duration_str: str) -> int:
    """Parse duration string to seconds."""
    # Format: H:MM:SS
    parts = duration_str.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    elif len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + int(seconds)
    return 0


def finalize_state(state: ParseState) -> Disc:
    """Finalize parse state into a Disc object.

    Associates audio/subtitle tracks with their titles.
    """
    # Sort titles by index
    for title_idx in sorted(state.titles.keys()):
        title = state.titles[title_idx]

        # Attach audio tracks
        for (t_idx, s_idx), track in state.audio_tracks.items():
            if t_idx == title_idx:
                title.audio_tracks.append(track)

        # Sort audio tracks by index
        title.audio_tracks.sort(key=lambda t: t.index)

        # Attach subtitle tracks
        for (t_idx, s_idx), track in state.subtitle_tracks.items():
            if t_idx == title_idx:
                title.subtitle_tracks.append(track)

        # Sort subtitle tracks by index
        title.subtitle_tracks.sort(key=lambda t: t.index)

        state.disc.titles.append(title)

    return state.disc
