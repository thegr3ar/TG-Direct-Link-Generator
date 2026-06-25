import asyncio
import logging
import sys
import os
import time
import base64
import struct
from datetime import datetime, timezone
from urllib.parse import quote_plus
from telethon import TelegramClient, errors
from telethon.tl.types import MessageMediaDocument, DocumentAttributeFilename, MessageMediaPhoto

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger("UserIndexer")

# Add current directory to path
sys.path.append(os.getcwd())

from main.vars import Var
from main.utils.name_extractor import extract
from main.utils.tmdb import TMDBClient, map_genres_to_categories
from main.utils.database import Database

def get_file_name(message):
    if message.media:
        if isinstance(message.media, MessageMediaDocument):
            for attr in message.media.document.attributes:
                if isinstance(attr, DocumentAttributeFilename):
                    return attr.file_name
        elif isinstance(message.media, MessageMediaPhoto):
            return f"photo_{message.id}.jpg"
    return (message.message or "").split('\n')[0].strip()

def get_pyrogram_hash(message):
    """Attempt to generate a Pyrogram-compatible file hash for documents."""
    if message.media and isinstance(message.media, MessageMediaDocument):
        try:
            doc_id = message.media.document.id
            # Pyrogram file_unique_id for document: type=0, version=4
            # Struct: <iiq -> int, int, long long (little-endian)
            data = struct.pack("<iiq", 0, 4, doc_id)
            digest = base64.urlsafe_b64encode(data).decode().rstrip("=")
            return digest[:6]
        except Exception:
            pass
    return ""

async def main():
    print("🔄 Starting indexing with user account...")

    if not Var.API_ID or not Var.API_HASH or not Var.PHONE_NUMBER:
        print("❌ API_ID, API_HASH, or PHONE_NUMBER not set in environment.")
        return

    client = TelegramClient('indexer_user_session', Var.API_ID, Var.API_HASH)

    try:
        await client.start(phone=Var.PHONE_NUMBER)
    except Exception as e:
        print(f"❌ Login failed: {e}")
        return

    me = await client.get_me()
    print(f"✅ Logged in as user.")

    db = Database(Var.MONGODB_URI, Var.DATABASE_NAME)
    await db.ensure_indexes()

    tmdb = TMDBClient(Var.TMDB_API_KEY)

    bin_channel = Var.BIN_CHANNEL
    if not bin_channel:
        print("❌ BIN_CHANNEL not set.")
        return

    try:
        entity = await client.get_entity(bin_channel)
    except Exception as e:
        print(f"❌ Could not find channel {bin_channel}: {e}")
        return

    # Checkpoint logic
    checkpoint = await db.get_checkpoint(bin_channel)
    offset_id = int(checkpoint.get("offset_id", 0) or 0)

    counters = {
        "total": int(checkpoint.get("total", 0) or 0),
        "new": int(checkpoint.get("new", 0) or 0),
        "dup": int(checkpoint.get("dup", 0) or 0),
        "invalid": int(checkpoint.get("invalid", 0) or 0),
        "notfound": int(checkpoint.get("notfound", 0) or 0),
        "error": int(checkpoint.get("error", 0) or 0)
    }

    if offset_id:
        print(f"⏩ Resuming from message {offset_id}...")

    sem = asyncio.Semaphore(Var.INDEX_CONCURRENCY)

    async def process_msg(msg):
        file_name = get_file_name(msg)
        if not file_name:
            return

        parsed = extract(file_name)
        if not parsed.valid:
            counters["invalid"] += 1
            return

        is_dup = await db.is_duplicate(str(msg.id), None, file_name)
        if is_dup:
            counters["dup"] += 1
            return

        async with sem:
            try:
                meta = await tmdb.fetch(parsed.title, parsed.year, parsed.is_tv_show)
            except Exception as e:
                logger.error(f"TMDB error for {parsed.title}: {e}")
                counters["error"] += 1
                return

        if not meta:
            counters["notfound"] += 1
            return

        is_dup = await db.is_duplicate(str(msg.id), meta.tmdb_id, file_name)
        if is_dup:
            counters["dup"] += 1
            return

        file_hash = get_pyrogram_hash(msg)

        base_url = Var.URL.rstrip('/')
        download_url = f"{base_url}/{msg.id}/{quote_plus(file_name)}?hash={file_hash}"
        watch_url = f"{base_url}/watch/{file_hash}{msg.id}"

        doc = {
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
            "message_id": str(msg.id),
            "hash": file_hash,
            "is_tv_show": meta.is_tv_show or parsed.is_tv_show,
            "season": parsed.season,
            "episode": parsed.episode,
            "views": 0,
            "created_at": datetime.now(timezone.utc)
        }

        categories = map_genres_to_categories(meta.genres)
        await db.insert_movie(doc, categories)
        counters["new"] += 1
        print(f"📝 Processing: {doc['title']} ({doc['year']})")

    try:
        async for msg in client.iter_messages(entity, offset_id=offset_id, reverse=True, limit=None):
            counters["total"] += 1
            if not msg.media:
                continue
            try:
                await process_msg(msg)
                # Update checkpoint every 10 messages
                if counters["total"] % 10 == 0:
                    state = {"offset_id": msg.id}
                    state.update(counters)
                    await db.save_checkpoint(bin_channel, state)
            except errors.FloodWaitError as e:
                print(f"⏳ Flood wait: {e.seconds}s")
                await asyncio.sleep(e.seconds)
            except Exception as e:
                logger.error(f"Error processing message {msg.id}: {e}")
                counters["error"] += 1

        # Clear checkpoint on success
        await db.clear_checkpoint(bin_channel)

    except KeyboardInterrupt:
        print("\n🛑 Stopped by user. Saving checkpoint...")
    except Exception as e:
        logger.exception(f"Fatal error during indexing: {e}")
    finally:
        await tmdb.close()
        db.close()
        print("\n✅ Indexing Complete!")
        print(f"📁 Total: {counters['total']}")
        print(f"🆕 New: {counters['new']}")
        print(f"⏭ Duplicates: {counters['dup']}")
        print(f"🚫 Invalid: {counters['invalid']}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Stopped by user.")
