"""Title selection logic for disc ripping."""

from enum import Enum

import structlog

from riparr.config.settings import Settings
from riparr.core.disc import Title
from riparr.core.title import group_by_duration, is_likely_play_all

log = structlog.get_logger()


class DiscClassification(str, Enum):
    """Classification of disc content type."""

    MOVIE = "movie"
    TV_SERIES = "tv_series"
    TV_SEASON = "tv_season"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class TitleSelector:
    """Selects which titles to rip from a disc."""

    def __init__(self, settings: Settings) -> None:
        """Initialize selector with settings.

        Args:
            settings: Application settings
        """
        self.min_movie_duration = settings.min_movie_duration
        self.min_tv_duration = settings.min_tv_duration
        self.max_titles = settings.max_titles

    def classify_disc(self, titles: list[Title]) -> DiscClassification:
        """Classify disc as movie or TV content.

        Heuristics:
        - Movie: 1-2 titles with duration > 60 min
        - TV: Multiple titles with similar duration (20-60 min each)
        - TV Season: 4+ titles with similar durations

        Args:
            titles: List of titles on disc

        Returns:
            Disc classification
        """
        if not titles:
            return DiscClassification.UNKNOWN

        # Filter to meaningful titles (> 5 min)
        meaningful = [t for t in titles if t.duration >= 300]

        if not meaningful:
            return DiscClassification.UNKNOWN

        # Single long title = movie
        if len(meaningful) == 1 and meaningful[0].duration >= 3600:
            return DiscClassification.MOVIE

        # Count titles by duration ranges
        long_titles = [t for t in meaningful if t.duration >= 3600]  # > 60 min
        medium_titles = [t for t in meaningful if 1200 <= t.duration < 3600]  # 20-60 min
        short_titles = [t for t in meaningful if 300 <= t.duration < 1200]  # 5-20 min

        # Movie heuristics
        if len(long_titles) >= 1 and len(medium_titles) <= 2:
            # One main feature with maybe some extras
            return DiscClassification.MOVIE

        # TV heuristics
        if len(medium_titles) >= 3:
            # Check if durations are similar (within 5 min of each other)
            groups = group_by_duration(medium_titles, tolerance_seconds=300)
            largest_group = max(groups, key=len)

            if len(largest_group) >= 4:
                return DiscClassification.TV_SEASON
            elif len(largest_group) >= 2:
                return DiscClassification.TV_SERIES

        # Short titles might be TV episodes too
        if len(short_titles) >= 4:
            groups = group_by_duration(short_titles, tolerance_seconds=180)
            largest_group = max(groups, key=len)
            if len(largest_group) >= 4:
                return DiscClassification.TV_SERIES

        # Mixed content
        if long_titles and medium_titles:
            return DiscClassification.MIXED

        return DiscClassification.UNKNOWN

    def select_titles(
        self,
        titles: list[Title],
        min_duration: int | None = None,
    ) -> list[Title]:
        """Select titles to rip based on classification and duration.

        Args:
            titles: All titles on disc
            min_duration: Override minimum duration in seconds

        Returns:
            List of titles to rip
        """
        if not titles:
            return []

        classification = self.classify_disc(titles)
        log.info("Disc classified", classification=classification.value, total_titles=len(titles))

        # Determine minimum duration based on classification
        if min_duration is not None:
            min_dur = min_duration
        elif classification in (DiscClassification.TV_SERIES, DiscClassification.TV_SEASON):
            min_dur = self.min_tv_duration
        else:
            min_dur = self.min_movie_duration

        # Filter by duration
        candidates = [t for t in titles if t.duration >= min_dur]

        if not candidates:
            log.warning("No titles meet minimum duration", min_duration=min_dur)
            return []

        # Remove play-all titles for TV content
        if classification in (DiscClassification.TV_SERIES, DiscClassification.TV_SEASON):
            candidates = self._filter_play_all(candidates)

        # Remove duplicate titles (same duration and size)
        candidates = self._filter_duplicates(candidates)

        # Apply maximum title limit
        if len(candidates) > self.max_titles:
            log.warning(
                "Too many titles, limiting selection",
                total=len(candidates),
                max=self.max_titles,
            )
            # For movies, keep longest; for TV, keep in order
            if classification == DiscClassification.MOVIE:
                candidates.sort(key=lambda t: t.duration, reverse=True)
            candidates = candidates[: self.max_titles]

        # Sort by title index for consistent output
        candidates.sort(key=lambda t: t.index)

        log.info(
            "Selected titles",
            count=len(candidates),
            indices=[t.index for t in candidates],
        )

        return candidates

    def _filter_play_all(self, titles: list[Title]) -> list[Title]:
        """Filter out play-all concatenated titles.

        Args:
            titles: Candidate titles

        Returns:
            Titles without play-all entries
        """
        result = []

        for title in titles:
            others = [t for t in titles if t.index != title.index]
            if not is_likely_play_all(title, others):
                result.append(title)
            else:
                log.debug("Filtered play-all title", index=title.index, duration=title.duration)

        return result

    def _filter_duplicates(self, titles: list[Title]) -> list[Title]:
        """Filter out duplicate titles with same duration and size.

        Some discs have multiple identical titles (different angles, etc.).

        Args:
            titles: Candidate titles

        Returns:
            Unique titles
        """
        seen: set[tuple[int, int]] = set()
        result = []

        for title in titles:
            key = (title.duration, title.size_bytes)
            if key not in seen:
                seen.add(key)
                result.append(title)
            else:
                log.debug(
                    "Filtered duplicate title",
                    index=title.index,
                    duration=title.duration,
                )

        return result

    def get_main_feature(self, titles: list[Title]) -> Title | None:
        """Get the main feature title from a movie disc.

        Args:
            titles: All titles on disc

        Returns:
            Main feature title or None
        """
        if not titles:
            return None

        # Filter to meaningful titles
        meaningful = [t for t in titles if t.duration >= self.min_movie_duration]

        if not meaningful:
            return None

        # Main feature is typically longest with multiple audio tracks
        candidates = sorted(meaningful, key=lambda t: t.duration, reverse=True)

        for candidate in candidates:
            # Prefer titles with multiple audio tracks (indicates main content)
            if len(candidate.audio_tracks) > 1:
                return candidate

        # Fallback to longest title
        return candidates[0]

    def get_episodes(self, titles: list[Title]) -> list[Title]:
        """Get episode titles from a TV disc.

        Args:
            titles: All titles on disc

        Returns:
            Episode titles sorted by index
        """
        classification = self.classify_disc(titles)

        if classification not in (DiscClassification.TV_SERIES, DiscClassification.TV_SEASON):
            return []

        # Filter to episode-length titles
        episodes = [t for t in titles if self.min_tv_duration <= t.duration < 3600]

        # Remove play-all
        episodes = self._filter_play_all(episodes)

        # Remove duplicates
        episodes = self._filter_duplicates(episodes)

        # Sort by index
        episodes.sort(key=lambda t: t.index)

        return episodes
