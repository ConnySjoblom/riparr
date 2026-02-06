"""ARM (Automatic Ripping Machine) API client for disc metadata lookup.

The ARM database provides metadata for DVDs and Blu-rays based on their CRC64 hash.
"""

import httpx
import structlog

from riparr.config import get_settings
from riparr.core.disc import DiscMetadata, MediaType

log = structlog.get_logger()


class ARMApiError(Exception):
    """ARM API error."""

    pass


async def lookup_disc(dvd_id: str) -> DiscMetadata | None:
    """Look up disc metadata from ARM database.

    Args:
        dvd_id: CRC64 hash of the disc

    Returns:
        DiscMetadata if found, None otherwise
    """
    settings = get_settings()
    url = f"{settings.arm_api_url}/api/v1/search/{dvd_id}"

    log.debug("Looking up disc in ARM database", dvd_id=dvd_id, url=url)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)

            if response.status_code == 404:
                log.debug("Disc not found in ARM database", dvd_id=dvd_id)
                return None

            response.raise_for_status()
            data = response.json()

            if not data or "results" not in data:
                return None

            results = data.get("results", [])
            if not results:
                return None

            # Use first result
            result = results[0]

            # Parse media type
            media_type_str = result.get("video_type", "").lower()
            if media_type_str == "movie":
                media_type = MediaType.MOVIE
            elif media_type_str in ("tv", "series", "episode"):
                media_type = MediaType.TV
            else:
                media_type = MediaType.UNKNOWN

            metadata = DiscMetadata(
                title=result.get("title", "Unknown"),
                year=result.get("year"),
                media_type=media_type,
                imdb_id=result.get("imdb_id"),
                tmdb_id=result.get("tmdb_id"),
                poster_url=result.get("poster_url"),
            )

            log.info(
                "Found disc metadata",
                title=metadata.title,
                year=metadata.year,
                media_type=metadata.media_type.value,
            )

            return metadata

    except httpx.HTTPStatusError as e:
        log.warning("ARM API request failed", status=e.response.status_code, error=str(e))
        return None
    except httpx.RequestError as e:
        log.warning("ARM API connection error", error=str(e))
        return None
    except Exception as e:
        log.error("ARM API error", error=str(e))
        return None


async def submit_disc(
    dvd_id: str,
    title: str,
    year: int | None = None,
    media_type: str = "movie",
    imdb_id: str | None = None,
) -> bool:
    """Submit disc metadata to ARM database.

    Args:
        dvd_id: CRC64 hash of the disc
        title: Title of the disc
        year: Release year
        media_type: Type (movie, tv)
        imdb_id: IMDB ID if known

    Returns:
        True if submission was successful
    """
    settings = get_settings()
    url = f"{settings.arm_api_url}/api/v1/submit"

    data: dict[str, str | int] = {
        "crc64": dvd_id,
        "title": title,
        "video_type": media_type,
    }

    if year:
        data["year"] = year
    if imdb_id:
        data["imdb_id"] = imdb_id

    log.debug("Submitting disc to ARM database", dvd_id=dvd_id, title=title)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=data)
            response.raise_for_status()

            log.info("Disc submitted to ARM database", dvd_id=dvd_id, title=title)
            return True

    except httpx.HTTPError as e:
        log.warning("Failed to submit disc to ARM", error=str(e))
        return False
