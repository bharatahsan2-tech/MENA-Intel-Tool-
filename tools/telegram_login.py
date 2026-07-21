"""One-time interactive Telegram login — creates the gitignored session file.

Run this YOURSELF in a terminal (it prompts for your phone + the code Telegram
sends in-app, and your 2FA password if you have one):

    python tools/telegram_login.py

After it says LOGGED IN, the bot uses the API silently forever; never run this
again unless you delete data/mena_telegram.session.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mena_bot.config import TELEGRAM_SESSION_PATH, get_telegram_creds  # noqa: E402

api_id, api_hash = get_telegram_creds()
if api_id is None:
    sys.exit("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env first "
             "(from https://my.telegram.org -> API development tools).")

try:
    from telethon.sync import TelegramClient
except ImportError:
    sys.exit("telethon not installed — run: pip install telethon")

with TelegramClient(TELEGRAM_SESSION_PATH, api_id, api_hash) as client:
    client.start()  # prompts: phone -> code (in your Telegram app) -> 2FA pw
    me = client.get_me()
    print(f"\nLOGGED IN as {me.first_name} (@{me.username or 'no username'})")
    print(f"Session saved: {TELEGRAM_SESSION_PATH}.session (gitignored)")
    print("The bot will now use the Telegram API automatically.")
