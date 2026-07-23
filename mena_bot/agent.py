"""Gemini tool-use loop for the MENA conflict monitor.

Tools are organized by SOURCE TIER (official / semi-official / media) so
the model can naturally assemble the required tier-ordered output and corroborate
a claim across RSS, Telegram, and (later) X. Each tool returns normalized items
already tagged with tier + source + timestamp + link + location.

ISAAC lessons applied: NO `from __future__ import annotations` (breaks google-genai
automatic function calling); tool signatures use plain runtime types; the client
is held on an object so its HTTP transport isn't garbage-collected; source-line
links are guaranteed in code, not left to the model.
"""
from google import genai
from google.genai import types

from .collect import FETCHABLE, collect_items
from .config import GEMINI_MODEL, require_gemini_key, x_configured
from .connectors.geocode import geocode_place
from .connectors.news import get_media_reporting
from .connectors.tzevaadom import get_israel_alerts
from .connectors.telegram_api import fetch_telegram_best, search_channels
from .connectors.x_api import fetch_x
from .data_loader import load_sources
from .prompt import SYSTEM_PROMPT
from .sources import find_sources, select_sources, resolve_country
from .connectors._common import now_utc_iso


def _collect_tier(country=None, tier=None, pillar=None, group=None, per_source=5):
    """Shared body for the tier tools: select -> collect -> compact result."""
    selected = select_sources(country=country, tier=tier, pillar=pillar, group=group)
    result = collect_items(selected, per_source=per_source)
    # Sources with no collectable transport (Tzeva Adom has its own tool) — the
    # model can still cite them by link.
    link_only = [{"name": s["name"], "access": s.get("access"),
                  "url": s.get("url"),
                  "dedicated_tool": (s.get("connector") or {}).get("dedicated_tool")}
                 for s in selected if s.get("access") not in FETCHABLE]
    items = result["items"]
    # A DIRECT item is the body's own words (its feed/Telegram/X). A RELAY item
    # is a media article found by querying for that body — it is media, and may
    # only REPORT an official statement. Keeping this split visible is what
    # stops "no direct official line exists" from being quietly papered over.
    direct = [i for i in items if i.get("retrieval") != "bing_news_relay"]
    relay = [i for i in items if i.get("retrieval") == "bing_news_relay"]
    direct_official = [i for i in direct if i.get("tier") == "official"]
    return {
        "ok": True,
        "tier": tier,
        "country": resolve_country(country) if country else None,
        "pillar": pillar,
        "retrieved_utc": now_utc_iso(),
        "count": len(items),
        "direct_count": len(direct),
        "direct_official_count": len(direct_official),
        "relay_count": len(relay),
        "items": items,
        "sources_hit": result["sources_hit"],
        "sources_empty": result["sources_empty"],
        "x_configured": x_configured(),
        "x_status": result.get("x_status"),
        "link_only_sources": link_only,
        "relay_note": (
            "Items with retrieval='bing_news_relay' are tier='media' — an "
            "article from relayed_by, found by querying for covers_body. They "
            "are NOT official statements. If such an article reports an "
            "official statement, attribute it '<covers_body>, per <relayed_by>' "
            "and cite the article link. If direct_official_count==0, then NO "
            "direct official statement was retrieved for this country — say "
            "that plainly in the OFFICIAL section; it is real intel, not a gap "
            "to fill with media."
        ),
    }


# ----------------------------- TOOLS ------------------------------------

def get_official_reporting(country: str) -> dict:
    """OFFICIAL-tier reporting for a country/actor (governments, militaries,
    ministries, state news agencies). ALWAYS pull this first — official sources
    from both sides lead every answer.

    Every country is now covered by a working transport: Iran via its own feeds
    (IRNA, Press TV) and X; Israel via the IDF channel; the Gulf states, US
    CENTCOM/DoD, Iraq, Lebanon and Jordan via Bing news relay of their state
    agencies (WAM, SPA, BNA, KUNA, QNA, ONA, Petra, NNA, INA). Relayed items are
    marked retrieval='bing_news_relay' — cite them as the body's statement VIA
    the named outlet, not as a direct fetch.

    For Israel SIRENS use get_israel_alerts instead — this tool does not carry
    live alert data.

    Args:
        country: "iran", "us", "uae", "saudi", "bahrain", "kuwait", "qatar",
            "oman", "jordan", "lebanon", "iraq", "israel", "houthi".
    """
    return _collect_tier(country=country, tier="official")


