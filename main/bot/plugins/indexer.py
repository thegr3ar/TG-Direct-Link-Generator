# This file is a part of TG-Direct-Link-Generator
"""Channel media indexer: /index, /search, /stats.

Scans BIN_CHANNEL, extracts a clean title from each file name, enriches it with
TMDB metadata and stores everything in MongoDB with categories. Designed for
10k+ files: concurrent TMDB lookups (rate limited), duplicate skipping, live
progress, and checkpoint/resume after a crash.
"""

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

BATCH_SIZE = 50


def _is_indexable(message) -> bool:
    """Only video and document files are indexed (not photos/stickers/etc)."""
    return bool(getattr(message, "video", None) or getattr(message, "document", None))


def _indexable_name(message) -> str:
    """File name for a media message, falling back to its caption."""
    name = get_name(message)
    if not name or name == "None":
        return (message.caption or "").strip()
    return name


_db = None
_db_lock = asyncio.Lock()
_index_running = False
_index_cancel = False


async def get_db() -> Database:
    global _db
    async with _db_lock:
        if _db is None:
            db = Database(Var.MONGODB_URI, Var.DATABASE_NAME)
            await db.ping()
            await db.ensure_indexes()
            _db = db
            logger.info("Connected to MongoDB and ensured indexes")
    return _db


def _config_missing() -> str:
    missing = []
    if not Var.TMDB_API_KEY:
        missing.append("TMDB_API_KEY")
    if not Var.MONGODB_URI:
        missing.append("MONGODB_URI")
    return ", ".join(missing)


def _format_eta(seconds: float) -> str:
    if seconds <= 0 or seconds != seconds:  # nan guard
        return "--"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _progress_text(c: dict, total: int, start: float, db_size, done: bool = False) -> str:
    scanned = c["scanned"]
    elapsed = max(time.monotonic() - start, 0.001)
    pct = (scanned / total * 100) if total else 0
    
    if done:
        header = "✅ **Indexing Complete!**"
        time_line = f"⏱ Total Time: {_format_eta(elapsed)}"
    else:
        rate = scanned / elapsed if elapsed > 0 else 0
        remaining = max(total - scanned, 0)
        eta = remaining / rate if rate > 0 else 0
        header = "🔄 **Indexing Channel...**"
        time_line = f"⏱ Estimated Time Remaining: {_format_eta(eta)}"
    
    size_line = f"💾 Database Size: {db_size} MB" if db_size is not None else ""
    
    return (
        f"{header}\n\n"
        f"📁 Processed: {scanned:,} / {total:,} ({pct:.1f}%)\n"
        f"🆕 New Movies: {c['new']:,}\n"
        f"⏭ Duplicates Skipped: {c['dup']:,}\n"
        f"📺 TV Shows: {c['tv']:,}\n"
        f"🚫 Skipped (no title): {c['invalid']:,}\n"
        f"❓ Not on TMDB: {c['notfound']:,}\n"
        f"⚠️ Errors: {c['error']:,}\n"
        f"{time_line}\n"
        f"{size_line}"
    ).strip()


def _build_movie_doc(message, meta, parsed) -> dict:
    file_name = _indexable_name(message)
    file_hash = get_hash(message)
    msg_id = message.id
    
    # Build URLs correctly
    base_url = Var.URL.rstrip('/')
    download_url = f"{base_url}/{msg_id}/{quote_plus(file_name)}?hash={file_hash}"
    watch_url = f"{base_url}/watch/{file_hash}{msg_id}"
    
    return {
        "title": meta.title or parsed.title,
        "year": meta.year if meta.year is not None else parsed.year,
        "language": meta.language,
        "poster_url": meta.poster_url,
        "backdrop_url": meta.backdrop_url,
        "rating": meta.rating,
        "overview": meta.overview,
        "genres": meta.genres,
        "cast": meta.cast,
        "director": meta.director,
        "tmdb_id": meta.tmdb_id,
        "file_name": file_name,
        "download_url": download_url,
        "watch_url": watch_url,
        "channel_id": str(Var.BIN_CHANNEL),
        "message_id": str(msg_id),
        "hash": file_hash,
        "is_tv_show": meta.is_tv_show or parsed.is_tv_show,
        "season": parsed.season,
        "episode": parsed.episode,
        "views": 0,
    }


