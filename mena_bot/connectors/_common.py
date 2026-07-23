"""Shared connector helpers: browser-header fetch, HTML/time cleaning, and the
normalized intel-item schema that every connector emits.

The normalized item is the unit the whole tool is built around: each carries its
own tier, source, timestamp, location, and item link — so the model can assemble
the required official -> semi-official -> unofficial/media output and corroborate
across source types without ever guessing where a fact came from.

NOTE: no `from __future__ import annotations` here — connector tool signatures
that reach the Gemini automatic-function-calling layer must stay runtime-
evaluable (ISAAC lesson: deferred string annotations crash AFC's isinstance).
"""
import html
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

try:
    from zoneinfo import ZoneInfo  # py3.9+; needs `tzdata` on Windows
except ImportError:  # pragma: no cover
    ZoneInfo = None

from ..config import BROWSER_HEADERS, HTTP_TIMEOUT


# ---- local-time stamping (CODE-ENFORCED dual UTC+local) --------------------
# The analyst requires local AND UTC on every item. Local time is derived HERE
# from the item's country via the IANA tz (DST-correct through zoneinfo), so the
# model never has to compute an offset (error-prone, DST-sensitive) — it renders
# both. Fixed-offset fallback (summer-2026, and correct year-round for the
# no-DST Gulf states) is used only if tzdata is unavailable.
_COUNTRY_TZ = {
    "IRAN": ("Asia/Tehran", 3.5), "ISRAEL": ("Asia/Jerusalem", 3.0),
    "BAHRAIN": ("Asia/Bahrain", 3.0), "UAE": ("Asia/Dubai", 4.0),
    "SAUDI_ARABIA": ("Asia/Riyadh", 3.0), "QATAR": ("Asia/Qatar", 3.0),
    "KUWAIT": ("Asia/Kuwait", 3.0), "OMAN": ("Asia/Muscat", 4.0),
    "JORDAN": ("Asia/Amman", 3.0), "LEBANON": ("Asia/Beirut", 3.0),
    "IRAQ": ("Asia/Baghdad", 3.0), "YEMEN_HOUTHIS": ("Asia/Aden", 3.0),
}


def _local_tzinfo(country):
    ent = _COUNTRY_TZ.get(country)
    if not ent:
        return None, None
    name, off = ent
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name), name
        except Exception:  # tzdata missing → fixed-offset fallback
            pass
    return timezone(timedelta(hours=off)), name


def stamp_local_time(item):
    """Fill timestamp_local + local_tz from the item's country + UTC time.
    Idempotent; no-op when country/time is missing (e.g. undated media)."""
    ts, country = item.get("timestamp_utc"), item.get("country")
    if not ts or not country or item.get("timestamp_local"):
        return item
    tz, name = _local_tzinfo(country)
    if tz is None:
        return item
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        loc = dt.astimezone(tz)
    except (ValueError, TypeError):
        return item
    item["timestamp_local"] = loc.isoformat()
    item["local_tz"] = f"UTC{loc.strftime('%z')[:3]}:{loc.strftime('%z')[3:]} ({name})"
    return item


# ---- target-type / impact HINTS (code assist, model verifies) --------------
# Keyword hints so the model doesn't OMIT the analyst's required target-type and
# impact fields. Word-boundary matched (\b) to avoid substring traps ("port" in
# "important"/"transport"). These are HINTS the model confirms against the
# source text — NOT authoritative labels.
def _rx(words):
    return re.compile(r"\b(?:%s)\b" % "|".join(re.escape(w) for w in words), re.I)


_TARGET_HINTS = [
    ("military base", _rx(("air base", "airbase", "military base", "barracks",
        "garrison", "naval base", "radar", "patriot", "air defense",
        "air defence", "al udeid", "al-udeid", "al-asad", "ain al-asad",
        "muwaffaq salti", "sheikh isa", "ali al salem", "al-dhafra", "al dhafra"))),
    ("oil/energy facility", _rx(("oil", "refinery", "gas field", "gas plant",
        "pipeline", "aramco", "power plant", "power station", "energy facility",
        "fuel storage", "petrochemical", "oil terminal"))),
    ("tech/telecom infra", _rx(("data center", "data centre", "fiber", "fibre",
        "fiber-optic", "submarine cable", "telecom", "internet infrastructure",
        "server farm"))),
    ("airport/aviation", _rx(("airport", "air navigation", "runway",
        "civil aviation"))),
    ("port/maritime", _rx(("port", "harbor", "harbour", "tanker", "vessel",
        "seaport"))),
    ("civilian infrastructure", _rx(("hospital", "desalination", "water plant",
        "power grid", "electricity grid"))),
    ("residential", _rx(("residential", "apartment", "neighborhood",
        "neighbourhood", "homes", "houses"))),
]
_IMPACT_HINTS = [
    ("intercepted", _rx(("intercepted", "interception", "shot down", "downed",
        "destroyed the drone"))),
    ("casualties", _rx(("killed", "dead", "wounded", "injured", "casualties",
        "casualty", "martyr", "martyrs"))),
    ("hit/damage", _rx(("struck", "hit", "damaged", "destroyed", "ablaze",
        "set on fire", "direct hit"))),
    ("no damage/no casualties", _rx(("no damage", "no casualties", "unaffected",
        "no injuries", "without damage"))),
]


