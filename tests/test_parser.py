"""Tests for MakeMKV output parser."""

import pytest

from riparr.ripper.parser import (
    ParseState,
    finalize_state,
    parse_line,
)


class TestMakeMKVParser:
    """Test MakeMKV robot mode output parsing."""

    def test_parse_disc_info(self) -> None:
        """Test parsing disc information."""
        state = ParseState()

        parse_line('CINFO:2,0,"THE_MATRIX"', state)
        parse_line('CINFO:1,0,"DVD"', state)

        assert state.disc.name == "THE_MATRIX"

    def test_parse_title_info(self) -> None:
        """Test parsing title information."""
        state = ParseState()

        parse_line('TINFO:0,9,0,"2:15:00"', state)
        parse_line('TINFO:0,8,0,"32"', state)
        parse_line('TINFO:0,11,0,"4500000000"', state)

        assert 0 in state.titles
        title = state.titles[0]
        assert title.duration == 8100  # 2:15:00 in seconds
        assert title.chapters == 32
        assert title.size_bytes == 4500000000

    def test_parse_stream_info(self) -> None:
        """Test parsing stream (audio/subtitle) information."""
        state = ParseState()

        # Create title first
        parse_line('TINFO:0,9,0,"1:30:00"', state)

        # Add audio track (attr_id: 1=TYPE, 6=CODEC_SHORT, 3=LANG_CODE, 25=CHANNELS)
        parse_line('SINFO:0,1,1,0,"Audio"', state)
        parse_line('SINFO:0,1,6,0,"DTS-HD MA"', state)
        parse_line('SINFO:0,1,3,0,"eng"', state)
        parse_line('SINFO:0,1,25,0,"6"', state)

        assert (0, 1) in state.audio_tracks
        audio = state.audio_tracks[(0, 1)]
        assert audio.codec == "DTS-HD MA"
        assert audio.language == "eng"
        assert audio.channels == 6

    def test_parse_progress(self) -> None:
        """Test parsing progress information."""
        state = ParseState()

        parse_line("PRGV:500,1000,1000", state)

        assert state.progress_current == 500
        assert state.progress_total == 1000
        assert state.progress_max == 1000

    def test_finalize_state(self) -> None:
        """Test finalizing parse state into Disc object."""
        state = ParseState()

        parse_line('CINFO:2,0,"TEST_DISC"', state)
        parse_line('TINFO:0,9,0,"1:30:00"', state)
        parse_line('TINFO:1,9,0,"0:45:00"', state)

        disc = finalize_state(state)

        assert disc.name == "TEST_DISC"
        assert len(disc.titles) == 2
        assert disc.titles[0].duration == 5400
        assert disc.titles[1].duration == 2700

    def test_parse_empty_line(self) -> None:
        """Empty lines should be ignored."""
        state = ParseState()
        parse_line("", state)
        parse_line("   ", state)
        # Should not raise

    def test_parse_drive_info(self) -> None:
        """Test parsing drive information."""
        state = ParseState()

        parse_line('DRV:0,1,1,0,"/dev/sr0","THE_MATRIX"', state)

        assert state.disc.name == "THE_MATRIX"


class TestDurationParsing:
    """Test duration string parsing."""

    def test_parse_hours_minutes_seconds(self) -> None:
        """Test parsing HH:MM:SS format."""
        state = ParseState()
        parse_line('TINFO:0,9,0,"2:30:45"', state)

        # 2*3600 + 30*60 + 45 = 9045
        assert state.titles[0].duration == 9045

    def test_parse_single_digit_hours(self) -> None:
        """Test parsing with single digit hours."""
        state = ParseState()
        parse_line('TINFO:0,9,0,"1:05:00"', state)

        # 1*3600 + 5*60 = 3900
        assert state.titles[0].duration == 3900


class TestCSVParsing:
    """Test CSV parsing with quoted strings."""

    def test_parse_quoted_string(self) -> None:
        """Test parsing quoted strings with commas."""
        state = ParseState()
        parse_line('CINFO:2,0,"Title, With Comma"', state)

        assert state.disc.name == "Title, With Comma"