# Semi-official is the ATTACKER/CLAIMANT layer — it exists ONLY for the actors
# making the strikes/blockade claims. In this conflict that is Iran and the
# Houthis. Defenders (Gulf states, Israel, Jordan) speak officially or not at
# all; forcing a semi-official section for them invents a tier. Enforced in code
# so the model cannot pull one no matter what the prompt has drifted to.
_SEMI_OFFICIAL_ACTORS = {"IRAN", "YEMEN_HOUTHIS"}


def get_semi_official_reporting(actor: str) -> dict:
    """SEMI-OFFICIAL reporting — the ATTACKER-CLAIM layer. IRGC-linked and
    Houthi-linked outlets where strike/blockade claims surface FIRST, BEFORE any
    official confirmation. These are CLAIMS, not confirmation.

    EXISTS ONLY FOR IRAN AND THE HOUTHIS. There is no semi-official tier for the
    Gulf states, Israel, or Jordan — for those, use get_official_reporting +
    get_media_reporting and do NOT create a semi-official section. This is
    enforced here: any other actor returns count=0 with an explanatory note.

    Args:
        actor: "iran" or "houthi" (also "yemen").
    """
    resolved = resolve_country(actor)
    if resolved not in _SEMI_OFFICIAL_ACTORS:
        return {
            "ok": True, "tier": "semi_official", "actor": actor,
            "resolved": resolved, "count": 0, "items": [],
            "enforced_empty": True,
            "note": (f"NO semi-official tier exists for '{actor}'. Semi-official "
                     "is the attacker-claim layer — ONLY Iran and the Houthis. "
                     f"For {actor}, report OFFICIAL + MEDIA only and OMIT the "
                     "semi-official section entirely; do not invent one."),
        }
    return _collect_tier(country=actor, tier="semi_official")


def get_maritime_reporting() -> dict:
    """MARITIME / Strait of Hormuz reporting — vessel strikes, strait closure or
    blockade, and strait-management developments (e.g. Iran-Oman talks).

    Pulls the authoritative advisory bodies UKMTO and US MARAD (MSCI). UKMTO is
    the primary incident line — vessel attacked, position, time. It posts to X
    first, so its X account is included when X is configured; otherwise UKMTO
    comes through Bing relay with more lag.

    Also run get_media_reporting("Strait of Hormuz") for wire coverage, and
    get_official_reporting("oman")/("iran") for the diplomatic strait track.
    """
    return _collect_tier(group="maritime", per_source=6)


def get_cyber_reporting() -> dict:
    """CYBER-pillar reporting — cyberattacks, and threats to fiber-optic cables
    and data centers.

    Pulls NetBlocks (connectivity outages, submarine-cable cuts, data-center/ISP
    disruption — the core source for the cable/data-center requirement) plus the
    national cyber authorities INCD (Israel) and aeCERT (UAE), all via Bing
    relay. For a live connectivity check also run
    get_media_reporting("Iran internet outage") or ("submarine cable").

    There are NO hacktivist claim channels here — those were removed as not
    credible. Attack claims by hacktivist groups reach you through the media
    tier only, and are CLAIMS.
    """
    return _collect_tier(group="cyber", per_source=5)


