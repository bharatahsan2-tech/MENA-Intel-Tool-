"""Probe every source in data/sources.json and write back verification results.

Stdlib only. Sets per-source: verified (bool), http_status, checked_at, verify_note.
Telegram sources are checked via the t.me/s/<handle> public preview page.
"""

import json
import ssl
import sys
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "sources.json"
TIMEOUT = 15
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE  # some gov/state sites have broken chains; we only need reachability


def fetch(url, headers=None):
    req_headers = {"User-Agent": UA, "Accept-Language": "en"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
            body = r.read(400_000)
            return r.status, body.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def probe(source):
    url = source.get("url", "")
    access = source.get("access", "web")
    headers = None
    conn = source.get("connector") or {}
    if isinstance(conn, dict) and conn.get("headers"):
        headers = conn["headers"]

    status, body = fetch(url, headers)
    note = ""
    ok = False

    if status is None:
        note = f"unreachable ({body})"
    elif access == "telegram":
        if "tgme_widget_message" in body:
            ok, note = True, "public channel, preview OK"
        elif "tgme_page_extra" in body and ("subscriber" in body or "member" in body):
            ok, note = True, "channel exists, preview disabled"
        elif status == 200:
            note = "t.me reachable but channel not found / no preview"
        else:
            note = f"HTTP {status}"
    elif status == 200:
        low = body[:4000].lower()
        if access == "rss" and ("<rss" in low or "<feed" in low or "<?xml" in low):
            ok, note = True, "RSS/Atom feed OK"
        elif access == "rss":
            ok, note = True, "200 but not XML at this path — connector must find real feed URL"
        elif access == "api":
            ok, note = True, "API endpoint reachable"
            if body.strip() in ("", "[]", "{}"):
                note = "API reachable, empty body (valid: no active alerts)"
        else:
            ok, note = True, "reachable"
    elif status in (401, 403):
        note = f"HTTP {status} — bot-blocked or auth-gated (may work in browser)"
    else:
        note = f"HTTP {status}"

    source["verified"] = ok
    source["http_status"] = status
    source["verify_note"] = note
    source["checked_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return source.get("name", url), ok, status, note


def iter_sources(data):
    for country_list in data.get("countries", {}).values():
        yield from country_list
    for key in ("maritime", "regional_osint", "cyber", "media"):
        yield from data.get(key, [])


def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    sources = list(iter_sources(data))
    print(f"Probing {len(sources)} sources...")

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(probe, sources))

    data["meta"]["probe"]["last_run"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    DATA.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    ok_n = sum(1 for _, ok, _, _ in results if ok)
    print(f"\n{ok_n}/{len(results)} verified\n")
    for name, ok, status, note in sorted(results, key=lambda r: r[1]):
        mark = "OK " if ok else "FAIL"
        print(f"[{mark}] {name} — {status} — {note}")


if __name__ == "__main__":
    sys.exit(main())
