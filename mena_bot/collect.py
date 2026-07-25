"""Collection dispatcher — turns a set of selected sources into normalized intel
items by routing each to its transport, concurrently.

TRANSPORTS (user directive 2026-07-16 — only these three, plus two exceptions):
  telegram -> t.me/s/<handle> public preview   (actor primary sources)
  x        -> X API v2 recent search           (gated on X_BEARER_TOKEN)
  bing     -> Bing News RSS relay              (official bodies that block scripts)
  rss      -> native feed  (exception: Iran state IRNA/PressTV/Mehr, State Dept)
  api      -> dedicated tool (Tzeva Adom Israel alerts) — NOT collected here

WHY `bing` EXISTS: 30 of the previous 61 sources were access="web" and this
dispatcher only ever fetched rss/telegram — so CENTCOM, KUNA, BNA, NCEMA, WAM,
UKMTO et al. silently returned ZERO items on every run. They now have a working
transport: their statements reach the wires within minutes and a SIMPLE Bing
query surfaces them with direct article links (live-verified 2026-07-16).

RELAY HONESTY: a `bing`-routed item is the body's statement read through media
relay, not fetched from the agency. Those items keep the body's tier (an
official statement relayed by Reuters is still an official statement) but carry
retrieval="bing_news_relay" + relayed_by, and the prompt requires that be said
out loud. X-batching keeps cost down: pay-per-use bills per post delivered.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from .connectors._common import dedupe_items, enrich_item, sort_by_time
from .connectors.news import get_media_reporting
from .connectors.rss import fetch_rss
from .connectors.telegram_api import fetch_telegram_best
from .connectors.x_api import fetch_x

# Transports this dispatcher can fetch. "api" sources are reached by their own
# dedicated tool (get_israel_alerts), not here.
FETCHABLE = ("rss", "telegram", "bing", "x")

# Per bing-routed source: cap queries so a wide selection can't fan out into
# dozens of Bing calls. Several SMALL queries + dedupe beats one big query.
_MAX_BING_QUERIES = 2


# Conflict lexicon spanning all four pillars. Deliberately BROAD — a canned
# relay query like "Bahrain BNA" pulls that country's general news, and the
# body gate alone can't tell a strike from a youth programme ("Youth City 2030
# ... Bahrain's Creative Talent" passed the body gate, verified 2026-07-16).
# Erring wide: a little noise beats dropping a real strategic item.
_CONFLICT_TERMS = (
    "strike", "struck", "missile", "drone", "uav", "siren", "intercept",
    "attack", "war", "conflict", "iran", "irgc", "tehran", "houthi", "israel",
    "hezbollah", "blast", "explosion", "casualt", "killed", "wounded",
    "air defen", "airspace", "base", "military", "army", "troops",
    "vessel", "tanker", "ship", "hormuz", "strait", "blockade", "maritime",
    "shipping", "port", "cyber", "hack", "outage", "cable", "data cent",
    "ceasefire", "truce", "talks", "negotiat", "diplomac", "mou", "sanction",
    "escalat", "threat", "warning", "evacuat", "embassy", "advisory",
    "flight", "airport", "closure", "nuclear", "retaliat", "defen",
    "centcom", "ncema", "ukmto", "gulf",
)


def _relevant(title: str, terms: list) -> bool:
    """True if an article is BOTH about this body/country AND about the war.

    THE GATE THAT KEEPS THE TIERS HONEST. Bing News returns articles MATCHING a
    query, not the queried agency's statements: querying "Bahrain interior
    ministry" returned a Conde Nast travel piece and a story about Chinese
    seaborne trade. Both gates are needed — the body gate stops unrelated
    coverage being attached to a government body, the conflict gate stops that
    country's routine domestic news becoming "intel". Verified 2026-07-16.
    """
    low = (title or "").lower()
    if not low:
        return False
    if terms and not any(t.lower() in low for t in terms):
        return False
    return any(t in low for t in _CONFLICT_TERMS)


def _fetch_bing_source(source: dict, per_source: int) -> list:
    """Retrieve MEDIA COVERAGE of one official/semi-official body via Bing News.

    TIER HONESTY — the critical rule here: these items stay tier="media". A Bing
    result is NEVER an official statement; it is an outlet's article that may
    REPORT one. Re-tagging it "official" because the query named an official
    body would launder a travel magazine into a government source. The body is
    recorded in `covers_body` instead, and the model is told to look inside the
    article for a quoted official statement and attribute it "<body>, per
    <outlet>".

    The honest consequence: for Gulf states with no direct feed, the OFFICIAL
    section may legitimately be empty. Saying "no direct statement from KUNA"
    IS the intel — it is not a gap to paper over.
    """
    queries = source.get("bing_queries") or []
    if not queries:
        return []
    terms = source.get("relevance_terms") or []
    body = source.get("name")
    # Low-volume advisory bodies need a wider window or they return nothing at
    # all (verified: "UKMTO" -> 0 items at day, 4 real advisories at week).
    freshness = source.get("relay_freshness", "day")
    out: list = []
    for q in queries[:_MAX_BING_QUERIES]:
        # broaden=False: the relay path uses its own vetted per-body queries +
        # relevance gate; auto-broadening would pull off-body country noise.
        res = get_media_reporting(q, freshness=freshness, max_items=per_source,
                                  broaden=False)
        if not res.get("ok"):
            continue
        for it in res.get("items", []):
            if not _relevant(it.get("text"), terms):
                continue
            outlet = it.get("source")  # the outlet Bing actually returned
            it = dict(it)
            it["tier"] = "media"          # NEVER the body's tier — see docstring
            it["covers_body"] = body
            it["body_tier"] = source.get("tier")
            it["country"] = source.get("country")
            it["pillars"] = source.get("pillars", [])
            it["retrieval"] = "bing_news_relay"
            it["relayed_by"] = outlet
            # country + pillars are set here (after make_item) — re-enrich so the
            # relay item gets local time and tactical target/impact hints too.
            enrich_item(it)
            it["relay_note"] = (
                f"MEDIA article from {outlet}, found by querying for {body}. "
                f"This is NOT a {body} statement and NOT official-tier. If the "
                f"article reports one, attribute it as '{body}, per {outlet}' "
                f"and cite this link."
            )
            out.append(it)
    return out


def _fetch_one(source: dict, per_source: int) -> list:
    """Route a single source dict to its transport. Never raises — a dead source
    yields an empty list so one failure can't sink the batch."""
    access = source.get("access")
    try:
        if access == "rss":
            res = fetch_rss(source["url"], source, limit=per_source)
            return res.get("items", []) if res.get("ok") else []
        if access == "telegram":
            handle = source.get("telegram")
            if not handle:
                return []
            # API when configured+logged-in, web preview otherwise. The API also
            # covers channels whose preview is disabled (Tasnimnews, newsil2022).
            res = fetch_telegram_best(handle, source, limit=per_source)
            return res.get("items", []) if res.get("ok") else []
        if access == "bing":
            return _fetch_bing_source(source, per_source)
        return []
    except Exception:
        return []


