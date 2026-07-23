"""X (Twitter) API v2 connector — official + media accounts, by handle.

WHY X IS HERE: the highest-value tactical/maritime bodies post to X FIRST —
UKMTO publishes vessel-incident advisories (position, time, description) to X
before anywhere else; NCEMA (the UAE alert authority) is X-first; CENTCOM
confirms strikes on X well before its press-release page updates. Bing relay of
those same statements lags 15-60 minutes. That gap is the whole case for X.

GATED: inactive until X_BEARER_TOKEN is set. Every call returns a structured
"not configured" result instead of raising, so the tool runs fully on
Telegram + Bing without X, and lights up the day a token is added.

COST DISCIPLINE (pay-per-use is $/post read): handles are BATCHED into one
`from:a OR from:b` query per call rather than one request per account, and
retweets are excluded — you pay for posts delivered, so never pull a firehose.

NOT LIVE-VERIFIED: no token existed at build time. Every handle in sources.json
carries verified=false; confirm handles on the first real run.
"""
import requests

from ..config import HTTP_TIMEOUT, get_x_token, x_configured
from ._common import make_item, now_utc_iso, to_utc_iso

_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"

# X caps a search query string; batch conservatively so we never 400 on length.
_MAX_HANDLES_PER_QUERY = 12


def _not_configured(handles):
    return {
        "ok": False,
        "configured": False,
        "handles": handles,
        "count": 0,
        "items": [],
        "error": "X_BEARER_TOKEN not set — X source is inactive.",
        "note": ("X is not configured, so these accounts were NOT read. Say so "
                 "plainly if the user asks about them; cover the same bodies via "
                 "get_official_reporting (Bing relay) and Telegram instead. Do "
                 "NOT present X content you did not fetch."),
    }


def fetch_x(handles, source_meta_by_handle=None, limit=10, since_hours=0):
    """Read recent posts from a batch of X accounts in ONE query.

    Args:
        handles: list of handles without '@', e.g. ["CENTCOM", "UK_MTO"].
        source_meta_by_handle: {handle_lower: source dict from sources.json} so
            each post inherits its account's vetted tier/country/pillars.
        limit: max posts to REQUEST across the batch (API floor 10, cap 100).
        since_hours: if > 0, drop posts older than this many hours and report
            `truncated` when the request cap was hit before the window ended —
            i.e. more posts may exist inside the window than were returned. This
            is what makes "how many X in the last 24h" answerable: raise `limit`
            and set `since_hours` so the whole window is covered.

    Returns:
        {"ok", "configured", "count", "items", "truncated"?, "window_hours"?}
    """
    handles = [h.lstrip("@").strip() for h in (handles or []) if h and h.strip()]
    if not handles:
        return {"ok": True, "configured": x_configured(), "count": 0, "items": []}
    if not x_configured():
        return _not_configured(handles)

    meta_map = {k.lower(): v for k, v in (source_meta_by_handle or {}).items()}
    batch = handles[:_MAX_HANDLES_PER_QUERY]
    query = "(" + " OR ".join(f"from:{h}" for h in batch) + ") -is:retweet"

    params = {
        "query": query,
        "max_results": max(10, min(int(limit), 100)),  # API floor is 10
        "tweet.fields": "created_at,author_id,entities,lang",
        "expansions": "author_id",
        "user.fields": "username,name,verified",
    }
    headers = {"Authorization": f"Bearer {get_x_token()}"}

    try:
        resp = requests.get(_SEARCH_URL, params=params, headers=headers,
                            timeout=HTTP_TIMEOUT)
    except requests.RequestException as e:
        return {"ok": False, "configured": True, "handles": batch, "count": 0,
                "items": [], "error": f"X request failed: {e}"}

    if resp.status_code == 401:
        return {"ok": False, "configured": True, "handles": batch, "count": 0,
                "items": [], "error": "X auth failed (401) — X_BEARER_TOKEN invalid or expired."}
    if resp.status_code == 429:
        return {"ok": False, "configured": True, "handles": batch, "count": 0,
                "items": [], "error": "X rate limit / quota exhausted (429)."}
    if resp.status_code != 200:
        return {"ok": False, "configured": True, "handles": batch, "count": 0,
                "items": [], "error": f"X HTTP {resp.status_code}: {resp.text[:200]}"}

    try:
        payload = resp.json()
    except ValueError as e:
        return {"ok": False, "configured": True, "handles": batch, "count": 0,
                "items": [], "error": f"X parse error: {e}"}

    users = {u["id"]: u for u in payload.get("includes", {}).get("users", [])}
    items = []
    for t in payload.get("data", []) or []:
        user = users.get(t.get("author_id"), {})
        handle = user.get("username", "")
        meta = meta_map.get(handle.lower(), {})
        created = t.get("created_at")
        if meta.get("name"):
            source_name = meta["name"]
        elif handle:
            source_name = f"X/@{handle}"
        else:
            source_name = "X/unknown"
        items.append(make_item(
            source=source_name,
            # An account absent from sources.json is NOT trusted into a tier.
            tier=meta.get("tier", "unverified"),
            country=meta.get("country"),
            pillars=meta.get("pillars", []),
            language=t.get("lang") or meta.get("language", "en"),
            text=(t.get("text") or "")[:700],
            timestamp_utc=to_utc_iso(created),
            timestamp_raw=created,
            link=f"https://x.com/{handle}/status/{t.get('id')}" if handle else None,
            source_url=f"https://x.com/{handle}" if handle else None,
            extra={"x_handle": handle},
        ))

    # Time-window filter (for counting/enumeration over a fixed window). If the
    # API returned a full page (hit max_results) and we're still inside the
    # window, older in-window posts were cut off — flag truncated so the model
    # never reports a count from a partial window.
    truncated = False
    requested = max(10, min(int(limit), 100))
    if since_hours and since_hours > 0:
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        raw_n = len(items)
        kept = [it for it in items
                if it.get("timestamp_utc") and
                datetime.fromisoformat(it["timestamp_utc"]) >= cutoff]
        # If nothing was dropped by the window AND we filled the page, the oldest
        # returned post is still inside the window -> there may be more beyond it.
        truncated = (len(kept) == raw_n) and (raw_n >= requested)
        items = kept

    return {
        "ok": True,
        "configured": True,
        "handles": batch,
        "query": query,
        "window_hours": since_hours or None,
        "truncated": truncated,
        "count": len(items),
        "retrieved_utc": now_utc_iso(),
        "items": items,
        "note": ("X posts are PRIMARY SOURCE posts from the named account. An "
                 "official account's post is an official statement; a media "
                 "account's post is media. Cite the post permalink."),
    }


if __name__ == "__main__":
    r = fetch_x(["CENTCOM", "UK_MTO"], limit=10)
    print("ok:", r["ok"], "| configured:", r.get("configured"),
          "| count:", r.get("count"), "|", r.get("error", ""))
