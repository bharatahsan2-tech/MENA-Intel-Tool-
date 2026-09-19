"""Media reporting + official-fallback connector — Bing News RSS (free, no key).

Two jobs:
1. MEDIA layer — journalists' reporting, cited separately from official data.
2. Verified FALLBACK for officials that block scripted access (CENTCOM, DoD,
   NCEMA, KUNA, BNA, INCD...) — their statements reach the wires within minutes;
   a simple Bing query surfaces them with direct article links (verified
   2026-07-16: "NCEMA confirms stability...", "CENTCOM announces second wave...").

RULE (from Phase-0 testing): keep queries SIMPLE — 1-3 words plus a country or
agency name. Complex boolean queries degrade to noise (verified: a boolean
Bahrain query returned horoscopes). Bing was chosen over Google News because
Google encrypts article URLs; Bing exposes the real link.

No `from __future__ import annotations` — Gemini AFC needs runtime signatures.
"""
import urllib.parse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import requests

from ..config import BROWSER_HEADERS, HTTP_TIMEOUT
from ._common import make_item

_SEARCH_URL = "https://www.bing.com/news/search"
_FRESHNESS = {"day": "7", "week": "8", "month": "9"}
_MAX_AGE = {"day": timedelta(hours=27), "week": timedelta(days=8),
            "month": timedelta(days=32)}

# Aggregators / syndication mirrors are not acceptable citations.
# Also blocked: user-generated blog platforms that ride a credible outlet's
# domain. blogs.timesofisrael.com is ToI's open blogging platform, NOT its
# newsroom — it surfaced an opinion post as cyber intel (verified 2026-07-16).
# The media tier is credible NAMED outlets with editorial standards; a reader
# seeing "timesofisrael.com" would wrongly assume editorial review.
_BLOCKED_DOMAINS = (
    # aggregators / syndication mirrors
    "msn.com", "aol.com", "yahoo.com", "bing.com", "news.google.com",
    "flipboard.com", "newsbreak.com", "smartnews.com", "ground.news",
    # user-generated blog platforms riding a real outlet's domain
    "blogs.timesofisrael.com", "medium.com", "substack.com",
    # state propaganda — not credible newsrooms, dropped even in annotate mode
    "rt.com", "tass.com", "sputnikglobe.com", "sputniknews.com",
    "globaltimes.cn", "presstv.co.uk",
)


def _blocked(url):
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in _BLOCKED_DOMAINS)


def _direct_url(link):
    if "apiclick" in link:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
        target = q.get("url", [None])[0]
        if target:
            return target
    return link


def _outlet(url):
    host = urllib.parse.urlparse(url).netloc
    return host[4:] if host.startswith("www.") else host


# CREDIBLE outlets = the "vetted" FLAG, not a hard filter (revised 2026-09-19).
# History: 2026-07-21 this hard-DROPPED anything non-West/MENA — but on
# 2026-09-19 that silently hid a major Riyadh missile strike, because the
# breaking Gulf coverage was ALL Indian live-blogs (News18, Livemint, The Week)
# while the Western wires lagged. So get_media_reporting no longer drops on
# credibility; it KEEPS credible newsrooms globally and marks each outlet_vetted
# true/false. Only genuine JUNK is still dropped (see _BLOCKED_DOMAINS:
# aggregators, blog platforms, state propaganda). The list below is broad on
# purpose — India covers this war heavily and credibly; add any real newsroom.
_CREDIBLE_OUTLETS = (
    # --- WEST: wires + US/UK/EU/AU majors ---
    "reuters.com", "apnews.com", "afp.com", "upi.com", "bbc.com", "bbc.co.uk",
    "theguardian.com", "ft.com", "wsj.com", "nytimes.com", "telegraph.co.uk",
    "washingtonpost.com", "cnn.com", "cbsnews.com", "nbcnews.com",
    "abcnews.go.com", "foxnews.com", "cnbc.com", "thehill.com", "newsweek.com",
    "time.com", "usatoday.com", "latimes.com", "npr.org", "pbs.org",
    "politico.com", "axios.com", "bloomberg.com", "economist.com",
    "independent.co.uk", "thetimes.co.uk", "sky.com", "standard.co.uk",
    "france24.com", "dw.com", "euronews.com", "lemonde.fr", "spiegel.de",
    "elpais.com", "theatlantic.com", "abc.net.au",
    # West defense/security specialists
    "defensenews.com", "breakingdefense.com", "janes.com", "warontherocks.com",
    "stripes.com", "militarytimes.com", "thewarzone.com",
    # --- MIDDLE EAST: pan-regional + Gulf + Israel + Turkey ---
    "aljazeera.com", "alarabiya.net", "alhurra.com", "middleeasteye.net",
    "al-monitor.com", "amwaj.media", "aawsat.com", "thenationalnews.com",
    "iranintl.com", "radiofarda.com", "rferl.org",
    "aa.com.tr", "trtworld.com",  # Turkey (regional wires)
    "arabnews.com", "gulfnews.com", "khaleejtimes.com", "arabtimesonline.com",
    "thepeninsulaqatar.com", "gulf-times.com", "kuna.net.kw", "wam.ae",
    "bna.bh", "spa.gov.sa", "qna.org.qa", "zawya.com", "newsofbahrain.com",
    "timesofisrael.com", "jpost.com", "haaretz.com", "ynetnews.com",
    "israelnationalnews.com", "i24news.tv",
    # --- INDIA + other major international newsrooms (heavy MENA-war coverage) ---
    "timesofindia.indiatimes.com", "hindustantimes.com", "indianexpress.com",
    "ndtv.com", "livemint.com", "thehindu.com", "news18.com",
    "business-standard.com", "firstpost.com", "cnbctv18.com", "theprint.in",
    "wionews.com", "deccanherald.com", "theweek.in", "moneycontrol.com",
    "scmp.com", "straitstimes.com", "japantimes.co.jp", "aljazeera.net",
    # --- GLOBAL SPECIALIST AUTHORITIES on this beat (topic, not geography) ---
    # maritime
    "lloydslist.com", "tradewindsnews.com", "maritime-executive.com",
    "navalnews.com", "gcaptain.com", "seatrade-maritime.com",
    # energy (strait/tanker economics)
    "argusmedia.com", "spglobal.com", "rigzone.com", "oilprice.com",
    # cyber
    "therecord.media", "bleepingcomputer.com", "thehackernews.com",
    "securityweek.com", "netblocks.org", "darkreading.com", "wired.com",
)