def _fetch_x_batch(x_sources: list, per_source: int) -> tuple:
    """Fetch ALL selected X accounts in one batched query (cost discipline:
    pay-per-use bills per post delivered). Returns (items, hit, empty)."""
    handles = [s["x_handle"] for s in x_sources if s.get("x_handle")]
    if not handles:
        return [], [], []
    meta_by_handle = {s["x_handle"]: s for s in x_sources if s.get("x_handle")}
    res = fetch_x(handles, meta_by_handle,
                  limit=max(10, per_source * len(handles)))
    if not res.get("ok") or not res.get("items"):
        # Not configured / rate-limited / genuinely empty — all report as empty
        # rather than silently vanishing, so the model can say X wasn't read.
        return [], [], [s.get("name") for s in x_sources]
    got_handles = {(it.get("x_handle") or "").lower() for it in res["items"]}
    hit = [s.get("name") for s in x_sources
           if (s.get("x_handle") or "").lower() in got_handles]
    empty = [s.get("name") for s in x_sources
             if (s.get("x_handle") or "").lower() not in got_handles]
    return res["items"], hit, empty


def collect_items(sources: list, per_source: int = 6, max_workers: int = 8) -> dict:
    """Fetch every source concurrently and merge into one normalized feed.

    Returns:
        {"item_count", "source_count", "items": [...newest first...],
         "sources_hit": [names], "sources_empty": [names], "x_status"}
    """
    items: list = []
    hit, empty = [], []

    # on_demand_only sources (Iran X accounts) are EXCLUDED from auto-batch
    # collection — the transport ladder reserves them for explicit get_x_account
    # calls so routine queries never spend X credits on saturated actors.
    fetchable = [s for s in sources if s.get("access") in FETCHABLE
                 and not s.get("on_demand_only")]
    not_fetchable = [s.get("name") for s in sources
                     if s.get("access") not in FETCHABLE
                     and not s.get("on_demand_only")]

    # X goes in ONE batched call; everything else fans out concurrently.
    x_sources = [s for s in fetchable if s.get("access") == "x"]
    rest = [s for s in fetchable if s.get("access") != "x"]

    x_status = None
    if x_sources:
        x_items, x_hit, x_empty = _fetch_x_batch(x_sources, per_source)
        items.extend(x_items)
        hit.extend(x_hit)
        empty.extend(x_empty)
        x_status = "read" if x_items else "not_configured_or_empty"

    if rest:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_fetch_one, s, per_source): s for s in rest}
            for fut in as_completed(futures):
                s = futures[fut]
                got = fut.result()
                if got:
                    items.extend(got)
                    hit.append(s.get("name"))
                else:
                    empty.append(s.get("name"))

    items = sort_by_time(dedupe_items(items))
    return {"item_count": len(items), "source_count": len(hit),
            "items": items, "sources_hit": hit,
            "sources_empty": empty + not_fetchable,
            "x_status": x_status}
