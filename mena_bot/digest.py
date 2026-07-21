"""Daily 24-hour intel digest in the analyst's EXAMPLES format.

Runs the SAME tool-driven agent as the chat, but section by section (tactical →
maritime → strategic → cyber) so each call stays bounded — the Telegram channels
alone carry ~2,200 text posts a day, far too many to summarize in one shot. Each
section is a FRESH agent chat: bounded context, lower cost, and pillars are
distinct enough that cross-section dedup isn't needed. The full response contract
(tier order, claim_status, dual timestamps, target type/impact, specific links)
is inherited from the system prompt automatically — the digest adds only the
EXAMPLES-format shaping per section.
"""
from datetime import datetime, timezone

from .agent import ask, build_chat
from .connectors._common import now_utc_iso

# (section_title, instruction) — {hours} is filled at generation time.
DEFAULT_SECTIONS = [
    ("TACTICAL — country by country", """\
Produce the TACTICAL section of a {hours}-hour intelligence digest on the Iran \
conflict. Sweep EVERY country of interest — Israel, Bahrain, Kuwait, Qatar, UAE, \
Saudi Arabia, Oman, Jordan, Iraq (and any other where a strike is reported). \
Use the tools (official first, then Iran/Houthi semi-official claims, then \
media); for Israel also call the sirens tool. For a specific/older event use \
search_telegram with native-language terms.
Organize as one block PER COUNTRY that had activity. For each event give: what \
was targeted (TARGET TYPE), IMPACT, attack method, dual timestamp (local + UTC), \
tier-ordered lines, and a specific source link. For every country with NO \
activity, write the explicit all-quiet line. Do not answer from memory."""),

    ("MARITIME / STRAIT OF HORMUZ", """\
Produce the MARITIME section of a {hours}-hour Iran-conflict digest. Cover vessel \
strikes/seizures, any strait closure or blockade, UKMTO/MARAD advisories, and \
the Iran-Oman strait-management diplomatic track. Use get_maritime_reporting + \
get_media_reporting("Strait of Hormuz") + official reporting for Oman/Iran. \
Every item: location (vessel position/port), dual timestamp, tier, specific \
link. If quiet, say so explicitly. Do not answer from memory."""),

    ("STRATEGIC — talks, ceasefire, MoU, escalation", """\
Produce the STRATEGIC section of a {hours}-hour Iran-conflict digest: \
negotiations, ceasefire/truce movement, MoUs, diplomatic statements, escalation \
rhetoric. Pull official reporting from BOTH Iran (incl. the MFA via \
get_x_account("IRIMFA_EN") if it concerns diplomacy) and the other parties, plus \
media (Axios/Barak Ravid for US-Iran scoops). Every item: dual timestamp, who \
said it, specific link. If quiet, say so. Do not answer from memory."""),

    ("CYBER — attacks, cables, data centers, outages", """\
Produce the CYBER section of a {hours}-hour Iran-conflict digest: cyberattacks, \
threats to fiber-optic/submarine cables and data centers, and connectivity \
outages. Use get_cyber_reporting + get_media_reporting("Iran internet outage") / \
("submarine cable"). Every item: target, location, dual timestamp, tier, \
specific link. If nothing credible, say so plainly. Do not answer from memory."""),
]


def generate_section(title, instruction, hours=24):
    """Generate one digest section via a fresh, bounded agent chat."""
    return ask(build_chat(), instruction.format(hours=hours))


def generate_digest(hours=24, sections=None, progress=None):
    """Assemble the full digest. `progress(i, total, title)` is called before
    each section so a UI can show live status. Returns markdown."""
    sections = sections or DEFAULT_SECTIONS
    stamp = now_utc_iso()
    parts = [
        f"# MENA / Iran Conflict — {hours}h Intel Digest",
        f"_Generated {stamp} · window: last {hours}h · "
        "official → semi-official → media, dual timestamps, sourced_\n",
    ]
    for i, (title, instruction) in enumerate(sections):
        if progress:
            progress(i, len(sections), title)
        try:
            body = generate_section(title, instruction, hours)
        except Exception as e:  # one section failing must not lose the rest
            body = f"_Section failed: {e}_"
        parts.append(f"\n## {title}\n\n{body}")
    if progress:
        progress(len(sections), len(sections), "done")
    parts.append(f"\n---\n_Digest complete · {now_utc_iso()}_")
    return "\n".join(parts)
