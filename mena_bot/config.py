"""Configuration and secrets — nothing hardcoded, so this is safe to share.

Each teammate supplies their own free Gemini key via a local .env file or an
environment variable. No keys, paths, or machine-specific values live in code.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # python-dotenv optional at runtime; env vars still work.
    pass

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"

# A real browser User-Agent is REQUIRED: several official sites (CENTCOM, UKMTO,
# Al Arabiya, gov.il) block the Python stdlib fingerprint. Verified 2026-07-16
# that requests + this UA returns 200 where urllib got 403.
HTTP_USER_AGENT = os.getenv(
    "MENA_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
)
# Browser-like header set — sent on every fetch to get past WAF fingerprinting.
BROWSER_HEADERS = {
    "User-Agent": HTTP_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# X/Twitter API v2 bearer token — OPTIONAL. The tool runs fully on Telegram +
# Bing without it; setting it activates the X connector with no code change.
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "").strip()

# Telegram API (Telethon) — OPTIONAL. Without it, Telegram falls back to the
# t.me/s/ web preview (which some channels disable, e.g. Tasnimnews). With it,
# every public channel is readable, faster and with full history.
# One-time interactive login: python tools/telegram_login.py
TELEGRAM_SESSION_PATH = str(DATA_DIR / "mena_telegram")  # -> mena_telegram.session


def get_telegram_creds():
    """(api_id, api_hash) or (None, None). Read at call time like the other keys."""
    api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    if api_id.isdigit() and api_hash:
        return int(api_id), api_hash
    return None, None


def telegram_api_configured() -> bool:
    return get_telegram_creds()[0] is not None
# Default learned from ISAAC: gemini-3.1-flash-lite retries failed tools and is
# honest about failure instead of fabricating. Override via env if needed.
GEMINI_MODEL = os.getenv("MENA_GEMINI_MODEL", "gemini-3.1-flash-lite").strip()

HTTP_TIMEOUT = int(os.getenv("MENA_HTTP_TIMEOUT", "20"))


def get_x_token() -> str:
    """Return the X bearer token, or "" if X is not configured.

    Re-reads the environment at call time (same reason as require_gemini_key):
    a host may inject the token after import, and callers must not capture a
    stale module-level value.
    """
    return X_BEARER_TOKEN or os.getenv("X_BEARER_TOKEN", "").strip()


def x_configured() -> bool:
    """True when an X bearer token is available."""
    return bool(get_x_token())


def require_gemini_key() -> str:
    """Return the Gemini key or raise a clear, actionable error.

    Re-reads the environment at call time so hosts that inject the key after
    import (Streamlit secrets, sidebar input) work without a restart.
    """
    key = GEMINI_API_KEY or os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "No GEMINI_API_KEY found. Copy .env.example to .env and add your free "
            "key from https://aistudio.google.com/apikey (or set the env var)."
        )
    return key
