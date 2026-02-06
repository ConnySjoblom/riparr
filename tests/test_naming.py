"""Tests for output naming."""

from pathlib import Path

import pytest

from riparr.config.settings import Settings
from riparr.core.disc import Disc, DiscMetadata, MediaType
from riparr.output.naming import OutputNamer, generate_codec_string


@pytest.fixture
def settings() -> Settings:
    """Create test settings."""
    return Settings(output_dir=Path("/output"))


@pytest.fixture
def namer(settings: Settings) -> OutputNamer:
    """Create output namer."""
    return OutputNamer(settings)


class TestMovieNaming:
    """Test movie output path generation."""

    def test_movie_with_full_metadata(self, namer: OutputNamer) -> None:
        """Movie with complete metadata."""
        disc = Disc(
            name="THE_MATRIX",
            metadata=DiscMetadata(
                title="The Matrix",
                year=1999,
                media_type=MediaType.MOVIE,
                imdb_id="tt0133093",
            ),
        )
        source = Path("/raw/title00.mkv")

        path = namer.get_output_path(disc, source)

        assert path == Path(
            "/output/Movies/The Matrix (1999) {imdb-tt0133093}/The Matrix (1999).mkv"
        )

    def test_movie_without_imdb(self, namer: OutputNamer) -> None:
        """Movie without IMDB ID."""
        disc = Disc(
            name="THE_MATRIX",
            metadata=DiscMetadata(
                title="The Matrix",
                year=1999,
                media_type=MediaType.MOVIE,
            ),
        )
        source = Path("/raw/title00.mkv")

        path = namer.get_output_path(disc, source)

        assert path == Path("/output/Movies/The Matrix (1999)/The Matrix (1999).mkv")

    def test_movie_with_codec_string(self, namer: OutputNamer) -> None:
        """Movie with codec string in filename."""
        disc = Disc(
            name="THE_MATRIX",
            metadata=DiscMetadata(
                title="The Matrix",
                year=1999,
                media_type=MediaType.MOVIE,
            ),
        )
        source = Path("/raw/title00.mkv")

        path = namer.get_output_path(disc, source, codec_string="H265 DTS-HD")

        assert "[H265 DTS-HD]" in str(path)


class TestTVNaming:
    """Test TV show output path generation."""

    def test_tv_episode_path(self, namer: OutputNamer) -> None:
        """TV episode with metadata."""
        disc = Disc(
            name="BREAKING_BAD_S01",
            metadata=DiscMetadata(
                title="Breaking Bad",
                year=2008,
                media_type=MediaType.TV,
                imdb_id="tt0903747",
                season=1,
            ),
        )
        source = Path("/raw/title01.mkv")

        path = namer.get_output_path(disc, source, title_num=1)

        assert path == Path(
            "/output/TV/Breaking Bad {imdb-tt0903747}/Season 01/Breaking Bad - S01E01.mkv"
        )

    def test_tv_default_season(self, namer: OutputNamer) -> None:
        """TV episode defaults to season 1."""
        disc = Disc(
            name="TV_SHOW",
            metadata=DiscMetadata(
                title="TV Show",
                year=2020,
                media_type=MediaType.TV,
            ),
        )
        source = Path("/raw/title00.mkv")

        path = namer.get_output_path(disc, source)

        assert "Season 01" in str(path)


class TestNoMetadata:
    """Test output naming without metadata."""

    def test_no_metadata_uses_disc_name(self, namer: OutputNamer) -> None:
        """Without metadata, use disc name."""
        disc = Disc(name="UNKNOWN_DISC_2020")
        source = Path("/raw/title00.mkv")

        path = namer.get_output_path(disc, source)

        assert "Unknown" in str(path)
        assert "title00.mkv" in str(path)


class TestFilenameSanitization:
    """Test filename sanitization."""

    def test_removes_invalid_characters(self, namer: OutputNamer) -> None:
        """Invalid characters should be removed."""
        disc = Disc(
            name="test",
            metadata=DiscMetadata(
                title="Movie: The Sequel?",
                year=2020,
                media_type=MediaType.MOVIE,
            ),
        )
        source = Path("/raw/title00.mkv")

        path = namer.get_output_path(disc, source)

        # Should not contain : or ?
        assert ":" not in str(path.name)
        assert "?" not in str(path.name)


class TestCodecString:
    """Test codec string generation."""

    def test_h265_with_dts(self) -> None:
        """H.265 with DTS-HD audio."""
        result = generate_codec_string("H.265", "DTS-HD MA")
        assert result == "H265 DTS-HD"

    def test_h264_with_truehd_atmos(self) -> None:
        """H.264 with TrueHD Atmos."""
        result = generate_codec_string("H.264", "TrueHD Atmos")
        assert result == "H264 Atmos"

    def test_hdr_included(self) -> None:
        """HDR should be included."""
        result = generate_codec_string("HEVC", "DTS", hdr=True)
        assert "HDR" in result