async def _process_message(message, db, tmdb, sem, counters) -> None:
    if not _is_indexable(message):
        return
    
    file_name = _indexable_name(message)
    parsed = extract(file_name)
    
    if not parsed.valid:
        counters["invalid"] += 1
        logger.info("Skip (no title): %s [%s]", file_name, parsed.reason)
        return
    
    # Cheap dedup before spending a TMDB call.
    dup = await db.is_duplicate(str(message.id), None, file_name)
    if dup:
        counters["dup"] += 1
        return
    
    async with sem:
        try:
            meta = await tmdb.fetch(parsed.title, parsed.year, parsed.is_tv_show)
        except Exception as e:
            counters["error"] += 1
            logger.exception("TMDB lookup failed for %s", parsed.title)
            return
    
    if meta is None:
        counters["notfound"] += 1
        logger.info("No TMDB match: %s (%s)", parsed.title, parsed.year)
        return
    
    dup = await db.is_duplicate(str(message.id), meta.tmdb_id, file_name)
    if dup:
        counters["dup"] += 1
        return
    
    doc = _build_movie_doc(message, meta, parsed)
    categories = map_genres_to_categories(meta.genres)
    inserted = await db.insert_movie(doc, categories)
    
    if inserted is None:
        counters["dup"] += 1
        return
    
    counters["new"] += 1
    if doc["is_tv_show"]:
        counters["tv"] += 1


async def _get_channel_message_count(bot, channel_id) -> int:
    """Get total message count in channel (works for bots)"""
    try:
        count = 0
        async for _ in bot.get_chat_history(channel_id, limit=1):
            count += 1
            break
        
        # Then try to get more accurate count
        # Note: Bots cannot get exact count, we use a limit
        total = 0
        async for msg in bot.get_chat_history(channel_id, limit=5000):
            total += 1
        
        logger.info(f"Channel has approximately {total} messages")
        return total
        
    except Exception as e:
        logger.warning(f"Could not get message count: {e}")
        return 0


async def _get_messages_since(bot, channel_id, offset_id):
    """Get messages since offset_id"""
    try:
        messages = []
        async for msg in bot.get_chat_history(channel_id, offset_id=offset_id, limit=10000):
            messages.append(msg)
        return messages
    except Exception as e:
        logger.error(f"Error getting messages: {e}")
        return []


