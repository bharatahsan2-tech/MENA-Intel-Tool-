"""Source selection over sources.json + a never-dead-end handoff tool.

Every fetch tool selects the sources it should hit from the Phase-0 directory,
so adding/retiring a source is a data edit, not a code change. Selection tags
each source with its country/group so downstream items carry provenance.
"""
from __future__ import annotations

from .data_loader import load_sources

# Actor aliases -> the key used in sources.json["countries"].
_COUNTRY_ALIASES = {
    "iran": "IRAN", "iranian": "IRAN", "tehran": "IRAN",
    "israel": "ISRAEL", "israeli": "ISRAEL", "idf": "ISRAEL",
    "bahrain": "BAHRAIN", "manama": "BAHRAIN",
    "uae": "UAE", "emirates": "UAE", "abu dhabi": "UAE", "dubai": "UAE",
    "saudi": "SAUDI_ARABIA", "saudi arabia": "SAUDI_ARABIA", "ksa": "SAUDI_ARABIA", "riyadh": "SAUDI_ARABIA",
    "qatar": "QATAR", "doha": "QATAR",
    "kuwait": "KUWAIT",
    "oman": "OMAN", "muscat": "OMAN",
    "jordan": "JORDAN", "amman": "JORDAN",
    "lebanon": "LEBANON", "lebanese": "LEBANON", "hezbollah": "LEBANON", "beirut": "LEBANON",
    "iraq": "IRAQ", "iraqi": "IRAQ", "baghdad": "IRAQ",
    "yemen": "YEMEN_HOUTHIS", "houthi": "YEMEN_HOUTHIS", "houthis": "YEMEN_HOUTHIS", "sanaa": "YEMEN_HOUTHIS",
    "us": "US", "usa": "US", "american": "US", "united states": "US",
    "centcom": "US", "pentagon": "US",
}

_GROUPS = ("maritime", "cyber", "media")


def resolve_country(name: str):
    if not name:
        return None
    return _COUNTRY_ALIASES.get(name.strip().lower())


def _tag(source: dict, country: str | None, group: str | None) -> dict:
    """Attach provenance (country/group) without mutating the cached original."""
    s = dict(source)
    if country and not s.get("country"):
        s["country"] = country
    if group:
        s["group"] = group
    return s


def select_sources(country: str | None = None, tier: str | None = None,
                   pillar: str | None = None, group: str | None = None,
                   access: str | None = None, verified_only: bool = False) -> list:
    """Return source dicts from sources.json matching the given filters.

    country: alias or sources.json key (IRAN, UAE, ...). group: one of
    maritime/regional_osint/cyber/media. tier/pillar/access filter within.
    """
    data = load_sources()
    pool: list = []

    ckey = resolve_country(country) if country else None
    if ckey:
        for s in data.get("countries", {}).get(ckey, []):
            pool.append(_tag(s, ckey, None))
    elif group:
        for s in data.get(group, []):
            pool.append(_tag(s, None, group))
    else:
        for ck, lst in data.get("countries", {}).items():
            for s in lst:
                pool.append(_tag(s, ck, None))
        for g in _GROUPS:
            for s in data.get(g, []):
                pool.append(_tag(s, None, g))

    def keep(s: dict) -> bool:
        if tier and s.get("tier") != tier:
            return False
        if pillar and pillar not in (s.get("pillars") or []):
            return False
        if access and s.get("access") != access:
            return False
        if verified_only and not s.get("verified"):
            return False
        return True

    return [s for s in pool if keep(s)]


def find_sources(country: str = "", pillar: str = "", tier: str = "") -> dict:
    """List vetted sources (with links) for a country/pillar/tier — the
    never-dead-end handoff. Use when no live connector covers something, or to
    show the analyst exactly which official/semi-official/media bodies feed a
    topic, and on which transport (telegram / x / bing relay / rss).

    Args:
        country: e.g. "iran", "uae", "houthi", "us" (optional).
        pillar: "tactical" | "maritime" | "strategic" | "cyber" (optional).
        tier: "official" | "semi_official" | "media" (optional).

    Returns:
        {"ok": True, "count", "sources": [{name, tier, access, url, telegram?,
         x_handle?, pillars, language}...]} grouped tier-first for citation.
    """
    matches = select_sources(country=country or None, pillar=pillar or None,
                             tier=tier or None)
    order = {"official": 0, "semi_official": 1, "media": 2}
    matches.sort(key=lambda s: order.get(s.get("tier"), 9))
    out = []
    for s in matches:
        entry = {
            "name": s.get("name"),
            "tier": s.get("tier"),
            "access": s.get("access"),
            "url": s.get("url"),
            "pillars": s.get("pillars"),
            "language": s.get("language"),
            "country": s.get("country"),
        }
        if s.get("telegram"):
            entry["telegram"] = s["telegram"]
        if s.get("x_handle"):
            entry["x_handle"] = s["x_handle"]
            entry["x_handle_verified"] = bool(s.get("verified"))
        out.append(entry)
    return {"ok": True, "count": len(out), "sources": out,
            "note": ("Cite official first, then semi-official, then media. "
                     "access='bing' means that body is read via media relay, "
                     "not fetched directly.")}