def get_telegram_channel(channel: str) -> dict:
    """Read recent messages from ONE public Telegram channel by handle — the
    vetted OFFICIAL/SEMI-OFFICIAL actor channels: "army21ye" (Houthi military
    media, official-for-Houthis — Yahya Saree strike claims), "idfofficial"
    (IDF), "Tasnimnews"/"farsna" (Iranian IRGC-linked, where strike claims
    surface first). Returns messages with their own timestamps and t.me
    permalinks. These are primary-source CLAIMS, not media — treat accordingly.
    Non-English channels: translate, keep the original in parentheses. Watch for
    stale timestamps — some channels post in bursts.

    Args:
        channel: the handle without @, e.g. "army21ye", "idfofficial".
    """
    # Resolve the handle to its vetted tier/meta if it's in the directory
    # (army21ye -> official, Tasnimnews -> semi_official). Unknown handles are
    # labeled "unverified" so the model treats them as uncorroborated.
    meta = None
    for s in select_sources():
        if (s.get("telegram") or "").lower() == channel.lower():
            meta = s
            break
    if meta is None:
        meta = {"name": f"Telegram/{channel}", "tier": "unverified"}
    return fetch_telegram_best(channel, meta, limit=10)


def search_telegram(query_en: str, query_fa: str = "", query_ar: str = "",
                    query_he: str = "", country: str = "", hours: int = 48) -> dict:
    """SEARCH the vetted Telegram channels by keyword — use this for ANY question
    about a specific topic, place, weapon, vessel or event, and for ANYTHING
    older than the last few minutes.

    WHY THIS EXISTS: the get_*_reporting tools return only the NEWEST posts, and
    these channels post 150-380 times a day — so that is barely ~15 MINUTES of
    coverage on the busy Iranian/Houthi channels. This tool searches back across
    hours/days instead.

    YOU MUST SUPPLY NATIVE-LANGUAGE TERMS. Telegram search is LITERAL, not
    semantic: "Bahrain" returns ZERO from the Farsi channels while "بحرین"
    returns hits. Each channel is searched in its OWN language, so if you omit
    query_fa/query_ar/query_he you will silently miss Iran's IRGC-linked, the
    Houthi, and the Israeli channels — your best primary sources. Always
    translate the key term yourself into Farsi, Arabic and Hebrew.
    Use ONE keyword per language (a place, weapon or actor), not a sentence.

    Args:
        query_en: the key term in English, e.g. "Hormuz".
        query_fa: same term in Farsi, e.g. "هرمز" (for Iranian channels).
        query_ar: same term in Arabic, e.g. "هرمز" (Houthi/Iraqi channels).
        query_he: same term in Hebrew (Israeli channels).
        country: optional filter — "iran", "israel", "houthi", "iraq".
        hours: how far back to search (default 48).

    Returns:
        {"ok", "count", "items": [...], "searched": [{channel, language, term}],
         "no_term_for": [channels skipped for lack of a term in their language]}
        `no_term_for` is a BLIND SPOT — say so if it is non-empty.
    """
    terms = {"en": query_en, "fa": query_fa, "ar": query_ar, "he": query_he}
    sources = [s for s in select_sources(country=country or None)
               if s.get("access") == "telegram"]
    return search_channels(sources, terms, hours=hours)


def get_x_account(handle: str, hours: int = 0) -> dict:
    """Read posts from ONE X (Twitter) account by handle — the vetted official/
    media accounts: "CENTCOM", "UK_MTO" (maritime incidents), "NCEMAUAE" and
    "moi_bahrain" (X-first Gulf alert authorities), "IDF", "IrnaEnglish"/
    "IRIMFA_EN" (Iran), "BarakRavid" (Axios diplomacy).

    COUNTING / ENUMERATION: to answer "how many times did X happen in the last N
    hours" (e.g. Bahrain siren activations, which live on moi_bahrain), set
    hours=N. That pulls the FULL window (up to 100 posts) instead of just the
    latest few, so you can enumerate and count every event. If the result has
    truncated=true, the window had MORE posts than were returned — say the count
    is a lower bound, do not present it as exact.

    X REQUIRES A TOKEN. If configured=false, X was NOT read — say so and use
    get_official_reporting / Telegram instead. Never present X you did not fetch.

    Args:
        handle: the handle without @, e.g. "moi_bahrain", "UK_MTO".
        hours: 0 for the latest posts (default); N to cover the last N hours for
            counting/enumeration.
    """
    meta = None
    for s in select_sources():
        if (s.get("x_handle") or "").lower() == handle.lstrip("@").lower():
            meta = s
            break
    key = handle.lstrip("@")
    meta_map = {key: meta} if meta else {}
    # For a windowed count, request the full page (100) so the whole window is
    # covered; otherwise keep it cheap at 10.
    limit = 100 if hours and hours > 0 else 10
    return fetch_x([key], meta_map, limit=limit, since_hours=hours or 0)


