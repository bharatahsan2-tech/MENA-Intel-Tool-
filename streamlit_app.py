"""MENA / Iran Conflict Intel Monitor — Streamlit UI.

Two modes on the same tool-driven agent:
  • Ask — on-demand tiered intel chat (build_chat / ask)
  • Daily digest — the 24h EXAMPLES-format summary (mena_bot.digest)

Run:  streamlit run streamlit_app.py
The free Gemini key is read from GEMINI_API_KEY (.env) or pasted in the sidebar.
"""
import os

import streamlit as st

# Cloud secrets bridge (must run BEFORE importing mena_bot, which reads os.getenv).
# Streamlit Cloud exposes secrets via st.secrets (a TOML), not env vars — copy
# them into os.environ. Locally, with no secrets.toml, this is a harmless no-op
# and the .env file is used instead.
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, str) and not os.environ.get(_k):
            os.environ[_k] = _v
except Exception:
    pass

from mena_bot.config import telegram_api_configured, x_configured
from mena_bot.data_loader import load_sources

st.set_page_config(page_title="MENA / Iran Conflict Intel Monitor",
                   page_icon="🛰️", layout="wide")


def _has_key() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


# ----------------------------- sidebar --------------------------------------
with st.sidebar:
    st.title("🛰️ MENA Intel Monitor")
    st.caption("Iran conflict · Gulf · Israel · maritime · cyber")

    if not _has_key():
        pasted = st.text_input("Gemini API key", type="password",
                               help="Free key at aistudio.google.com/apikey")
        if pasted:
            os.environ["GEMINI_API_KEY"] = pasted.strip()

    st.markdown("**Transports**")
    st.write("Gemini:", "✅ ready" if _has_key() else "❌ key needed")
    st.write("Telegram API:",
             "✅ live" if telegram_api_configured() else "— web fallback")
    st.write("X API:", "✅ live" if x_configured() else "— off (Bing relay)")

    try:
        d = load_sources()
        n = (sum(len(v) for v in d["countries"].values())
             + sum(len(d.get(g, [])) for g in ("maritime", "cyber", "media")))
        st.caption(f"{n} vetted sources · {len(d['countries'])} countries")
    except Exception:
        pass

    st.divider()
    if st.button("🔄 New conversation", use_container_width=True):
        for k in ("chat", "history"):
            st.session_state.pop(k, None)
        st.rerun()
    st.caption("Official → semi-official → media. Timestamps local+UTC. "
               "Sources linked. Claims never shown as confirmed.")


# ------------------------------- main ---------------------------------------
st.title("MENA / Iran Conflict Intel Monitor")
tab_chat, tab_digest = st.tabs(["💬 Ask (on-demand)", "📰 Daily 24h digest"])

with tab_chat:
    if not _has_key():
        st.info("Add your free Gemini API key in the sidebar to begin.")
    else:
        from mena_bot.agent import ask, build_chat

        if "chat" not in st.session_state:
            st.session_state.chat = build_chat()
            st.session_state.history = []

        for role, msg in st.session_state.history:
            with st.chat_message(role):
                st.markdown(msg)

        prompt = st.chat_input("Ask about strikes, Hormuz, ceasefire, cyber…")
        if prompt:
            st.session_state.history.append(("user", prompt))
            with st.chat_message("user"):
                st.markdown(prompt)
            with st.chat_message("assistant"):
                with st.spinner("Gathering live intel across sources…"):
                    try:
                        answer = ask(st.session_state.chat, prompt)
                    except Exception as e:
                        answer = f"⚠️ Error: {e}"
                st.markdown(answer)
            st.session_state.history.append(("assistant", answer))

with tab_digest:
    st.caption("Tool-driven summary of the last 24h in the analyst format: "
               "tactical (country-wise) → maritime → strategic → cyber.")
    col1, col2 = st.columns(2)
    hours = col1.slider("Window (hours)", 6, 48, 24, 6)
    include_cyber = col2.checkbox("Include cyber section", value=True)

    if st.button("Generate digest", type="primary", disabled=not _has_key()):
        from mena_bot.digest import DEFAULT_SECTIONS, generate_digest

        sections = DEFAULT_SECTIONS if include_cyber else [
            s for s in DEFAULT_SECTIONS if not s[0].startswith("CYBER")]
        bar = st.progress(0.0, "Starting…")

        def _cb(i, total, title):
            bar.progress(i / total, f"Gathering: {title}")

        try:
            md = generate_digest(hours=hours, sections=sections, progress=_cb)
            bar.empty()
            st.markdown(md)
            st.download_button("⬇️ Download (.md)", md,
                               file_name=f"mena_digest_{hours}h.md",
                               mime="text/markdown")
        except Exception as e:
            bar.empty()
            st.error(f"Digest failed: {e}")
