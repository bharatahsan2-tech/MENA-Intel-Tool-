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


def _download_for_hash(client, msg):
    """Bytes of a photo message, preferring a SMALL thumbnail to save bandwidth.

    dHash reduces any image to an 8x8 grid, so a ~320px medium thumbnail carries
    every bit of signal the hash needs while being a fraction of the full-photo
    download — the difference between a snappy scoped search and a slow one over
    the serialized lock. We prefer the 'm' size, fall back through larger thumbs,
    and finally to the full download; a stripped inline placeholder (a few blurry
    bytes) is skipped because it does not decode to a real image. Any failure
    falls through, and None means "could not fetch" (caller skips the photo)."""
    thumb = None
    try:
        sizes = list(getattr(msg.photo, "sizes", None) or [])
        # smaller-is-better ranking; 'm' (~320px) is the sweet spot for hashing
        rank = {"m": 0, "s": 1, "x": 2, "y": 3, "w": 4}
        ranked = []
        for sz in sizes:
            if type(sz).__name__ in ("PhotoStrippedSize", "PhotoPathSize"):
                continue  # inline/vector placeholder, not a real raster image
            ranked.append((rank.get(getattr(sz, "type", ""), 9), sz))
        if ranked:
            ranked.sort(key=lambda r: r[0])
            thumb = ranked[0][1]
    except Exception:
        thumb = None
    for attempt in (thumb, -1, None):  # chosen thumb -> largest thumb -> full
        try:
            data = client.download_media(msg, file=bytes, thumb=attempt)
            if data:
                return data
        except Exception:
            continue
    return None


