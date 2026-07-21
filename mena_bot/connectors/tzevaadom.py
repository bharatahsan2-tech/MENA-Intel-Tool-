"""Israel Home Front Command alert data via the Tzeva Adom mirror API.

The official oref.org.il endpoint geo-blocks non-Israeli IPs (403 from this
network, verified 2026-07-16). api.tzevaadom.co.il carries the same Home Front
Command siren/alert data and is reachable — it is the working real-time Israel
tactical feed. Label output as "Home Front Command data via Tzeva Adom mirror".

THREAT-CODE HONESTY: this API's `threat` integer is not authoritatively
documented publicly. We pass the raw code, cities, and time through unchanged
(all sacred/authoritative) and only hard-label code 0 = rockets/missiles — the
universally-documented meaning of the Red Color ("Tzeva Adom") alert. Every
other code is returned flagged "category unverified" rather than risk
mislabeling the attack method (missile vs drone), which is core intel. Fill in
_THREAT_CODES only when a code is confirmed against Home Front Command.
"""
from datetime import datetime, timezone

import requests

from ._common import fetch, make_item, now_utc_iso

_LIVE = "https://api.tzevaadom.co.il/notifications"
_HISTORY = "https://api.tzevaadom.co.il/alerts-history"
_PORTAL = "https://www.oref.org.il/"

# Only codes we can state with confidence. Others -> flagged unverified.
_THREAT_CODES = {
    0: "Rockets and missiles (Red Alert / “Tzeva Adom”)",
}


def _category(code):
    if code in _THREAT_CODES:
        return _THREAT_CODES[code], True
    return f"threat code {code} (category unverified — confirm at Home Front Command)", False


def _epoch_iso(ts):
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def get_israel_alerts(history_limit: int = 12) -> dict:
    """Get Israel Home Front Command siren/alert data (rockets, drones, etc.).

    The TACTICAL feed for Israel — real sirens by locality. Use for any question
    about strikes/sirens/interceptions in Israel. Returns both the live state
    (active alerts right now) and recent history.

    Cities are the LOCATION and come through in Hebrew — transliterate/translate
    them (keep the Hebrew in parentheses on first use). `active_count == 0` with
    an empty live list is a valid "no active sirens right now" reading.

    Args:
        history_limit: how many recent alert events to return from history.

    Returns:
        {"ok": True, "source", "source_url", "retrieved_utc",
         "active_count", "active": [...],
         "history_count", "history": [normalized item...],
         "threat_code_note"}
    """
    out = {
        "ok": True,
        "source": "Home Front Command (Pikud HaOref) data via Tzeva Adom mirror",
        "source_url": _PORTAL,
        "retrieved_utc": now_utc_iso(),
        "threat_code_note": (
            "cities/time/threat_code are authoritative from the feed. "
            "category_verified=false means the drone-vs-missile label is NOT "
            "confirmed for that code — report it as the feed's raw code and "
            "advise confirming at oref.org.il. Only code 0 (rockets/missiles) "
            "is confirmed."
        ),
    }

    # --- live active alerts ---
    try:
        resp = fetch(_LIVE)
        live = resp.json() if resp.status_code == 200 else []
    except (requests.RequestException, ValueError):
        live = []
    active = []
    if isinstance(live, list):
        for a in live:
            code = a.get("threat") if isinstance(a, dict) else None
            cat, verified = _category(code)
            active.append({
                "cities_he": a.get("cities") if isinstance(a, dict) else None,
                "threat_code": code,
                "threat_category": cat,
                "category_verified": verified,
                "is_drill": a.get("isDrill") if isinstance(a, dict) else None,
                "timestamp_utc": _epoch_iso(a.get("time")) if isinstance(a, dict) else None,
            })
    out["active_count"] = len(active)
    out["active"] = active

    # --- recent history ---
    history = []
    try:
        resp = fetch(_HISTORY)
        events = resp.json() if resp.status_code == 200 else []
    except (requests.RequestException, ValueError):
        events = []
    if isinstance(events, list):
        for ev in events[:history_limit]:
            for a in (ev.get("alerts") or []):
                code = a.get("threat")
                cat, verified = _category(code)
                cities = a.get("cities") or []
                ts = _epoch_iso(a.get("time"))
                loc = ", ".join(cities) if cities else None
                history.append(make_item(
                    source=out["source"], tier="official", country="ISRAEL",
                    pillars=["tactical"], language="he",
                    text=f"{cat} — sirens in: {loc or 'unspecified'}"
                         + (" (drill)" if a.get("isDrill") else ""),
                    timestamp_utc=ts, timestamp_raw=str(a.get("time")),
                    link=_PORTAL, source_url=_PORTAL, location=loc,
                    extra={"threat_code": code, "threat_category": cat,
                           "category_verified": verified,
                           "cities_he": cities, "is_drill": a.get("isDrill")},
                ))
    out["history_count"] = len(history)
    out["history"] = history
    return out


if __name__ == "__main__":
    r = get_israel_alerts(history_limit=5)
    print("ok", r["ok"], "active", r["active_count"], "history", r["history_count"])
    for it in r["history"][:5]:
        print("  ", it["timestamp_utc"], "| code", it.get("threat_code"),
              "|", (it["text"][:70]).encode("ascii", "replace").decode())
