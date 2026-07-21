# MENA / Iran Conflict Intel Monitor

Analyst tool for timely intel on the Iran conflict across the Gulf, Israel, and the region.
Sibling project to the ISAAC weather bot — same stack (Python + free Gemini API).

## Intel pillars

1. **Tactical** — strikes, sirens, interceptions, impacts (country-wise: Bahrain, UAE, Saudi, Oman, Jordan, Israel, Qatar, Kuwait, Iraq, Lebanon + any new), attack method, target type, Israel–Lebanon front.
2. **Maritime / Strait of Hormuz** — vessel strikes, closure/blockade, strait-management developments (Iran–Oman track).
3. **Strategic** — talks, MoU, ceasefire, escalation rhetoric.
4. **Cyber** — cyberattacks, fiber-optic cable and data-center threats, connectivity outages.

Every item: timestamp + location (mandatory), source tier stated. Official sources from both
Iran and target countries always present first, then semi-official, then media.
Explicit all-quiet reporting per country ("no attacks toward X in last 24h").

## Three transports (the 2026-07-16 rebuild)

Intel arrives through **Telegram + X + Bing News RSS** — nothing else.

**Why the rebuild:** the previous directory had 61 sources, but 30 were `access: "web"` while
the dispatcher only ever fetched `rss`/`telegram`. Those 30 — CENTCOM, KUNA, BNA, NCEMA, WAM,
UKMTO, SPA, QNA… — silently returned **zero items on every run**. Official coverage was
structurally empty while 16 standing media RSS feeds generated the noise. Now every source sits
on a transport that actually fetches:

| transport | what it is | who's on it |
|---|---|---|
| `telegram` | `t.me/s/<handle>` public preview, no key | IDF, Houthi military media (army21ye), Tasnim, Fars |
| `x` | X API v2 recent search — **needs `X_BEARER_TOKEN`** | CENTCOM, UKMTO, NCEMA, IDF, Iran state, Reuters/AJE/ToI |
| `bing` | Bing News RSS relay of a body's statements | the Gulf/US/Iraq/Lebanon state agencies, UKMTO, MARAD, CERTs, NetBlocks |
| `rss` | native feed — **kept by exception** | Iran's IRNA + Press TV + Mehr, US State Dept advisories |
| `api` | structured JSON — **kept by exception** | Israel Home Front Command sirens (Tzeva Adom mirror) |