def match_channels_by_image(target_hashes, sources, hours=48, per_channel=20,
                            max_downloads=150, max_distance=10,
                            scan_multiplier=8):
    """REVERSE-IMAGE search: find vetted-channel PHOTO posts whose image matches
    an uploaded image, by perceptual (dHash) Hamming distance.

    This closes the caption-LESS repost gap. search_channels() matches on the
    TEXT a channel typed with a photo, so a channel that reposts the SAME image
    with a different caption — or none — is invisible to it. Here we download the
    photos themselves and compare image-hash to image-hash.

    COST / SCOPE — this is the heaviest tool (it downloads images, serialized on
    the Telethon lock), so it is bounded on every axis:
      - PHOTO messages only; documents, PDFs, videos and text are skipped.
      - `hours` bounds the window; `per_channel` caps downloads per channel and
        `max_downloads` caps the TOTAL, so an unscoped sweep cannot run away.
        When the total cap binds, truncated=True and coverage is PARTIAL.
      - PREFER TO NARROW: pass a single-channel `sources` list (candidate channel
        from a caption search) so only that channel's photos download — seconds,
        not a full sweep.

    Args:
        target_hashes: dHash int(s) of the uploaded image(s).
        sources: source dicts (need telegram + tier + name).
        hours: how far back to look for photo posts.
        per_channel: max photos to DOWNLOAD from one channel.
        max_downloads: max photos to download in total (hard bandwidth cap).
        max_distance: Hamming threshold for a match (<= is a match). 64-bit hash;
            0 = identical, <=10 tolerates the recompression/resize Telegram
            applies on repost (tuned against those exact distortions).
        scan_multiplier: message-scan cap per channel = per_channel*this (bounds
            how deep we enumerate metadata looking for photos on busy channels).

    Returns:
        {"ok", "count", "matches": [{channel, message_id, link, distance,
         timestamp_utc, tier, source}], "channels_scanned", "photos_checked",
         "downloaded", "truncated", ...}. Matches are sorted closest-first, ties
        broken by EARLIEST post — the earliest close match is the likely
        originator, later ones are reposts.
    """
    targets = [t for t in (target_hashes or []) if t is not None]
    if not targets:
        return {"ok": True, "count": 0, "matches": [],
                "note": "No image hash supplied — nothing to match."}

    factory, err = _check_usable()
    if err:
        return {**err, "count": 0, "matches": []}

    from ..imagehash import dhash_bytes, hamming

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours)))
    matches, channels_scanned = [], []
    photos_checked, downloaded = 0, 0
    closest_miss = None
    hit_total_cap, scan_capped_any = False, False

    with _LOCK:
        try:
            with factory() as client:
                if not client.is_user_authorized():
                    return {**_unusable("Telegram session not logged in — run: "
                                        "python tools/telegram_login.py"),
                            "count": 0, "matches": []}
                scan_cap = max(per_channel * scan_multiplier, _MIN_SCAN)
                for s in sources:
                    ch = s.get("telegram")
                    if not ch:
                        continue
                    if downloaded >= max_downloads:
                        hit_total_cap = True
                        break
                    ch_dl, ch_scanned, reached_cutoff = 0, 0, False
                    try:
                        for m in client.iter_messages(ch, limit=scan_cap):
                            ch_scanned += 1
                            if m.date and m.date < cutoff:
                                reached_cutoff = True
                                break  # left the window — this channel is done
                            if not m.photo:
                                continue  # only photo messages carry an image
                            if downloaded >= max_downloads:
                                hit_total_cap = True
                                break
                            if ch_dl >= per_channel:
                                break
                            blob = _download_for_hash(client, m)
                            if blob is None:
                                continue
                            downloaded += 1
                            ch_dl += 1
                            photos_checked += 1
                            h = dhash_bytes(blob)
                            if h is None:
                                continue
                            dist = min(hamming(h, t) for t in targets)
                            if dist <= max_distance:
                                ts = m.date.isoformat() if m.date else None
                                matches.append({
                                    "channel": ch, "message_id": m.id,
                                    "link": f"https://t.me/{ch}/{m.id}",
                                    "distance": dist, "timestamp_utc": ts,
                                    "tier": s.get("tier", "unverified"),
                                    "source": s.get("name", f"Telegram/{ch}"),
                                })
                            elif closest_miss is None or dist < closest_miss["distance"]:
                                closest_miss = {"channel": ch, "distance": dist,
                                                "link": f"https://t.me/{ch}/{m.id}"}
                    except Exception:
                        continue  # one bad channel must not sink the sweep
                    # Coverage honesty: hitting the scan cap before the cutoff
                    # (and before filling per_channel) means older in-window
                    # photos on this channel were NOT checked.
                    if (not reached_cutoff and ch_scanned >= scan_cap
                            and ch_dl < per_channel):
                        scan_capped_any = True
                    channels_scanned.append({"channel": ch, "downloaded": ch_dl,
                                             "scanned": ch_scanned,
                                             "reached_window_edge": reached_cutoff})
                    if hit_total_cap:
                        break
        except Exception as e:
            return {**_unusable(f"{type(e).__name__}: {e}"),
                    "count": 0, "matches": []}

    # Closest first; ties broken by EARLIEST timestamp (the likely originator).
    matches.sort(key=lambda x: (x["distance"], x["timestamp_utc"] or "9999"))

    out = {"ok": True, "api_used": True, "retrieved_utc": now_utc_iso(),
           "window_hours": hours, "max_distance": max_distance,
           "count": len(matches), "matches": matches,
           "channels_scanned": channels_scanned,
           "photos_checked": photos_checked, "downloaded": downloaded,
           "truncated": hit_total_cap}
    if not matches:
        out["closest_miss"] = closest_miss
        near = ("Closest photo was Hamming distance "
                f"{closest_miss['distance']} ({closest_miss['link']}) — that is a "
                "DIFFERENT image, do NOT present it as a match. "
                if closest_miss else "")
        out["note"] = (
            f"No photo within Hamming distance {max_distance} of the uploaded "
            f"image was found across the {len(channels_scanned)} channel(s) "
            f"scanned over {hours}h. " + near +
            "So no vetted channel reposted this exact image in-window as a photo "
            "(it may have been posted as a file/document, outside the window, or "
            "older than the scan). Widen `hours`, or say the image could not be "
            "located — do NOT invent a source or cite a channel homepage.")
    if hit_total_cap:
        out["truncate_note"] = (
            f"Hit the {max_downloads}-download cap before scanning every channel "
            "— coverage is PARTIAL. A null result here is NOT proof of absence: "
            "narrow to a candidate channel (from caption search) and re-run, or "
            "raise the window.")
    elif scan_capped_any:
        out["partial_note"] = (
            "On at least one high-volume channel the message-scan cap was hit "
            "before reaching the window edge, so its OLDER in-window photos were "
            "not checked. If you expected a hit there, pass that single channel "
            "to search it deeper.")
    return out


def search_channels(sources, terms_by_lang, hours=72, per_channel=4):
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
