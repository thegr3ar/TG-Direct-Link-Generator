import os
import sys

# ============================================================
# FIX: Environment setup BEFORE any other imports
# ============================================================
os.environ["PYROGRAM_IGNORE_TIME"] = "1"
os.environ['TZ'] = 'Africa/Mogadishu'

import time
try:
    time.tzset()
except AttributeError:
    pass

# This file is a part of TG-Direct-Link-Generator
import asyncio
import logging
import pytz
import ntplib
import socket
from datetime import datetime
from .vars import Var
from aiohttp import web
from pyrogram import idle
from pyrogram.errors import RPCError, BadMsgNotification
from main import utils
from main import StreamBot
from main.server import web_server
from main.bot.clients import initialize_clients

logging.basicConfig(
    level=logging.INFO,
    datefmt="%d/%m/%Y %H:%M:%S",
    format='[%(asctime)s] {%(pathname)s:%(lineno)d} %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(stream=sys.stdout),
              logging.FileHandler("streambot.log", mode="a", encoding="utf-8")],)

logging.getLogger("aiohttp").setLevel(logging.ERROR)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("aiohttp.web").setLevel(logging.ERROR)

server = web.AppRunner(web_server())

if sys.version_info[1] > 9:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
else:
    loop = asyncio.get_event_loop()


def sync_time():
    print("---------------------- Syncing System Time ----------------------")
    ntp_servers = ['pool.ntp.org', 'time.google.com', 'time.cloudflare.com']
    client = ntplib.NTPClient()
    for server_addr in ntp_servers:
        try:
            # Try to resolve and connect using both IPv4 and IPv6 if available,
            # but force IPv4 if we suspect ai_socktype issues
            response = client.request(server_addr, version=3, timeout=5)
            ntp_time = response.tx_time
            system_time = time.time()
            offset = ntp_time - system_time
            print(f"NTP Server: {server_addr}")
            print(f"NTP Time: {datetime.fromtimestamp(ntp_time)}")
            print(f"System Time: {datetime.fromtimestamp(system_time)}")
            print(f"Offset: {offset} seconds")
            print("------------------------------ DONE ------------------------------")
            return offset
        except Exception as e:
            print(f"⚠️ Failed to sync with {server_addr}: {e}")
            continue

    print("❌ Comprehensive NTP sync failure. Proceeding with system time.")
    print("------------------------------ DONE ------------------------------")
    return 0

async def start_services():
    time_offset = sync_time()

    print()
    print("-------------------- Initializing Telegram Bot --------------------")
    
    # Try to start with retry logic
    max_retries = 10
    retry_delay = 5
    
    for attempt in range(max_retries):
        try:
            await StreamBot.start()
            break
        except (RPCError, BadMsgNotification) as e:
            error_msg = str(e).lower()
            if "msg_id" in error_msg or "time" in error_msg or "sync" in error_msg or "[16]" in error_msg:
                print(f"⚠️ Time sync error (attempt {attempt+1}/{max_retries})")
                print(f"   Error: {e}")

                # In Pyrogram v2, adjust the offset
                try:
                    if hasattr(StreamBot, "session"):
                        StreamBot.session.time_offset = int(time_offset) if time_offset != 0 else 0
                except:
                    pass

                print("   ⏳ Waiting and retrying...")
                await asyncio.sleep(retry_delay)
                continue
            else:
                logging.error(f"Critical RPC Error: {e}")
                raise e
        except Exception as e:
            error_msg = str(e).lower()
            if "already terminated" in error_msg or "connection" in error_msg:
                print(f"⚠️ Connection error (attempt {attempt+1}/{max_retries})")
                await asyncio.sleep(retry_delay)
                continue
            else:
                # Catch the filter error here if it still happens despite our sed
                if "edited" in str(e) and "filters" in str(e):
                    logging.error("Detected missing 'edited' filter attribute. Please check plugins.")
                logging.error(f"Unexpected Error during startup: {e}", exc_info=True)
                raise e
    else:
        print("❌ Failed to start bot after multiple attempts")
        return
    
    bot_info = await StreamBot.get_me()
    StreamBot.username = bot_info.username
    print("------------------------------ DONE ------------------------------")
    print()
    print(
        "---------------------- Initializing Clients ----------------------"
    )
    await initialize_clients()
    print("------------------------------ DONE ------------------------------")
    if Var.ON_HEROKU:
        print("------------------ Starting Keep Alive Service ------------------")
        print()
        asyncio.create_task(utils.ping_server())
    print("--------------------- Initializing Web Server ---------------------")
    await server.setup()
    bind_address = "0.0.0.0" if Var.ON_HEROKU else Var.BIND_ADDRESS
    await web.TCPSite(server, bind_address, Var.PORT).start()
    print("------------------------------ DONE ------------------------------")
    print()
    print("------------------------- Service Started -------------------------")
    print("                        bot =>> {}".format(bot_info.first_name))
    if bot_info.dc_id:
        print("                        DC ID =>> {}".format(str(bot_info.dc_id)))
    print("                        server ip =>> {}".format(bind_address, Var.PORT))
    if Var.ON_HEROKU:
        print("                        app running on =>> {}".format(Var.FQDN))
    print("------------------------------------------------------------------")
    print()
    print("""
 _____________________________________________   
|                                             |  
|          Deployed Successfully              |  
|              Join @TechZBots                |
|_____________________________________________|
    """)
    await idle()


async def cleanup():
    try:
        await server.cleanup()
    except Exception:
        pass
    try:
        if getattr(StreamBot, "is_connected", False):
            await StreamBot.stop()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        loop.run_until_complete(start_services())
    except KeyboardInterrupt:
        pass
    except Exception as err:
        logging.error(err, exc_info=True)
    finally:
        try:
            loop.run_until_complete(cleanup())
        except Exception:
            pass
        loop.stop()
        print("------------------------ Stopped Services ------------------------")
