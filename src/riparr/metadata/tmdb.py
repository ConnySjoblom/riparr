"""TMDB (The Movie Database) API client for metadata lookup."""

import httpx
import structlog

from riparr.config import get_settings
from riparr.core.disc import DiscMetadata, MediaType

log = structlog.get_logger()

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"


class TMDBError(Exception):
    """TMDB API error."""

    pass


async def search_movie(title: str, year: int | None = None) -> DiscMetadata | None:
    """Search for a movie on TMDB.

    Args:
        title: Movie title to search
        year: Optional release year for more accurate results

    Returns:
        DiscMetadata if found, None otherwise
    """
    settings = get_settings()

    if not settings.tmdb_api_key:
        log.debug("TMDB API key not configured")
        return None

    params = {
        "api_key": settings.tmdb_api_key,
        "query": title,
        "include_adult": "false",
    }

    if year:
        params["year"] = str(year)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{TMDB_BASE_URL}/search/movie",
                params=params,
            )
            response.raise_for_status()
            data = response.json()

            results = data.get("results", [])
            if not results:
                log.debug("No TMDB results for movie", title=title)
                return None

            # Use first result
            movie = results[0]

            # Get external IDs (IMDB)
            imdb_id = await _get_external_ids("movie", movie["id"])

            metadata = DiscMetadata(
                title=movie.get("title", title),
                year=_parse_year(movie.get("release_date")),
                media_type=MediaType.MOVIE,
                imdb_id=imdb_id,
                tmdb_id=movie.get("id"),
                poster_url=_get_poster_url(movie.get("poster_path")),
                overview=movie.get("overview"),
            )

            log.info("Found movie on TMDB", title=metadata.title, year=metadata.year)
            return metadata

    except httpx.HTTPError as e:
        log.warning("TMDB search failed", error=str(e))
        return None


async def search_tv(title: str, year: int | None = None) -> DiscMetadata | None:
    """Search for a TV series on TMDB.

    Args:
        title: TV series title to search
        year: Optional first air year

    Returns:
        DiscMetadata if found, None otherwise
    """
    settings = get_settings()

    if not settings.tmdb_api_key:
        log.debug("TMDB API key not configured")
        return None

    params = {
        "api_key": settings.tmdb_api_key,
        "query": title,
        "include_adult": "false",
    }

    if year:
        params["first_air_date_year"] = str(year)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{TMDB_BASE_URL}/search/tv",
                params=params,
            )
            response.raise_for_status()
            data = response.json()

            results = data.get("results", [])
            if not results:
                log.debug("No TMDB results for TV series", title=title)
                return None

            # Use first result
            show = results[0]

            # Get external IDs (IMDB)
            imdb_id = await _get_external_ids("tv", show["id"])

            # Get season/episode info
            show_details = await _get_tv_details(show["id"])

            metadata = DiscMetadata(
                title=show.get("name", title),
                year=_parse_year(show.get("first_air_date")),
                media_type=MediaType.TV,
                imdb_id=imdb_id,
                tmdb_id=show.get("id"),
                poster_url=_get_poster_url(show.get("poster_path")),
                overview=show.get("overview"),
                episode_count=show_details.get("number_of_episodes"),
            )

            log.info("Found TV series on TMDB", title=metadata.title, year=metadata.year)
            return metadata

    except httpx.HTTPError as e:
        log.warning("TMDB search failed", error=str(e))
        return None


async def _get_external_ids(media_type: str, tmdb_id: int) -> str | None:
    """Get external IDs (IMDB) for a TMDB entry.

    Args:
        media_type: "movie" or "tv"
        tmdb_id: TMDB ID

    Returns:
        IMDB ID or None
    """
    settings = get_settings()

    if not settings.tmdb_api_key:
        return None

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{TMDB_BASE_URL}/{media_type}/{tmdb_id}/external_ids",
                params={"api_key": settings.tmdb_api_key},
            )
            response.raise_for_status()
            data = response.json()
            return data.get("imdb_id")

    except httpx.HTTPError:
        return None


async def _get_tv_details(tmdb_id: int) -> dict:
    """Get TV series details.

    Args:
        tmdb_id: TMDB ID

    Returns:
        Dict with series details
    """
    settings = get_settings()

    if not settings.tmdb_api_key:
        return {}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{TMDB_BASE_URL}/tv/{tmdb_id}",
                params={"api_key": settings.tmdb_api_key},
            )
            response.raise_for_status()
            return response.json()

    except httpx.HTTPError:
        return {}


def _parse_year(date_str: str | None) -> int | None:
    """Parse year from date string (YYYY-MM-DD)."""
    if date_str and len(date_str) >= 4:
        try:
            return int(date_str[:4])
        except ValueError:
            pass
    return None


def _get_poster_url(poster_path: str | None) -> str | None:
    """Get full poster URL from path."""
    if poster_path:
        return f"{TMDB_IMAGE_BASE}{poster_path}"
    return None


async def search(title: str, year: int | None = None, media_type: str | None = None) -> DiscMetadata | None:
    """Search TMDB for movie or TV series.

    If media_type is not specified, searches movies first, then TV.

    Args:
        title: Title to search
        year: Optional year
        media_type: Optional type (movie, tv)

    Returns:
        DiscMetadata if found, None otherwise
    """
    if media_type == "movie":
        return await search_movie(title, year)
    elif media_type == "tv":
        return await search_tv(title, year)
    else:
        # Try movie first, then TV
        result = await search_movie(title, year)
        if result:
            return result
        return await search_tv(title, year)