def _credible(url):
    """True if the outlet is a credible West/MENA newsroom or specialist desk.
    Blocked domains never qualify (a blog platform must not inherit its parent
    domain's credibility: blogs.timesofisrael.com endswith .timesofisrael.com)."""
    if _blocked(url):
        return False
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in _CREDIBLE_OUTLETS)


# Auto-broadening: Bing matches the query against the HEADLINE, which usually
# names only the country, not the town — so "Yanbu Jazan" misses a Reuters piece
# headlined "missiles over Saudi" that "Saudi missiles" finds (verified
# 2026-07-25). When a query names a place/country, we ALSO run broad
# country+event companion queries so headline-only-country coverage is caught.
# This is code-enforced because prompt guidance to "broaden" was not reliably
# followed by the model.
_PLACE_TO_COUNTRY = {
    "saudi": "Saudi", "jazan": "Saudi", "yanbu": "Saudi", "riyadh": "Saudi",
    "jeddah": "Saudi", "dammam": "Saudi", "abha": "Saudi", "najran": "Saudi",
    "bahrain": "Bahrain", "manama": "Bahrain",
    "uae": "UAE", "emirates": "UAE", "dubai": "UAE", "abu dhabi": "UAE",
    "qatar": "Qatar", "doha": "Qatar", "udeid": "Qatar",
    "kuwait": "Kuwait", "oman": "Oman", "muscat": "Oman",
    "jordan": "Jordan", "amman": "Jordan",
    "israel": "Israel", "tel aviv": "Israel", "haifa": "Israel",
    "iran": "Iran", "tehran": "Iran", "bandar abbas": "Iran",
    "iraq": "Iraq", "baghdad": "Iraq", "erbil": "Iraq", "ain al-asad": "Iraq",
    "lebanon": "Lebanon", "beirut": "Lebanon",
    "yemen": "Yemen", "houthi": "Yemen", "sanaa": "Yemen", "hodeidah": "Yemen",
    "hormuz": "Strait of Hormuz", "red sea": "Red Sea",
}


def _detect_country(query):
    """Return the broad country/region term for a query, or None."""
    low = f" {query.lower()} "
    for place, country in _PLACE_TO_COUNTRY.items():
        if place in low:
            return country
    return None


