"""Telegram via the real API (Telethon) — Phase 2 of Telegram access.

WHY: the t.me/s/ web preview fails for channels that disable it (Tasnimnews and
newsil2022 return zero items today, verified 2026-07-16) and only shows the last
~20 posts. The API reads any public channel, faster, with full history.

GATED like the X connector: without TELEGRAM_API_ID/TELEGRAM_API_HASH in .env
(or before the one-time login), every call returns a structured "not usable"
result and the caller falls back to the web preview — the tool never breaks.

LOGIN IS INTERACTIVE AND SEPARATE: tools/telegram_login.py (one time, creates
data/mena_telegram.session, gitignored). This module must NEVER trigger the
interactive login itself — client.start() inside the bot would hang waiting for
a phone number on stdin.

THREADING: collect.py calls connectors from a ThreadPoolExecutor. Telethon is
asyncio-based and its SQLite session cannot be shared across threads, so all
API access serializes on a module lock, and each call runs in its own event
loop. Serial cost is ~0.3s/channel — fine for ~20 channels.
"""
import os
import threading
from datetime import datetime, timedelta, timezone

from ..config import TELEGRAM_SESSION_PATH, get_telegram_creds, telegram_api_configured
from ._common import make_item, now_utc_iso, sort_by_time

_LOCK = threading.Lock()

# How far past `limit` to scan for text posts on media-heavy channels. One
# Telethon request covers up to 100 messages, so this is effectively free.
_SCAN_MULTIPLIER = 6
_MIN_SCAN = 40


def _unusable(reason):
    return {"ok": False, "api_used": False, "error": reason,
            "note": "Falling back to t.me/s/ web preview where available."}


def _check_usable():
    """Configured + logged in? Returns (client_factory, None) or (None, error dict).

    Session source, in order: a TELEGRAM_STRING_SESSION env var (for CLOUD deploy,
    where the .session file can't exist and interactive login is impossible), else
    the local .session file created by tools/telegram_login.py.
    """
    if not telegram_api_configured():
        return None, _unusable("TELEGRAM_API_ID/TELEGRAM_API_HASH not set in .env")
    try:
        from telethon.sync import TelegramClient
    except ImportError:
        return None, _unusable("telethon not installed — pip install telethon")
    api_id, api_hash = get_telegram_creds()
    string_session = os.getenv("TELEGRAM_STRING_SESSION", "").strip()
    if string_session:
        from telethon.sessions import StringSession
        return (lambda: TelegramClient(
            StringSession(string_session), api_id, api_hash)), None
    return (lambda: TelegramClient(TELEGRAM_SESSION_PATH, api_id, api_hash)), None


def fetch_telegram_api(channel, source_meta=None, limit=10, since_hours=24):
    """Read recent messages from one public channel via the Telegram API.

    TIME-BOUNDED, NOT JUST COUNT-BOUNDED. `limit` alone is a trap: these
    channels post 150-380 times a day during the war, so the 5 newest posts
    cover only ~11-30 MINUTES on farsna/Tasnimnews/almasirah2 (measured
    2026-07-16). `since_hours` bounds the window in real time; `limit` caps
    tokens. When both bind, `truncated=True` says so out loud — the model must
    never present a truncated slice as the full window. Use search_telegram()
    to reach anything older or narrower.

    Same result shape as telegram_web.fetch_telegram, plus api_used=True.
    Returns ok=False (never raises, never prompts) when unconfigured,
    not-logged-in, or the channel is unresolvable.
    """
    meta = source_meta or {}
    name = meta.get("name", f"Telegram/{channel}")
    factory, err = _check_usable()
    if err:
        err = dict(err)
        err.update(source=name, channel=channel)
        return err

    # OVER-FETCH, THEN FILTER TO TEXT. Captionless media posts are useless to us
    # (no OCR), and on image-heavy channels they crowd out the text entirely:
    # mehrnews returned 0 items because its 5 newest posts were all captionless
    # photo cards (verified 2026-07-16). Scanning deeper costs nothing — Telethon
    # pulls up to 100 messages per request — and text-heavy channels are
    # unaffected because we stop as soon as `limit` text posts are collected.
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(since_hours)))
    scan_cap = max(limit * _SCAN_MULTIPLIER, _MIN_SCAN)
    items, skipped, scanned, truncated = [], 0, 0, False
    with _LOCK:
        try:
            with factory() as client:
                if not client.is_user_authorized():
                    return {**_unusable("Telegram session not logged in — run: "
                                        "python tools/telegram_login.py"),
                            "source": name, "channel": channel}
                for m in client.iter_messages(channel, limit=scan_cap):
                    scanned += 1
                    if m.date and m.date < cutoff:
                        break  # left the time window — done, not truncated
                    if len(items) >= limit:
                        truncated = True  # more posts exist inside the window
                        break
                    text = (m.message or "").strip()
                    if not text:
                        skipped += 1
                        continue  # media-only post, no caption
                    ts = m.date.isoformat() if m.date else None
                    items.append(make_item(
                        source=name, tier=meta.get("tier", "unverified"),
                        country=meta.get("country"), pillars=meta.get("pillars", []),
                        language=meta.get("language", "en"), text=text[:700],
                        timestamp_utc=ts, timestamp_raw=ts,
                        link=f"https://t.me/{channel}/{m.id}",
                        source_url=f"https://t.me/s/{channel}",
                    ))
                else:
                    truncated = scanned >= scan_cap
        except Exception as e:  # bad handle, flood-wait, network — degrade, don't crash
            return {**_unusable(f"{type(e).__name__}: {e}"),
                    "source": name, "channel": channel}

    out = {"ok": True, "api_used": True, "source": name, "channel": channel,
           "source_url": f"https://t.me/s/{channel}",
           "retrieved_utc": now_utc_iso(),
           "window_hours": since_hours,
           "oldest_item_utc": items[-1]["timestamp_utc"] if items else None,
           "truncated": truncated,
           "count": len(items), "items": items,
           "scanned": scanned, "media_only_skipped": skipped}
    if truncated:
        out["truncate_note"] = (
            f"Showing the {len(items)} NEWEST text posts only — this channel "
            f"posted more inside the {since_hours}h window (it is high-volume). "
            f"These items reach back only to {out['oldest_item_utc']}, NOT a "
            f"full {since_hours}h. Do not call this a complete window; use "
            "search_telegram(...) to find older or topic-specific posts.")
    if not items and skipped:
        # Honest signal: the channel IS active, we just can't read pictures.
        out["note"] = (f"Channel active but all {skipped} recent posts scanned "
                       "are captionless media (no OCR available) — report as "
                       "'no text posts retrievable', NOT as 'no activity'.")
    return out