**Kept-by-exception** (deliberate deviations from "Telegram/X/Bing only" — cut them if unwanted):
Iran's own state feeds (their primary voice; relay is slower and loses their framing), the
Israel siren API (the only structured source of sirens/impact/timing and of genuine all-quiet),
and the State Dept advisory feed (one low-noise official feed; EXAMPLES #3 consular items).

### Relay honesty (the rule that keeps the tiers meaningful)

A `bing`-routed item is **media**, never official. Bing returns articles *matching a query*,
not the queried agency's statements — querying "Bahrain interior ministry" returned a Condé
Nast travel piece and a story about Chinese seaborne trade. So relay items stay `tier: "media"`,
record the body in `covers_body`, and the model must attribute them **"<body>, per <outlet>"**.

Two gates protect this, both in `collect.py`:
- **body gate** (`relevance_terms` per source) — the article must mention that body/country.
- **conflict gate** (`_CONFLICT_TERMS`) — and be about the war, or "Youth City 2030 …
  Bahrain's Creative Talent" arrives as tactical intel.

Consequence, by design: for Gulf states with no direct feed, the OFFICIAL section may be
legitimately empty. **"No direct statement from KUNA" is the intel** — not a gap to fill.

### Outlet credibility

Media items carry `outlet_vetted`. Bing indexes whoever published, so this **annotates rather
than filters** (a hard allowlist would drop real regional reporting — Arab Times and The
Peninsula carried genuine Kuwaiti MOD interception detail). `outlet_vetted=false` means not an
established newsroom on this beat — a crypto blog asserting "Iran closes the Strait of Hormuz",
a local US TV station. The model leads with vetted outlets and flags unvetted ones as
uncorroborated single-source claims. Aggregators and blog platforms riding a credible domain
(`blogs.timesofisrael.com`) are blocked outright.

**Tier policy:** the media tier is credible named outlets only. Anonymous OSINT/Telegram
aggregators are **not** used. Actors' own channels — Houthi military media, IDF, Tasnim/Fars —
are kept in the **official/semi-official** tiers as the primary-source claims they are.

## Enabling X

X is optional and inactive until you set a token — everything else runs without it.

```
X_BEARER_TOKEN=...   # in .env
```

**Why it's worth it:** UKMTO (vessel incidents), NCEMA (the UAE alert authority) and CENTCOM
post to X *first*. Without it those bodies reach you only through Bing relay, 15–60 min later.
Cost is per post **read**, so `x_api.py` batches all accounts into one query and excludes
retweets. ⚠️ **Every X handle in `sources.json` is `verified: false` — unprobed, because no
token existed at build time. Confirm each handle on the first live run.**

## Run it

```
pip install -r requirements.txt
cp .env.example .env         # paste your free Gemini key from aistudio.google.com/apikey
python app.py
```

Ask e.g. *"tactical picture for Bahrain in the last 24h"*, *"status of the Strait of Hormuz"*,
*"any ceasefire movement?"*, *"cyber or connectivity threats to the Gulf?"*.

## Structure

- `data/sources.json` — 41 sources, tiered (official / semi_official / media), tagged by pillar,
  country and transport. Per bing source: `bing_queries`, `relevance_terms`, `relay_freshness`.
- `tools/probe_sources.py` — re-runs live source verification.
- `mena_bot/`
  - `connectors/` — `telegram_web` (t.me/s reader, no key), `x_api` (X v2, token-gated),
    `news` (Bing News: media tier + relay transport + outlet vetting), `rss` (feed parser),
    `tzevaadom` (Israel sirens), `geocode` (Open-Meteo), `_common` (fetch + item schema).
  - `sources.py` — selection over sources.json + `find_sources` handoff.
  - `collect.py` — concurrent dispatcher + the relay gates; X batched into one call.
  - `agent.py` — Gemini function-calling loop, 10 tier-organized tools + in-code link guarantee.
  - `prompt.py` — the tier-ordered response contract (location + timestamp mandatory, relay
    attribution, claim discipline, translation, links).
- `app.py` — terminal chat entrypoint.

## Source tiers

- **official** — government/military/ministry/state agency of the named country/actor
- **semi_official** — IRGC-linked, Houthi-linked, state-linked outlets (claims, not statements)
- **media** — established named news outlets (via Bing), annotated with `outlet_vetted`

Semi-official claims never produce "confirmed" status; corroboration state is always explicit.
`relay_freshness` is `day` by default; low-volume advisory bodies (UKMTO, MARAD, NetBlocks,
INCD, aeCERT…) use `week` — verified that `"UKMTO"` returns 0 items at day and 4 real
advisories at week, and an advisory stays operative until withdrawn.

## Phases

- [x] Phase 0 — source directory, live-verified (2026-07-16)
- [x] Transport rebuild — Telegram/X/Bing only; relay gates + outlet vetting; live-verified end-to-end (2026-07-16)
- [x] **Phase 1 — Telegram via API (2026-07-16):** 21 user-supplied channels added (Iran 9 new,
  Israel 6, Iraq 1, Yemen 3 — 23 channels total), all handles probed alive. Telethon connector
  (`connectors/telegram_api.py`), gated on `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` with automatic
  t.me/s/ web-preview fallback. **To activate the API: put both values in `.env`, then run once
  in a terminal: `python tools/telegram_login.py`** (interactive: phone → in-app code → 2FA).
  Until then everything runs on web preview — except `Tasnimnews` and `newsil2022`, whose
  previews are disabled and which need the API login.
- [x] **Phase 2 — X connector (2026-07-20):** `X_BEARER_TOKEN` set; 18 X accounts (17 officials +
  Axios/Barak Ravid), all handles validated via user-lookup. X is last-resort: media stays on Bing,
  Iran X is `on_demand_only`, officials batch per-country. Response contract v2 code-enforced
  (claim_status, semi-official = Iran+Houthis only, dual timestamps, target/impact, specific links).
- [x] **Phase 3 — Streamlit UI (2026-07-21):** `streamlit run streamlit_app.py`. Two tabs — **Ask**
  (on-demand tiered chat) and **Daily 24h digest** (`mena_bot/digest.py`, section-by-section in the
  EXAMPLES format). Sidebar shows transport status + source counts; Gemini key via `.env` or paste.
  Runs on port 8502 (ISAAC weather app uses 8501). Live-verified in-browser.
- [ ] Later — schedule the daily digest (cron); persistent item store + corroboration scoring;
  Telethon fuller history; deploy/share the app.