def get_media_reporting(query: str, freshness: str = "day",
                        max_items: int = 6, broaden: bool = True) -> dict:
    """Search recent NEWS ARTICLES about the conflict (media layer, UNOFFICIAL).

    Also the fallback for official bodies that block direct access (CENTCOM, US
    DoD, NCEMA, KUNA, Bahrain BNA/MOI, INCD): a simple query surfaces their
    statements via wire coverage with direct links.

    RULES: present media under a separate "unofficial / media" section, cite
    each as outlet + publish date + direct link, never let it drive a
    "confirmed" status. Aggregators and undated/stale items are filtered here.
    If count == 0, say so — do not substitute weaker sources.

    Args:
        query: SIMPLE terms — 1-3 words + a place/agency, e.g. "CENTCOM",
            "Bahrain sirens", "Strait of Hormuz tanker", "NCEMA UAE".
        freshness: "day" (default — war moves fast), "week", or "month".
        max_items: max articles.

    Returns:
        {"ok": True, "query", "count", "retrieved_utc", "excluded",
         "items": [normalized item with tier="media"...]}
    """
    fresh_key = freshness.strip().lower()
    interval = _FRESHNESS.get(fresh_key, "7")
    max_age = _MAX_AGE.get(fresh_key, _MAX_AGE["day"])
    now = datetime.now(timezone.utc)
    excluded = {"aggregator": 0, "stale_or_undated": 0, "non_vetted_kept": 0}

    def _run(q, cap):
        """Fetch + filter one Bing query. Mutates `excluded`; returns items."""
        params = {"q": q, "format": "rss", "qft": f'interval="{interval}"'}
        try:
            resp = requests.get(_SEARCH_URL, params=params,
                                headers=BROWSER_HEADERS, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except (requests.RequestException, ET.ParseError):
            return []
        got = []
        for it in root.findall(".//item"):
            url = _direct_url(it.findtext("link") or "")
            if not url or _blocked(url):  # junk only: aggregators/blogs/propaganda
                excluded["aggregator"] += 1
                continue
            pub_raw = it.findtext("pubDate")
            try:
                pub = parsedate_to_datetime(pub_raw)
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                pub = None
            if pub is None or now - pub > max_age:
                excluded["stale_or_undated"] += 1
                continue
            # ANNOTATE, don't drop: credible non-West/MENA newsrooms (e.g. Indian
            # live-blogs first on a Gulf strike) are KEPT and flagged, so a major
            # event is never hidden for lack of a Western wire.
            vetted = _credible(url)
            if not vetted:
                excluded["non_vetted_kept"] += 1
            got.append(make_item(
                source=_outlet(url) or "unknown", tier="media",
                text=(it.findtext("title") or "").strip(),
                timestamp_utc=pub.astimezone(timezone.utc).isoformat(),
                timestamp_raw=pub_raw, link=url, source_url=url,
                extra={"outlet_vetted": vetted}))
            if len(got) >= cap:
                break
        return got

    # Primary query, then broad country+event companions so headline-only-country
    # coverage isn't missed (see _detect_country note). Companions are cheap (Bing
    # RSS is free); broaden=False on the official-relay path keeps it targeted.
    queries = [query]
    items = _run(query, max_items * 2)
    if broaden:
        country = _detect_country(query)
        if country:
            for comp in (f"{country} missiles", f"{country} air defense",
                         f"{country} strike"):
                if comp.lower() != query.strip().lower():
                    items += _run(comp, max_items)
                    queries.append(comp)

    # Merge + dedupe by URL, newest first, then cap.
    seen, uniq = set(), []
    for it in sorted(items, key=lambda i: i.get("timestamp_utc") or "", reverse=True):
        u = it.get("link")
        if u and u not in seen:
            seen.add(u)
            uniq.append(it)
    uniq = uniq[:max_items + (4 if broaden else 0)]
    return {
        "ok": True,
        "source": "Bing News (media — UNOFFICIAL)",
        "query": query,
        "queries_run": queries,
        "count": len(uniq),
        "vetted_count": sum(1 for i in uniq if i.get("outlet_vetted")),
        "excluded": excluded,
        "retrieved_utc": now.isoformat(),
        "items": uniq,
        "note": ("MEDIA reports/claims — corroboration, not confirmation. Broad "
                 "country+event companion queries were auto-run so a report "
                 "headlined by country (not town) isn't missed. Junk "
                 "(aggregators, blogs, state propaganda) is dropped; everything "
                 "else is KEPT with outlet_vetted true/false. Lead with "
                 "outlet_vetted=true, but for a BREAKING event STILL report what "
                 "outlet_vetted=false credible outlets (e.g. Indian live-blogs) "
                 "carry — flag them as not-yet-in-a-Western-wire, never withhold "
                 "a major strike for lack of one. count=0 -> 'no media retrieved'."),
    }


if __name__ == "__main__":
    r = get_media_reporting("CENTCOM", freshness="week", max_items=5)
    print(r["ok"], r.get("count"), r.get("excluded"))
    for it in r.get("items", []):
        print("  ", it["source"], "|", (it["text"] or "")[:60], "|", it["timestamp_raw"])
