# Scans BIN_CHANNEL, extracts a clean title from each file name, enriches it with
# metadata from TMDB, and saves it to MongoDB.

import asyncio
import logging
import time
from urllib.parse import quote_plus

from pyrogram import filters
from pyrogram.errors import FloodWait, RPCError

from main.bot import StreamBot
from main.vars import Var
from main.utils.name_extractor import extract
from main.utils.tmdb import TMDBClient, map_genres_to_categories
from main.utils.database import Database
from main.utils.file_properties import get_hash, get_name

logger = logging.getLogger(__name__)

_db = None
_db_lock = asyncio.Lock()

async def get_db() -> Database:
    global _db
    async with _db_lock:
        if _db is None:
            db = Database(Var.MONGODB_URI, Var.DATABASE_NAME)
            await db.ping()
            await db.ensure_indexes()
            _db = db
        return _db


@StreamBot.on_message(filters.command("index") & filters.user(Var.OWNER_ID))
async def index_command(bot, message):
    await message.reply_text(
        "⚠️ **Indexing cannot be done by bots due to Telegram API restrictions.**\n\n"
        "Please run the `indexer_user.py` script on your server:\n"
        "`python3 indexer_user.py`\n\n"
        "This script uses your Telegram user account to scan the channel once."
    )


@StreamBot.on_message(filters.command("stats") & filters.user(Var.OWNER_ID))
async def stats_command(bot, message):
    try:
        db = await get_db()
        s = await db.stats()
    except Exception as e:
        await message.reply_text(f"❌ Could not read stats: `{e}`")
        return
    
    size = f"{s['db_size_mb']} MB" if s["db_size_mb"] is not None else "n/a"
    await message.reply_text(
        "📊 **Database Stats**\n\n"
        f"🎬 Total: {s['total_movies']:,}\n"
        f"🎞 Movies: {s['movies']:,}\n"
        f"📺 TV Shows: {s['tv_shows']:,}\n"
        f"🏷️ Categories: {s['categories']:,}\n"
        f"💾 Database Size: {size}"
    )


@StreamBot.on_message(filters.command("search") & ~filters.user(Var.BANNED_USERS))
async def search_command(bot, message):
    if len(message.command) < 2:
        await message.reply_text("Usage: `/search <title>`")
        return
    
    query = message.text.split(None, 1)[1].strip()
    
    try:
        db = await get_db()
        results = await db.search_movies(query, limit=10)
    except Exception as e:
        await message.reply_text(f"❌ Search failed: `{e}`")
        return
    
    if not results:
        await message.reply_text(f"No results for `{query}`.")
        return
    
    lines = [f"🔎 **Results for** `{query}`:\n"]
    for r in results:
        year = r.get("year") or "—"
        kind = "📺" if r.get("is_tv_show") else "🎬"
        rating = r.get("rating") or 0
        url = r.get("watch_url") or r.get("download_url") or ""
        title = r.get("title", "Unknown")
        lines.append(f"{kind} [{title} ({year})]({url}) ⭐ {rating}")
    
    await message.reply_text("\n".join(lines), disable_web_page_preview=True)
