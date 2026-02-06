"""Tests for title selection logic."""

import pytest

from riparr.config.settings import Settings
from riparr.core.disc import Title
from riparr.ripper.selector import DiscClassification, TitleSelector


@pytest.fixture
def settings() -> Settings:
    """Create test settings."""
    return Settings(
        min_movie_duration=600,  # 10 min
        min_tv_duration=300,  # 5 min
        max_titles=50,
    )


@pytest.fixture
def selector(settings: Settings) -> TitleSelector:
    """Create title selector."""
    return TitleSelector(settings)


class TestDiscClassification:
    """Test disc classification."""

    def test_classify_movie_single_long_title(self, selector: TitleSelector) -> None:
        """Single long title should be classified as movie."""
        titles = [
            Title(index=0, duration=7200),  # 2 hours
        ]
        assert selector.classify_disc(titles) == DiscClassification.MOVIE

    def test_classify_movie_with_extras(self, selector: TitleSelector) -> None:
        """Long title with short extras should be movie."""
        titles = [
            Title(index=0, duration=7200),  # 2 hours (main feature)
            Title(index=1, duration=600),  # 10 min extra
            Title(index=2, duration=300),  # 5 min extra
        ]
        assert selector.classify_disc(titles) == DiscClassification.MOVIE

    def test_classify_tv_season(self, selector: TitleSelector) -> None:
        """Multiple similar-duration titles should be TV season."""
        # 6 episodes of ~42 minutes each
        titles = [
            Title(index=i, duration=2520 + (i * 30))
            for i in range(6)
        ]
        assert selector.classify_disc(titles) == DiscClassification.TV_SEASON

    def test_classify_tv_series(self, selector: TitleSelector) -> None:
        """2-3 similar episodes should be TV series."""
        titles = [
            Title(index=0, duration=2500),
            Title(index=1, duration=2600),
        ]
        assert selector.classify_disc(titles) == DiscClassification.TV_SERIES

    def test_classify_empty(self, selector: TitleSelector) -> None:
        """Empty title list should be unknown."""
        assert selector.classify_disc([]) == DiscClassification.UNKNOWN


class TestTitleSelection:
    """Test title selection."""

    def test_select_movie_titles(self, selector: TitleSelector) -> None:
        """Should select main feature for movie."""
        titles = [
            Title(index=0, duration=7200),  # Main feature
            Title(index=1, duration=300),  # Too short
        ]
        selected = selector.select_titles(titles)

        assert len(selected) == 1
        assert selected[0].index == 0

    def test_filter_short_titles(self, selector: TitleSelector) -> None:
        """Titles below minimum duration should be filtered."""
        titles = [
            Title(index=0, duration=7200),
            Title(index=1, duration=100),  # Too short
            Title(index=2, duration=200),  # Too short
        ]
        selected = selector.select_titles(titles)

        assert len(selected) == 1
        assert all(t.duration >= 600 for t in selected)

    def test_filter_duplicates(self, selector: TitleSelector) -> None:
        """Duplicate titles (same duration/size) should be filtered."""
        titles = [
            Title(index=0, duration=7200, size_bytes=1000000),
            Title(index=1, duration=7200, size_bytes=1000000),  # Duplicate
            Title(index=2, duration=7200, size_bytes=1000000),  # Duplicate
        ]
        selected = selector.select_titles(titles)

        assert len(selected) == 1

    def test_custom_min_duration(self, selector: TitleSelector) -> None:
        """Custom minimum duration should override default."""
        titles = [
            Title(index=0, duration=7200),
            Title(index=1, duration=400),
            Title(index=2, duration=200),
        ]
        selected = selector.select_titles(titles, min_duration=300)

        assert len(selected) == 2


class TestPlayAllDetection:
    """Test play-all title detection."""

    def test_detect_play_all(self, selector: TitleSelector) -> None:
        """Play-all titles should be filtered from TV discs."""
        titles = [
            # 4 episodes
            Title(index=0, duration=2520),
            Title(index=1, duration=2520),
            Title(index=2, duration=2520),
            Title(index=3, duration=2520),
            # Play all (sum of episodes)
            Title(index=4, duration=10080),
        ]
        selected = selector.select_titles(titles)

        # Should not include the play-all title
        assert len(selected) == 4
        assert all(t.index != 4 for t in selected)


class TestMainFeatureDetection:
    """Test main feature detection."""

    def test_get_main_feature(self, selector: TitleSelector) -> None:
        """Should return longest title with multiple audio tracks."""
        titles = [
            Title(index=0, duration=7200, audio_tracks=[]),
            Title(index=1, duration=7000, audio_tracks=[
                type("AudioTrack", (), {"index": 0, "codec": "DTS"})(),
                type("AudioTrack", (), {"index": 1, "codec": "AC3"})(),
            ]),
        ]
        main = selector.get_main_feature(titles)

        # Should prefer title with audio tracks even if slightly shorter
        assert main is not None
        assert main.index == 1

    def test_get_main_feature_fallback(self, selector: TitleSelector) -> None:
        """Should fall back to longest if no audio track info."""
        titles = [
            Title(index=0, duration=7200),
            Title(index=1, duration=3600),
        ]
        main = selector.get_main_feature(titles)

        assert main is not None
        assert main.index == 0