def fetch_telegram_best(channel, source_meta=None, limit=10, since_hours=24):
    """API first, web preview fallback — the one entry point callers use."""
    res = fetch_telegram_api(channel, source_meta, limit=limit,
                             since_hours=since_hours)
    if res.get("ok"):
        return res
    from .telegram_web import fetch_telegram
    web = fetch_telegram(channel, source_meta, limit=limit)
    web["api_used"] = False
    web["api_skip_reason"] = res.get("error")
    return web


def search_channels(sources, terms_by_lang, hours=48, per_channel=4):
    """Server-side keyword search across vetted channels, language-routed.

    TELEGRAM SEARCH IS LITERAL, NOT SEMANTIC — this is the whole reason for
    terms_by_lang. Searching "Bahrain" across the Farsi channels returns ZERO
    while "بحرین" returns hits (verified 2026-07-16 on farsna). A single English
    query would silently miss the IRGC-linked sources — looking like it worked
    while returning nothing from the channels that matter most.

    Each source's own `language` field routes it to the right term, so every
    channel gets exactly one search (~0.3s each).

    Args:
        sources: source dicts (need telegram + language + tier).
        terms_by_lang: {"en": "...", "fa": "...", "ar": "...", "he": "..."}.
        hours: how far back to search.
        per_channel: max hits per channel.

    Returns: {"ok", "count", "items", "searched", "no_term_for": [...]}
    """
    factory, err = _check_usable()
    if err:
        return {**err, "count": 0, "items": []}

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours)))
    items, searched, no_term = [], [], []
    with _LOCK:
        try:
            with factory() as client:
                if not client.is_user_authorized():
                    return {**_unusable("Telegram session not logged in — run: "
                                        "python tools/telegram_login.py"),
                            "count": 0, "items": []}
                for s in sources:
                    ch = s.get("telegram")
                    if not ch:
                        continue
                    lang = (s.get("language") or "en").lower()
                    term = (terms_by_lang.get(lang) or "").strip()
                    if not term:
                        # No term in this channel's language: searching in the
                        # wrong language returns 0 and reads as "nothing found".
                        # Report the blind spot instead of faking a clean miss.
                        no_term.append({"channel": ch, "language": lang})
                        continue
                    searched.append({"channel": ch, "language": lang, "term": term})
                    try:
                        msgs = client.get_messages(ch, limit=per_channel,
                                                   search=term)
                    except Exception:
                        continue  # one bad channel must not sink the search
                    for m in msgs:
                        if m.date and m.date < cutoff:
                            continue
                        text = (m.message or "").strip()
                        if not text:
                            continue
                        ts = m.date.isoformat() if m.date else None
                        items.append(make_item(
                            source=s.get("name", f"Telegram/{ch}"),
                            tier=s.get("tier", "unverified"),
                            country=s.get("country"), pillars=s.get("pillars", []),
                            language=lang, text=text[:700],
                            timestamp_utc=ts, timestamp_raw=ts,
                            link=f"https://t.me/{ch}/{m.id}",
                            source_url=f"https://t.me/s/{ch}",
                            extra={"matched_term": term},
                        ))
        except Exception as e:
            return {**_unusable(f"{type(e).__name__}: {e}"), "count": 0, "items": []}

    return {"ok": True, "api_used": True, "count": len(items),
            "retrieved_utc": now_utc_iso(), "window_hours": hours,
            "items": sort_by_time(items), "searched": searched,
            "no_term_for": no_term}


if __name__ == "__main__":
    for ch in ("Tasnimnews", "MilitaryMediaY"):
        r = fetch_telegram_best(ch, limit=3)
        print(ch, "| ok:", r["ok"], "| api:", r.get("api_used"),
              "| count:", r.get("count"), "|", r.get("error", "")[:60])
