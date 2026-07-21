"""Telegram public-channel reader via the t.me/s/<channel> web preview.

Phase 1 Telegram access with NO API key: public channels expose a read-only web
preview we can parse. This already covers the OSINT, Houthi military-media,
IRGC-linked, and hacktivist channels in sources.json. Phase 2 swaps in the
Telethon API (needs a free api_id) for fuller history and lower latency.

CRITICAL: parse each message as a BLOCK so its text stays bound to its own
timestamp and permalink. Extracting text and <time> with separate passes
misaligns them — and a misattributed timestamp violates the timestamps-sacred
rule (verified failure mode 2026-07-16: a channel showed 2024 text against 2026
times when parsed with split regexes).
"""
import re

import requests

from ._common import clean_html, fetch, make_item, to_utc_iso

_TME = "https://t.me/s/{}"

# Each rendered message is one <div class="tgme_widget_message ..."> ... </div>
# carrying a data-post="channel/<id>" attribute. Split on that boundary.
_MSG_SPLIT = re.compile(r'<div class="tgme_widget_message[ "]')
_DATA_POST = re.compile(r'data-post="([^"]+)"')
_TEXT_BLOCK = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
_TIME_ISO = re.compile(r'<time[^>]*datetime="([^"]+)"')


def fetch_telegram(channel, source_meta=None, limit=8):
    """Read recent messages from a public Telegram channel's web preview.

    Args:
        channel: channel handle (without @), e.g. "army21ye", "OSINTdefender".
        source_meta: source dict from sources.json (tier/country/pillars/lang).
            Optional — defaults to an OSINT tag for ad-hoc lookups.
        limit: max most-recent messages to return.

    Returns:
        {"ok": bool, "source", "channel", "count", "items": [...], "error"?,
         "note"?}. Messages carry their own ISO timestamp + t.me permalink.
        Media-only posts (no caption) are skipped (no OCR); text posts kept.
    """
    meta = source_meta or {}
    name = meta.get("name", f"Telegram/{channel}")
    tier = meta.get("tier", "unverified")
    country = meta.get("country")
    pillars = meta.get("pillars", [])
    language = meta.get("language", "en")
    url = _TME.format(channel)

    try:
        resp = fetch(url)
        if resp.status_code != 200:
            return {"ok": False, "source": name, "channel": channel,
                    "error": f"HTTP {resp.status_code} — channel may be private/renamed"}
        body = resp.text
    except requests.RequestException as e:
        return {"ok": False, "source": name, "channel": channel,
                "error": f"request failed: {e}"}

    if "tgme_widget_message" not in body:
        return {"ok": True, "source": name, "channel": channel, "count": 0,
                "items": [],
                "note": "channel reachable but no public message preview "
                        "(preview disabled or no recent posts)"}

    items = []
    # First split segment is page chrome before the first message — skip it.
    for block in _MSG_SPLIT.split(body)[1:]:
        post = _DATA_POST.search(block)
        tmatch = _TIME_ISO.search(block)
        textmatch = _TEXT_BLOCK.search(block)
        text = clean_html(textmatch.group(1)) if textmatch else ""
        if not text:
            continue  # media-only post; no caption to read
        post_id = post.group(1) if post else None
        link = f"https://t.me/{post_id}" if post_id else url
        raw_time = tmatch.group(1) if tmatch else None
        items.append(make_item(
            source=name, tier=tier, country=country, pillars=pillars,
            language=language, text=text[:700],
            timestamp_utc=to_utc_iso(raw_time), timestamp_raw=raw_time,
            link=link, source_url=url,
        ))

    # Page renders oldest -> newest; return the most recent `limit`.
    items = items[-limit:][::-1]
    return {"ok": True, "source": name, "channel": channel,
            "source_url": url, "count": len(items), "items": items}


if __name__ == "__main__":
    for ch in ("OSINTdefender", "army21ye"):
        r = fetch_telegram(ch, limit=3)
        print(f"--- {ch}: ok={r['ok']} count={r.get('count')} {r.get('error') or ''}")
        for it in r.get("items", []):
            # ascii-safe console print (Arabic channels)
            print("   ", it["timestamp_utc"], "|",
                  (it["text"][:80]).encode("ascii", "replace").decode())
