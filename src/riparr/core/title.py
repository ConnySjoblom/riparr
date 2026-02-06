"""Title-related utilities and extended models."""

from riparr.core.disc import Title


def parse_duration(duration_str: str) -> int:
    """Parse duration string (HH:MM:SS) to seconds.

    Args:
        duration_str: Duration in HH:MM:SS or MM:SS format

    Returns:
        Duration in seconds
    """
    parts = duration_str.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = map(int, parts)
        return hours * 3600 + minutes * 60 + seconds
    elif len(parts) == 2:
        minutes, seconds = map(int, parts)
        return minutes * 60 + seconds
    else:
        return int(parts[0])


def format_duration(seconds: int) -> str:
    """Format seconds as HH:MM:SS string."""
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def is_likely_main_feature(title: Title, all_titles: list[Title]) -> bool:
    """Determine if a title is likely the main feature.

    Heuristics:
    - Longest title on disc
    - Has multiple audio tracks
    - Has multiple chapters

    Args:
        title: The title to check
        all_titles: All titles on the disc

    Returns:
        True if likely main feature
    """
    if not all_titles:
        return False

    max_duration = max(t.duration for t in all_titles)

    # Main feature is usually the longest
    if title.duration == max_duration:
        return True

    # Or within 10% of longest with good metadata
    if title.duration >= max_duration * 0.9:
        if len(title.audio_tracks) > 1 or title.chapters >= 10:
            return True

    return False


def is_likely_play_all(title: Title, other_titles: list[Title]) -> bool:
    """Detect if title is a 'play all' concatenation of other titles.

    TV discs often have a "play all" title that's the sum of episodes.

    Args:
        title: The title to check
        other_titles: Other titles to compare against

    Returns:
        True if likely a play-all concatenation
    """
    if not other_titles:
        return False

    # Get titles shorter than this one
    shorter = [t for t in other_titles if t.duration < title.duration and t.index != title.index]

    if len(shorter) < 2:
        return False

    # Check if this title's duration matches sum of shorter titles
    # Allow 5% tolerance for chapter transitions
    sum_duration = sum(t.duration for t in shorter)
    tolerance = sum_duration * 0.05

    if abs(title.duration - sum_duration) <= tolerance:
        return True

    # Also check if segments indicate concatenation
    if title.segment_count > 1 and title.segment_count == len(shorter):
        return True

    return False


def group_by_duration(titles: list[Title], tolerance_seconds: int = 120) -> list[list[Title]]:
    """Group titles by similar duration.

    Useful for identifying TV episodes of similar length.

    Args:
        titles: List of titles to group
        tolerance_seconds: Maximum duration difference within a group

    Returns:
        List of title groups
    """
    if not titles:
        return []

    sorted_titles = sorted(titles, key=lambda t: t.duration)
    groups: list[list[Title]] = []
    current_group: list[Title] = [sorted_titles[0]]

    for title in sorted_titles[1:]:
        if title.duration - current_group[0].duration <= tolerance_seconds:
            current_group.append(title)
        else:
            groups.append(current_group)
            current_group = [title]

    groups.append(current_group)
    return groups
