"""MENA Conflict Intel Monitor — terminal chat (Phase 1).

Run:  python app.py
Requires a free Gemini key in .env (see .env.example).

On-demand intel: ask anything about the conflict — a country's tactical picture,
the Strait of Hormuz, ceasefire talks, a cyber incident, or a specific Telegram
channel. The mena_bot package is UI-agnostic so a Streamlit web UI (Phase 3) can
wrap the same agent for the team.
"""
from __future__ import annotations

import sys


def main() -> None:
    try:
        from mena_bot.agent import build_chat, ask
    except Exception as e:  # e.g. google-genai not installed
        print(f"[startup error] {e}")
        print("Install deps first:  pip install -r requirements.txt")
        sys.exit(1)

    try:
        chat = build_chat()
    except RuntimeError as e:  # missing key -> clear message
        print(f"[config] {e}")
        sys.exit(1)

    print("=" * 68)
    print(" MENA Conflict Intel Monitor — Phase 1 (on-demand query)")
    print(" Official -> semi-official -> unofficial/media, corroborated & sourced.")
    print(" Ask e.g.: 'tactical picture for Bahrain in the last 24h',")
    print(" 'status of the Strait of Hormuz', 'any ceasefire movement?'.")
    print(" 'quit' to exit.")
    print("=" * 68)

    while True:
        try:
            q = input("\nyou > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            break
        if not q:
            continue
        if q.lower() in {"quit", "exit", "q"}:
            print("bye.")
            break
        try:
            print("\n" + ask(chat, q))
        except Exception as e:
            print(f"[error] {e}")


if __name__ == "__main__":
    main()