@StreamBot.on_message(filters.command("index") & filters.user(Var.OWNER_ID))
async def index_command(bot, message):
    global _index_running, _index_cancel
    
    missing = _config_missing()
    if missing:
        await message.reply_text(f"❌ Missing config: `{missing}`")
        return
    
    if _index_running:
        await message.reply_text("⚠️ An indexing job is already running. Use /cancelindex to stop it.")
        return

    _index_running = True
    _index_cancel = False
    status = await message.reply_text("🔄 Starting indexer...")
    start = time.monotonic()
    
    counters = {
        "scanned": 0, 
        "new": 0, 
        "dup": 0, 
        "tv": 0, 
        "invalid": 0,
        "notfound": 0, 
        "error": 0
    }
    
    tmdb = TMDBClient(Var.TMDB_API_KEY, rate_per_sec=40)
    
    try:
        db = await get_db()
        
        # Try to get total count
        total = await _get_channel_message_count(bot, Var.BIN_CHANNEL)
        
        # Get checkpoint
        checkpoint = await db.get_checkpoint(Var.BIN_CHANNEL)
        offset_id = int(checkpoint.get("offset_id", 0) or 0)
        
        # Restore counters from checkpoint
        for k in counters:
            counters[k] = int(checkpoint.get(k, counters[k]) or 0)

        sem = asyncio.Semaphore(Var.INDEX_CONCURRENCY)
        batch = []
        last_progress = 0

        async def flush(batch_msgs):
            nonlocal last_progress
            
            # Process messages in parallel
            tasks = []
            for m in batch_msgs:
                if _is_indexable(m):
                    tasks.append(_process_message(m, db, tmdb, sem, counters))
            
            if tasks:
                await asyncio.gather(*tasks)
            
            counters["scanned"] += len(batch_msgs)
            
            # Save checkpoint
            checkpoint_data = {"offset_id": batch_msgs[-1].id}
            checkpoint_data.update(counters)
            await db.save_checkpoint(Var.BIN_CHANNEL, checkpoint_data)
            
            # Update progress
            if counters["scanned"] - last_progress >= Var.INDEX_PROGRESS_EVERY:
                last_progress = counters["scanned"]
                stats = await db.stats()
                size = stats.get("db_size_mb")
                try:
                    await status.edit_text(_progress_text(counters, total, start, size))
                except Exception:
                    pass

        # Main loop - get messages from channel
        async for msg in bot.get_chat_history(
            Var.BIN_CHANNEL, 
            offset_id=offset_id,
            limit=10000  # Max messages to fetch per run
        ):
            if _index_cancel:
                break
            
            # Skip if message is before checkpoint
            if offset_id and msg.id <= offset_id:
                counters["scanned"] += 1
                continue
            
            batch.append(msg)
            
            if len(batch) >= BATCH_SIZE:
                try:
                    await flush(batch)
                except FloodWait as fw:
                    logger.info(f"Flood wait: {fw.value}s")
                    await asyncio.sleep(fw.value)
                batch = []
        
        # Flush remaining
        if batch and not _index_cancel:
            await flush(batch)

        # Clear checkpoint on success
        await db.clear_checkpoint(Var.BIN_CHANNEL)
        
        stats = await db.stats()
        size = stats.get("db_size_mb")
        final = _progress_text(counters, total or counters["scanned"], start, size, done=True)
        
        if _index_cancel:
            final = "🛑 **Indexing Cancelled.**\n\n" + final
        
        try:
            await status.edit_text(final)
        except Exception:
            await message.reply_text(final)
            
    except FloodWait as fw:
        logger.info(f"Flood wait: {fw.value}s")
        await asyncio.sleep(fw.value)
        # Retry after flood wait
        await status.edit_text(f"⏳ Flood wait: {fw.value}s, resuming...")
        await asyncio.sleep(1)
        
    except RPCError as e:
        logger.exception("RPC Error during indexing")
        error_msg = str(e)
        
        if "BOT_METHOD_INVALID" in error_msg or "messages.GetHistory" in error_msg:
            # This is the critical error - bot cannot use GetHistory
            await status.edit_text(
                "❌ **Bot cannot fetch channel history.**\n\n"
                "Please make sure:\n"
                "1. The bot is added as **admin** to the channel\n"
                "2. The bot has **'View Messages'** permission\n"
                "3. Try using a **user account** instead of bot for indexing\n\n"
                f"Error: `{error_msg}`"
            )
        else:
            await status.edit_text(f"❌ Indexing failed: `{error_msg}`\nProgress checkpointed; re-run /index to resume.")
            
    except Exception as e:
        logger.exception("Indexing failed")
        await status.edit_text(f"❌ Indexing failed: `{e}`\nProgress was checkpointed; re-run /index to resume.")
        
    finally:
        _index_running = False
        await tmdb.close()


@StreamBot.on_message(filters.command("cancelindex") & filters.user(Var.OWNER_ID))
async def cancel_index_command(bot, message):
    global _index_cancel
    if not _index_running:
        await message.reply_text("No indexing job is currently running.")
        return
    _index_cancel = True
    await message.reply_text("🛑 Cancelling after the current batch...")


@StreamBot.on_message(filters.command("stats") & filters.user(Var.OWNER_ID))
async def stats_command(bot, message):
    if _config_missing():
        await message.reply_text(f"❌ Missing config: `{_config_missing()}`")
        return
    
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
    if _config_missing():
        await message.reply_text(f"❌ Missing config: `{_config_missing()}`")
        return
    
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