# Israel alerts, media, geocode, find_sources imported directly as tools.

TOOLS = [
    geocode_place,
    get_official_reporting,
    get_semi_official_reporting,
    get_israel_alerts,
    get_maritime_reporting,
    get_cyber_reporting,
    get_media_reporting,
    get_telegram_channel,
    search_telegram,
    get_x_account,
    find_sources,
]


# ---------------- source-line link guarantee (code, not prompt) -------------

def _build_link_map():
    """Agency/source name -> portal URL, built from sources.json so it stays in
    sync, plus a few high-value aliases. Longest names first (specific wins)."""
    # Manual aliases FIRST so their clean portal URLs win over raw feed/api URLs
    # when the same name is generated from sources.json (dedupe keeps first).
    pairs = [
        ("Home Front Command", "https://www.oref.org.il/"),
        ("Pikud HaOref", "https://www.oref.org.il/"),
        ("CENTCOM", "https://www.centcom.mil/"),
        ("US Central Command", "https://www.centcom.mil/"),
        ("IRGC", "https://en.irna.ir/"),
        ("Iranian Armed Forces", "https://en.irna.ir/"),
        ("UKMTO", "https://www.ukmto.org/"),
        ("NetBlocks", "https://netblocks.org/"),
        ("Yemeni Armed Forces", "https://t.me/s/army21ye"),
    ]
    data = load_sources()
    groups = list(data.get("countries", {}).values()) + [
        data.get(g, []) for g in ("maritime", "cyber", "media")]
    for grp in groups:
        for s in grp:
            url = s.get("source_url") or s.get("url")
            name = s.get("name", "")
            if not url or not name:
                continue
            # Use the pre-dash short name ("IRNA ... — English" -> "IRNA ...").
            short = name.split("—")[0].split("(")[0].strip()
            pairs.append((short, url))
            pairs.append((name, url))
    # De-dupe, keep first (manual wins); sort longest-first so a specific name
    # matches before a short one on the same line.
    seen, out = set(), []
    for name, url in pairs:
        if name and name not in seen:
            seen.add(name)
            out.append((name, url))
    out.sort(key=lambda p: len(p[0]), reverse=True)
    return out


_SOURCE_LINK_MAP = _build_link_map()


def _linkify_sources(text: str) -> str:
    """Guarantee source-citation lines carry a link. For any line mentioning a
    source ("Source", "Sources", "data as of") with no markdown link yet, wrap
    the first known agency name in a link to its portal."""
    out = []
    for line in text.splitlines():
        low = line.lower()
        if ("source" in low or "data as of" in low) and "](" not in line:
            for name, url in _SOURCE_LINK_MAP:
                idx = line.find(name)
                if idx != -1:
                    line = f"{line[:idx]}[{name}]({url}){line[idx + len(name):]}"
                    break
        out.append(line)
    return "\n".join(out)


class MenaChat:
    """Holds BOTH the client and the chat session (see ISAAC: a local client gets
    garbage-collected, closing the HTTP transport mid-conversation)."""

    def __init__(self):
        self._client = genai.Client(api_key=require_gemini_key())
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=TOOLS,
            temperature=0.2,  # intel work: precision over creativity
        )
        self._chat = self._client.chats.create(model=GEMINI_MODEL, config=config)

    def send(self, user_message: str) -> str:
        resp = self._chat.send_message(user_message)
        return _linkify_sources(resp.text or "(no text returned)")


def build_chat() -> MenaChat:
    return MenaChat()


def ask(chat: MenaChat, user_message: str) -> str:
    return chat.send(user_message)
