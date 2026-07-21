"""Generic RSS/Atom connector — normalizes any feed into intel items.

Drives every feed source in sources.json (IRNA, Press TV, Mehr, State Dept
advisories, Naval News, The Record, BleepingComputer, Al Jazeera, Times of
Israel, Axios). One parser, parameterized by the source metadata, so adding a
feed to sources.json needs no new code.
"""
from xml.etree import ElementTree as ET

import requests

from ._common import clean_html, fetch, make_item, to_utc_iso

# Atom namespace (RSS 2.0 needs none).
_ATOM = "{http://www.w3.org/2005/Atom}"


def _first_text(el, *tags):
    for tag in tags:
        child = el.find(tag)
        if child is not None and child.text:
            return child.text.strip()
    return None


def _atom_link(entry):
    # Prefer rel="alternate"; fall back to the first link with an href.
    best = None
    for link in entry.findall(f"{_ATOM}link"):
        href = link.get("href")
        if not href:
            continue
        if link.get("rel") in (None, "alternate"):
            return href
        best = best or href
    return best


def fetch_rss(url, source_meta, limit=8):
    """Fetch and normalize one RSS/Atom feed.

    Args:
        url: Feed URL.
        source_meta: the source dict from sources.json (name, tier, country,
            pillars, language, source_url) used to tag every item.
        limit: max items to return (newest as the feed orders them).

    Returns:
        {"ok": bool, "source", "count", "items": [normalized item...], "error"?}
    """
    name = source_meta.get("name", url)
    tier = source_meta.get("tier", "media")
    country = source_meta.get("country")
    pillars = source_meta.get("pillars", [])
    language = source_meta.get("language", "en")
    source_url = source_meta.get("source_url") or source_meta.get("url")

    try:
        resp = fetch(url)
        if resp.status_code != 200:
            return {"ok": False, "source": name,
                    "error": f"HTTP {resp.status_code} for {url}"}
        root = ET.fromstring(resp.content)
    except requests.RequestException as e:
        return {"ok": False, "source": name, "error": f"request failed: {e}"}
    except ET.ParseError as e:
        return {"ok": False, "source": name, "error": f"feed parse error: {e}"}

    items = []
    # RSS 2.0: channel/item ; Atom: feed/entry
    entries = root.findall(".//item") or root.findall(f".//{_ATOM}entry")
    for entry in entries[:limit]:
        title = _first_text(entry, "title", f"{_ATOM}title") or ""
        desc = _first_text(entry, "description", "summary",
                           f"{_ATOM}summary", f"{_ATOM}content")
        raw_date = _first_text(entry, "pubDate", "published", "updated",
                               f"{_ATOM}published", f"{_ATOM}updated",
                               "{http://purl.org/dc/elements/1.1/}date")
        link = _first_text(entry, "link") or _atom_link(entry)
        text = title
        clean_desc = clean_html(desc)
        if clean_desc and clean_desc.lower() not in title.lower():
            text = f"{title} — {clean_desc}" if title else clean_desc
        items.append(make_item(
            source=name, tier=tier, country=country, pillars=pillars,
            language=language, text=text[:600],
            timestamp_utc=to_utc_iso(raw_date), timestamp_raw=raw_date,
            link=link, source_url=source_url,
        ))

    return {"ok": True, "source": name, "source_url": source_url,
            "count": len(items), "items": items}


if __name__ == "__main__":
    meta = {"name": "IRNA", "tier": "official", "country": "IRAN",
            "pillars": ["tactical"], "language": "en",
            "source_url": "https://en.irna.ir/"}
    r = fetch_rss("https://en.irna.ir/rss", meta, limit=3)
    print(r["ok"], r.get("count"), r.get("error"))
    for it in r.get("items", []):
        print(" ", it["timestamp_utc"], "|", it["text"][:70])