def classify_target(text):
    """Return (target_type_hints:list, impact_hint:str|None) from text keywords."""
    low = text or ""
    targets = [name for name, rx in _TARGET_HINTS if rx.search(low)]
    impact = next((name for name, rx in _IMPACT_HINTS if rx.search(low)), None)
    return targets, impact


def enrich_item(item):
    """Stamp local time (always) + target/impact hints (tactical items only).
    Idempotent — safe to re-run after a connector sets country/pillars late."""
    stamp_local_time(item)
    if "tactical" in (item.get("pillars") or []) and item.get("text"):
        targets, impact = classify_target(item["text"])
        if targets and not item.get("target_type_hint"):
            item["target_type_hint"] = targets
        if impact and not item.get("impact_hint"):
            item["impact_hint"] = impact
    return item


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch(url, headers=None, timeout=None):
    """GET a URL with browser headers. Returns a requests.Response (raises for
    connection errors, not for HTTP status — caller inspects status_code).

    Browser headers are essential: several official sites (CENTCOM, UKMTO,
    Al Arabiya, gov.il) 403 the Python stdlib fingerprint but return 200 here.
    """
    h = dict(BROWSER_HEADERS)
    if headers:
        h.update(headers)
    return requests.get(url, headers=h, timeout=timeout or HTTP_TIMEOUT)


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_html(fragment):
    """Strip tags/entities from an HTML fragment to plain text (single-spaced)."""
    if not fragment:
        return ""
    text = re.sub(r"<br\s*/?>", " ", fragment, flags=re.I)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def to_utc_iso(value):
    """Best-effort parse of a feed/date string to an ISO-8601 UTC string.

    Handles RFC-822 (RSS pubDate) and ISO-8601 (Atom/JSON). Returns None if it
    cannot parse — callers keep the raw string too, and timestamps are never
    fabricated when parsing fails.
    """
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    # ISO-8601 first (Atom, Telegram <time datetime>, JSON APIs).
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass
    # RFC-822 (RSS pubDate: "Wed, 15 Jul 2026 12:43:00 GMT").
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    # Date-only variants some feeds use (State Dept: "Fri, 10 Jul 2026").
    for fmt in ("%a, %d %b %Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            continue
    return None


# CLAIM-STATUS IS STAMPED IN CODE, NOT LEFT TO THE MODEL. The single most
# dangerous failure in intel work is presenting a claim as confirmed fact. The
# baseline status is derived from the source tier here, so a semi-official item
# physically ARRIVES labeled "CLAIMED (unconfirmed)" — the model can only elevate
# it to CONFIRMED by citing an official corroboration in the same answer, never
# by its own judgment, and can never start from "confirmed". Prompt drift over a
# long session cannot weaken this because it rides on every item, every call.
_TIER_CLAIM_STATUS = {
    "official": "OFFICIAL STATEMENT",           # the body's own on-record word
    "semi_official": "CLAIMED (unconfirmed)",   # attacker/claimant layer — a CLAIM
    "media": "MEDIA REPORT",                    # journalism — reported, not official
    "unverified": "UNCORROBORATED",             # unknown account — treat as weak
}


def claim_status_for(tier):
    return _TIER_CLAIM_STATUS.get(tier, "UNCORROBORATED")


def make_item(source, tier, text, timestamp_utc=None, timestamp_raw=None,
              link=None, source_url=None, country=None, pillars=None,
              language="en", location=None, extra=None):
    """Build one normalized intel item. Every connector returns these.

    location: filled by the connector only when the source structurally provides
    it (e.g. Israel alert locality). Otherwise left None for the model to extract
    from text — location is mandatory in output, so it is a first-class field.

    claim_status: tier-derived and CODE-ENFORCED (see _TIER_CLAIM_STATUS) — the
    model renders it, never overrides it upward without official corroboration.
    """
    item = {
        "source": source,
        "tier": tier,                       # official | semi_official | media
        "claim_status": claim_status_for(tier),  # CODE-STAMPED — never model-set
        "country": country,
        "pillars": pillars or [],
        "text": text,
        "language": language,
        "location": location,               # None => model extracts from text
        "timestamp_utc": timestamp_utc,     # SACRED — copied from source, never invented
        "timestamp_raw": timestamp_raw,     # original string as published
        "link": link,                       # item-specific permalink
        "source_url": source_url,           # source portal (fallback link)
    }
    if extra:
        item.update(extra)
    return enrich_item(item)


def dedupe_items(items):
    """Drop TRUE duplicates only — same source, same text, AND same timestamp.

    The timestamp is part of the key on purpose: repeated official alerts reuse
    identical boilerplate ("The siren has been sounded...") for EACH activation,
    so keying on text alone collapsed 4 distinct Bahrain siren events into 1 and
    the count came back 2 instead of 4 (verified 2026-07-21). Distinct times =
    distinct events; only an exact source+text+time triple is a real duplicate
    (e.g. the same post fetched twice)."""
    seen, out = set(), []
    for it in items:
        key = (it.get("source"), it.get("timestamp_utc"),
               (it.get("text") or "")[:90].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def sort_by_time(items):
    """Newest first; items without a parseable timestamp sink to the bottom."""
    return sorted(items, key=lambda it: it.get("timestamp_utc") or "", reverse=True)
