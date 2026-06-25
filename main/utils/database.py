# This file is a part of TG-Direct-Link-Generator
"""MongoDB (Atlas) data layer for the channel media indexer.

Collections:
  movies            - one document per indexed file (full TMDB metadata)
  categories        - distinct genres/categories
  movie_categories  - many-to-many join between movies and categories
  index_state       - checkpoint so /index can resume after a crash
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING, TEXT
from pymongo.errors import DuplicateKeyError

logger = logging.getLogger(__name__)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "unknown"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Database:
    def __init__(self, uri: str, db_name: str):
        if not uri:
            raise ValueError("MONGODB_URI is not configured")
        self._client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=20000)
        self.db = self._client[db_name]
        self.movies = self.db["movies"]
        self.categories = self.db["categories"]
        self.movie_categories = self.db["movie_categories"]
        self.index_state = self.db["index_state"]

    async def ping(self) -> None:
        await self.db.command("ping")

    async def ensure_indexes(self) -> None:
        await self.movies.create_index([("tmdb_id", ASCENDING)], unique=True, sparse=True)
        await self.movies.create_index([("message_id", ASCENDING)], unique=True)
        await self.movies.create_index([("file_name", ASCENDING)])
        await self.movies.create_index([("title", TEXT)])
        await self.movies.create_index([("is_tv_show", ASCENDING)])
        await self.movies.create_index([("rating", DESCENDING)])
        await self.movies.create_index([("views", DESCENDING)])
        await self.movies.create_index([("created_at", DESCENDING)])
        await self.movies.create_index([("genres", ASCENDING)])

        await self.categories.create_index([("slug", ASCENDING)], unique=True)
        await self.categories.create_index([("name", ASCENDING)])

        await self.movie_categories.create_index(
            [("movie_id", ASCENDING), ("category_id", ASCENDING)], unique=True
        )
        await self.movie_categories.create_index([("category_id", ASCENDING)])

    # --- duplicate detection -------------------------------------------------
    async def is_duplicate(
        self, message_id: str, tmdb_id: Optional[int], file_name: str
    ) -> Optional[str]:
        """Return a reason string if a matching movie already exists, else None."""
        if await self.movies.find_one({"message_id": str(message_id)}, {"_id": 1}):
            return "message_id"
        if tmdb_id is not None and await self.movies.find_one(
            {"tmdb_id": tmdb_id}, {"_id": 1}
        ):
            return "tmdb_id"
        if await self.movies.find_one({"file_name": file_name}, {"_id": 1}):
            return "file_name"
        return None

    # --- categories ----------------------------------------------------------
    async def get_or_create_category(self, name: str, tmdb_id: Optional[int] = None):
        slug = slugify(name)
        existing = await self.categories.find_one({"slug": slug}, {"_id": 1})
        if existing:
            return existing["_id"]
        doc = {
            "name": name,
            "slug": slug,
            "tmdb_id": tmdb_id,
            "created_at": _utcnow(),
        }
        try:
            res = await self.categories.insert_one(doc)
            return res.inserted_id
        except DuplicateKeyError:
            existing = await self.categories.find_one({"slug": slug}, {"_id": 1})
            return existing["_id"] if existing else None

    async def link_movie_category(self, movie_id, category_id) -> None:
        try:
            await self.movie_categories.insert_one(
                {
                    "movie_id": movie_id,
                    "category_id": category_id,
                    "created_at": _utcnow(),
                }
            )
        except DuplicateKeyError:
            pass

    # --- movies --------------------------------------------------------------
    async def insert_movie(self, doc: dict, categories: list) -> Optional[object]:
        """Insert a movie and wire up its category links. Returns inserted _id."""
        doc.setdefault("views", 0)
        doc.setdefault("created_at", _utcnow())
        try:
            res = await self.movies.insert_one(doc)
        except DuplicateKeyError as e:
            logger.info("Skipping duplicate movie on insert: %s", e)
            return None
        movie_id = res.inserted_id
        for cat_name in categories:
            cat_id = await self.get_or_create_category(cat_name)
            if cat_id is not None:
                await self.link_movie_category(movie_id, cat_id)
        return movie_id

    async def search_movies(self, query: str, limit: int = 10) -> list:
        cursor = self.movies.find(
            {"$text": {"$search": query}},
            {"score": {"$meta": "textScore"}},
        ).sort([("score", {"$meta": "textScore"})]).limit(limit)
        results = await cursor.to_list(length=limit)
        if results:
            return results
        # Fallback to a case-insensitive regex match if text search finds nothing.
        rx = re.compile(re.escape(query), re.IGNORECASE)
        cursor = self.movies.find({"title": rx}).limit(limit)
        return await cursor.to_list(length=limit)

    # --- stats ---------------------------------------------------------------
    async def stats(self) -> dict:
        total = await self.movies.count_documents({})
        tv = await self.movies.count_documents({"is_tv_show": True})
        cats = await self.categories.count_documents({})
        try:
            db_stats = await self.db.command("dbStats")
            size_mb = round(float(db_stats.get("dataSize", 0)) / (1024 * 1024), 2)
        except Exception:
            size_mb = None
        return {
            "total_movies": total,
            "tv_shows": tv,
            "movies": total - tv,
            "categories": cats,
            "db_size_mb": size_mb,
        }

    # --- checkpoint ----------------------------------------------------------
    async def get_checkpoint(self, channel_id: int) -> dict:
        doc = await self.index_state.find_one({"_id": str(channel_id)})
        return doc or {}

    async def save_checkpoint(self, channel_id: int, state: dict) -> None:
        state = {**state, "updated_at": _utcnow()}
        await self.index_state.update_one(
            {"_id": str(channel_id)}, {"$set": state}, upsert=True
        )

    async def clear_checkpoint(self, channel_id: int) -> None:
        await self.index_state.delete_one({"_id": str(channel_id)})

    def close(self) -> None:
        self._client.close()
