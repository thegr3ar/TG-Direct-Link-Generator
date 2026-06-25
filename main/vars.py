# This file is a part of TG-Direct-Link-Generator

from os import environ
from dotenv import load_dotenv

load_dotenv()


class Var(object):
    MULTI_CLIENT = False
    API_ID = int(environ.get("API_ID"))
    API_HASH = str(environ.get("API_HASH"))
    BOT_TOKEN = str(environ.get("BOT_TOKEN"))
    SLEEP_THRESHOLD = int(environ.get("SLEEP_THRESHOLD", "60"))  # 1 minute
    WORKERS = int(environ.get("WORKERS", "6"))  # 6 workers = 6 commands at once
    BIN_CHANNEL = int(
        environ.get("BIN_CHANNEL", None)
    )  # you NEED to use a CHANNEL when you're using MULTI_CLIENT
    PORT = int(environ.get("PORT", 8080))
    BIND_ADDRESS = str(environ.get("WEB_SERVER_BIND_ADDRESS", "0.0.0.0"))
    PING_INTERVAL = int(environ.get("PING_INTERVAL", "1200"))  # 20 minutes
    HAS_SSL = environ.get("HAS_SSL", False)
    HAS_SSL = True if str(HAS_SSL).lower() == "true" else False
    NO_PORT = environ.get("NO_PORT", False)
    NO_PORT = True if str(NO_PORT).lower() == "true" else False
    if "DYNO" in environ:
        ON_HEROKU = True
        APP_NAME = str(environ.get("APP_NAME"))
    else:
        ON_HEROKU = False
    
    # ============================================================
    # FIX: Correct FQDN and URL generation
    # ============================================================
    # Get FQDN from environment, fallback to BIND_ADDRESS
    FQDN = str(environ.get("FQDN", BIND_ADDRESS))
    
    # If FQDN is 0.0.0.0, use a better fallback
    if FQDN == "0.0.0.0":
        # Try to get from environment or use a default
        FQDN = str(environ.get("FQDN", "localhost"))
    
    # Build URL based on settings
    if ON_HEROKU:
        URL = f"https://{FQDN}/"
    else:
        # Use HTTPS if HAS_SSL is True, otherwise HTTP
        protocol = "https" if HAS_SSL else "http"
        # Add port only if NO_PORT is False and NOT using SSL on port 443
        port = "" if NO_PORT or (HAS_SSL and PORT == 443) else f":{PORT}"
        URL = f"{protocol}://{FQDN}{port}/"
    
    # ============================================================
    # LOGGING FOR DEBUGGING
    # ============================================================
    print(f"📍 FQDN: {FQDN}")
    print(f"📍 URL: {URL}")
    print(f"📍 HAS_SSL: {HAS_SSL}")
    print(f"📍 NO_PORT: {NO_PORT}")
    print(f"📍 PORT: {PORT}")
    print(f"📍 ON_HEROKU: {ON_HEROKU}")

    UPDATES_CHANNEL = "TechZBots"
    OWNER_ID = int(environ.get('OWNER_ID', '777000'))

    BANNED_CHANNELS = list(set(int(x) for x in str(environ.get("BANNED_CHANNELS", "-1001296894100")).split()))
    BANNED_USERS = list(set(int(x) for x in str(environ.get("BANNED_USERS","5275470552 5287015877")).split()))
