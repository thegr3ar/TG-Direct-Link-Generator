# This file is a part of TG-Direct-Link-Generator
"""Async TMDB client with rate limiting and retry/backoff.

Fetches metadata used by the channel indexer: searches movies/TV by title+year,
pulls full details with credits, and maps TMDB genre IDs to local categories.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

TMDB_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p"
POSTER_SIZE = "w500"
BACKDROP_SIZE = "w1280"

# TMDB genre id -> local category name (spec section 4.5).
GENRE_MAP = {
    28: "Action",
    12: "Adventure",
    16: "Animation",
    35: "Comedy",
    80: "Crime",
    99: "Documentary",
    18: "Drama",
    10751: "Family",
    14: "Fantasy",
    36: "History",
    27: "Horror",
    10402: "Music",
    9648: "Mystery",
    10749: "Romance",
    878: "Science Fiction",
    10770: "TV Movie",
    53: "Thriller",
    10752: "War",
    37: "Western",
    # TV-specific genres mapped onto the closest local category.
    10759: "Action",       # Action & Adventure
    10762: "Family",       # Kids
    10763: "Documentary",  # News
    10764: "Documentary",  # Reality
    10765: "Science Fiction",  # Sci-Fi & Fantasy
    10766: "Drama",        # Soap
    10767: "Documentary",  # Talk
    10768: "War",          # War & Politics
}


@dataclass
class TMDBMetadata:
    tmdb_id: int
    title: str
    year: Optional[int]
    language: str
    poster_url: Optional[str]
    backdrop_url: Optional[str]
    rating: float
    overview: str
    genres: list = field(default_factory=list)
    cast: list = field(default_factory=list)
    director: Optional[str] = None
    is_tv_show: bool = False


class _RateLimiter:
    """Simple async token-bucket limiter (TMDB allows ~50 req/sec)."""

    def __init__(self, rate_per_sec: int):
        self._min_interval = 1.0 / max(rate_per_sec, 1)
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


class TMDBClient:
    def __init__(
        self,
        api_key: str,
        session: Optional[aiohttp.ClientSession] = None,
        rate_per_sec: int = 40,
        max_retries: int = 3,
    ):
        if not api_key:
            raise ValueError("TMDB_API_KEY is not configured")
        self.api_key = api_key
        self._session = session
        self._owns_session = session is None
        self._limiter = _RateLimiter(rate_per_sec)
        self.max_retries = max_retries

    async def __aenter__(self) -> "TMDBClient":
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def _get(self, path: str, params: dict) -> Optional[dict]:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        query = {"api_key": self.api_key, **params}
        url = f"{TMDB_BASE}{path}"

        backoff = 1.0
        for attempt in range(1, self.max_retries + 1):
            await self._limiter.acquire()
            try:
                async with self._session.get(url, params=query, timeout=30) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    if resp.status == 429:
                        retry_after = float(resp.headers.get("Retry-After", backoff))
                        logger.warning("TMDB 429 rate limited, sleeping %.1fs", retry_after)
                        await asyncio.sleep(retry_after)
                        continue
                    if resp.status == 404:
                        return None
                    if 500 <= resp.status < 600:
                        logger.warning("TMDB %s on %s (attempt %s)", resp.status, path, attempt)
                        await asyncio.sleep(backoff)
                        backoff *= 2
                        continue
                    logger.error("TMDB error %s on %s", resp.status, path)
                    return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning("TMDB network error on %s (attempt %s): %s", path, attempt, e)
                await asyncio.sleep(backoff)
                backoff *= 2
        logger.error("TMDB request failed after %s attempts: %s", self.max_retries, path)
        return None

    @staticmethod
    def _image_url(path: Optional[str], size: str) -> Optional[str]:
        if not path:
            return None
        return f"{IMAGE_BASE}/{size}{path}"

    @staticmethod
    def _year_from_date(date_str: Optional[str]) -> Optional[int]:
        if date_str and len(date_str) >= 4 and date_str[:4].isdigit():
            return int(date_str[:4])
        return None

    async def _search(self, media_type: str, title: str, year: Optional[int]) -> Optional[int]:
        params = {"query": title, "include_adult": "false"}
        if year:
            params["year" if media_type == "movie" else "first_air_date_year"] = year
        data = await self._get(f"/search/{media_type}", params)
        results = (data or {}).get("results") or []
        if not results and year:
            # Retry once without the year filter (release dates are often off).
            params.pop("year", None)
            params.pop("first_air_date_year", None)
            data = await self._get(f"/search/{media_type}", params)
            results = (data or {}).get("results") or []
        if not results:
            return None
        return results[0].get("id")

    async def _details(self, media_type: str, tmdb_id: int) -> Optional[dict]:
        return await self._get(
            f"/{media_type}/{tmdb_id}", {"append_to_response": "credits"}
        )

    @staticmethod
    def _build_metadata(media_type: str, d: dict) -> TMDBMetadata:
        is_tv = media_type == "tv"
        title = d.get("title") or d.get("name") or ""
        date = d.get("release_date") or d.get("first_air_date")
        credits = d.get("credits") or {}
        cast = [
            {"name": c.get("name"), "character": c.get("character")}
            for c in (credits.get("cast") or [])[:5]
            if c.get("name")
        ]
        director = None
        for crew in credits.get("crew") or []:
            if crew.get("job") == "Director":
                director = crew.get("name")
                break
        if is_tv and not director:
            creators = d.get("created_by") or []
            if creators:
                director = creators[0].get("name")
        genres = [g.get("name") for g in (d.get("genres") or []) if g.get("name")]

        return TMDBMetadata(
            tmdb_id=d.get("id"),
            title=title,
            year=TMDBClient._year_from_date(date),
            language=d.get("original_language") or "und",
            poster_url=TMDBClient._image_url(d.get("poster_path"), POSTER_SIZE),
            backdrop_url=TMDBClient._image_url(d.get("backdrop_path"), BACKDROP_SIZE),
            rating=float(d.get("vote_average") or 0.0),
            overview=d.get("overview") or "",
            genres=genres,
            cast=cast,
            director=director,
            is_tv_show=is_tv,
        )

    async def fetch(
        self, title: str, year: Optional[int], is_tv_show: bool
    ) -> Optional[TMDBMetadata]:
        """Search + fetch full details for a title. Returns None if not found."""
        media_type = "tv" if is_tv_show else "movie"
        tmdb_id = await self._search(media_type, title, year)
        if tmdb_id is None:
            # Fall back to the other media type before giving up.
            other = "movie" if media_type == "tv" else "tv"
            tmdb_id = await self._search(other, title, year)
            if tmdb_id is None:
                return None
            media_type = other
        details = await self._details(media_type, tmdb_id)
        if not details:
            return None
        return self._build_metadata(media_type, details)


def map_genres_to_categories(genres: list) -> list:
    """Map a list of TMDB genre names (or ids) to local category names."""
    categories = []
    for g in genres or []:
        if isinstance(g, int):
            name = GENRE_MAP.get(g)
        else:
            name = g
        if name and name not in categories:
            categories.append(name)
    return categories
