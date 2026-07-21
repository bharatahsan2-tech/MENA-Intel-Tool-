"""Export the local Telegram .session to a StringSession for CLOUD deploy.

Run this LOCALLY, once, after tools/telegram_login.py has logged you in. It
converts data/mena_telegram.session into a portable string with NO re-login.
Put the printed value into TELEGRAM_STRING_SESSION (a Streamlit Cloud secret or
server env var) so Telegram works on a host that has no session file.

⚠️ The StringSession is a FULL LOGIN CREDENTIAL — anyone with it controls your
Telegram account. Treat it like a password: never commit it, never paste it into
chat. It is written to a local file here so it never has to be copied by hand.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telethon.sessions import SQLiteSession, StringSession  # noqa: E402

from mena_bot.config import TELEGRAM_SESSION_PATH, telegram_api_configured  # noqa: E402


def main():
    if not telegram_api_configured():
        raise SystemExit("Set TELEGRAM_API_ID / TELEGRAM_API_HASH in .env first.")

    sq = SQLiteSession(str(TELEGRAM_SESSION_PATH))
    if not sq.auth_key:
        raise SystemExit(
            "No login found in the session file. Run first:\n"
            "    python tools/telegram_login.py")

    ss = StringSession()
    ss.set_dc(sq.dc_id, sq.server_address, sq.port)
    ss.auth_key = sq.auth_key
    value = ss.save()

    out = Path(__file__).resolve().parent.parent / "telegram_string_session.txt"
    out.write_text(value, encoding="utf-8")
    print(f"StringSession written to: {out}")
    print(f"  length: {len(value)} chars")
    print("\nNext: copy its contents into TELEGRAM_STRING_SESSION as a secret,")
    print("then DELETE this file. It is gitignored, but do not leave it lying around.")


if __name__ == "__main__":
    main()
