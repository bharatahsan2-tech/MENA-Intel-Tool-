"""MENA / Iran Conflict Intel Monitor — Streamlit UI.

Two modes on the same tool-driven agent:
  • Ask — on-demand tiered intel chat (build_chat / ask), input docked at bottom
  • Digest — the 24h EXAMPLES-format summary (mena_bot.digest)

Run:  streamlit run streamlit_app.py
Gemini key: read from GEMINI_API_KEY (.env) or pasted in the sidebar.
"""
import os

import streamlit as st

# Cloud secrets bridge (must run BEFORE importing mena_bot, which reads os.getenv).
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, str) and not os.environ.get(_k):
            os.environ[_k] = _v
except Exception:
    pass

from mena_bot.config import telegram_api_configured, x_configured
from mena_bot.data_loader import load_sources

st.set_page_config(page_title="MENA Intel Monitor", page_icon="🛰️",
                   layout="wide", initial_sidebar_state="expanded")

# --------------------------- styling (sleek dark console) -------------------
st.markdown("""
<style>
:root { --accent:#38bdf8; --card:#111a2e; --line:rgba(255,255,255,.07); }
/* hide Deploy/menu/decoration/footer — but KEEP the header so the sidebar
   collapse/expand control stays reachable */
[data-testid="stToolbar"], [data-testid="stDecoration"],
#MainMenu, footer { display:none !important; }
header[data-testid="stHeader"] { background:transparent !important; }
html, body, [class*="css"], [data-testid="stChatInput"] textarea {
  font-family:"Inter","Segoe UI",-apple-system,system-ui,sans-serif;
  -webkit-font-smoothing:antialiased; }
.block-container { max-width:840px; padding-top:1.2rem; padding-bottom:8rem; }
h1,h2,h3 { letter-spacing:-.02em; font-weight:700; }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }
/* sidebar */
[data-testid="stSidebar"] { background:#080d18; border-right:1px solid var(--line); }
[data-testid="stSidebar"] .block-container { padding-top:1.4rem; }
[data-testid="stSidebar"] hr { margin:.8rem 0; border-color:var(--line); }
/* chat bubbles */
[data-testid="stChatMessage"] {
  background:var(--card); border:1px solid var(--line); border-radius:16px;
  padding:1rem 1.2rem; margin-bottom:.7rem; box-shadow:0 1px 4px rgba(0,0,0,.3); }
[data-testid="stChatMessage"] p { line-height:1.6; }
/* docked input bar */
[data-testid="stBottom"] > div { background:transparent; }
[data-testid="stChatInput"] {
  border-radius:16px; border:1px solid rgba(56,189,248,.4);
  background:#0e1830; box-shadow:0 4px 24px rgba(0,0,0,.35); }
[data-testid="stChatInput"] textarea { font-size:1rem; }
/* buttons */
.stButton button {
  border-radius:12px; border:1px solid var(--line); background:var(--card);
  font-weight:500; transition:border-color .15s,transform .05s; }
.stButton button:hover { border-color:var(--accent); }
.stButton button:active { transform:translateY(1px); }
/* status pills in sidebar */
.pill { display:inline-block; padding:.05rem .45rem; border-radius:999px;
  font-size:.72rem; font-weight:600; }
.pill.on { background:rgba(56,189,248,.15); color:#7dd3fc; }
.pill.off { background:rgba(148,163,184,.12); color:#94a3b8; }
.brandwrap { display:flex; align-items:center; gap:.55rem; margin-bottom:.1rem; }
.brandwrap .logo { font-size:1.5rem; }
.brandwrap .name { font-size:1.15rem; font-weight:700; letter-spacing:-.01em; }
.subtle { color:#8695ab; font-size:.82rem; }
.hero { text-align:center; margin:2.5rem 0 1.5rem; }
.hero h1 { font-size:1.9rem; margin-bottom:.3rem; }
</style>
""", unsafe_allow_html=True)


def _has_key() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


def _pill(on: bool, on_txt="live", off_txt="off"):
    cls, txt = ("on", on_txt) if on else ("off", off_txt)
    return f'<span class="pill {cls}">{txt}</span>'


# ------------------------------- sidebar ------------------------------------
with st.sidebar:
    st.markdown('<div class="brandwrap"><span class="logo">🛰️</span>'
                '<span class="name">MENA Intel Monitor</span></div>',
                unsafe_allow_html=True)
    st.markdown('<div class="subtle">Iran conflict · Gulf · Israel · maritime · '
                'cyber</div>', unsafe_allow_html=True)
    st.write("")

    if not _has_key():
        pasted = st.text_input("Gemini API key", type="password",
                               help="Free key at aistudio.google.com/apikey")
        if pasted:
            os.environ["GEMINI_API_KEY"] = pasted.strip()
            st.rerun()

    st.markdown(
        f'Gemini&nbsp;{_pill(_has_key(), "ready", "key needed")}<br>'
        f'Telegram&nbsp;{_pill(telegram_api_configured())}<br>'
        f'X&nbsp;API&nbsp;{_pill(x_configured())}',
        unsafe_allow_html=True)

    try:
        d = load_sources()
        n = (sum(len(v) for v in d["countries"].values())
             + sum(len(d.get(g, [])) for g in ("maritime", "cyber", "media")))
        st.markdown(f'<div class="subtle" style="margin-top:.6rem">{n} vetted '
                    f'sources · {len(d["countries"])} countries</div>',
                    unsafe_allow_html=True)
    except Exception:
        pass

    st.divider()
    mode = st.radio("Mode", ["💬 Ask", "📰 24h digest"], label_visibility="collapsed")
    if st.button("＋ New conversation", use_container_width=True):
        for k in ("chat", "history"):
            st.session_state.pop(k, None)
        st.rerun()
    st.markdown('<div class="subtle" style="margin-top:1rem">Official → '
                'semi-official → media · timestamps local+UTC · every claim '
                'sourced.</div>', unsafe_allow_html=True)


# ------------------------------- chat mode ----------------------------------
def _run_chat():
    from mena_bot.agent import ask, build_chat

    if "chat" not in st.session_state:
        st.session_state.chat = build_chat()
        st.session_state.history = []

    history = st.session_state.history

    if not history:
        st.markdown('<div class="hero"><h1>What do you need on the conflict?</h1>'
                    '<div class="subtle">Tiered, sourced, timestamped intel across '
                    'the Gulf, Israel, maritime and cyber.</div></div>',
                    unsafe_allow_html=True)
        examples = ["Tactical picture for Bahrain in the last 24h",
                    "Strait of Hormuz status right now",
                    "Any ceasefire or talks movement?"]
        cols = st.columns(len(examples))
        for c, ex in zip(cols, examples):
            if c.button(ex, use_container_width=True):
                st.session_state.pending = ex
                st.rerun()

    for role, msg in history:
        with st.chat_message(role, avatar="🛰️" if role == "assistant" else "🧭"):
            st.markdown(msg)

    typed = st.chat_input("Ask, or attach a screenshot / PDF to source…",
                          accept_file=True,
                          file_type=["png", "jpg", "jpeg", "webp", "pdf"])
    pending = st.session_state.pop("pending", None)

    user_text, files = None, []
    if typed is not None:
        user_text = (typed.text or "").strip()
        files = list(typed.files or [])
    elif pending:
        user_text = pending

    attachments = []
    for f in files:
        data = f.getvalue()
        if len(data) > 15 * 1024 * 1024:
            st.warning(f"{f.name} is larger than 15MB — skipped.")
            continue
        attachments.append({"data": data,
                            "mime": f.type or "application/octet-stream",
                            "name": f.name})

    if user_text or attachments:
        shown = user_text or ""
        if attachments:
            shown = (shown + "\n\n" if shown else "") + \
                "📎 " + ", ".join(a["name"] for a in attachments)
        history.append(("user", shown))
        with st.chat_message("user", avatar="🧭"):
            st.markdown(shown)
            for f in files:
                if (f.type or "").startswith("image/"):
                    st.image(f.getvalue(), width=260)
        with st.chat_message("assistant", avatar="🛰️"):
            msg = "Reading the file & sourcing it…" if attachments \
                else "Gathering live intel across sources…"
            with st.spinner(msg):
                try:
                    answer = ask(st.session_state.chat, user_text or "",
                                 attachments=attachments or None)
                except Exception as e:
                    answer = f"⚠️ Error: {e}"
            st.markdown(answer)
        history.append(("assistant", answer))


# ------------------------------ digest mode ---------------------------------
def _run_digest():
    st.title("📰 Daily 24h digest")
    st.markdown('<div class="subtle">Analyst-format summary: tactical '
                '(country-wise) → maritime → strategic → cyber.</div>',
                unsafe_allow_html=True)
    st.write("")
    c1, c2 = st.columns([2, 1])
    hours = c1.slider("Window (hours)", 6, 48, 24, 6)
    include_cyber = c2.checkbox("Cyber section", value=True)

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


# -------------------------------- main --------------------------------------
if not _has_key():
    st.markdown('<div class="hero"><h1>MENA Intel Monitor</h1><div class="subtle">'
                'Add your free Gemini API key in the sidebar to begin.</div></div>',
                unsafe_allow_html=True)
elif mode.startswith("📰"):
    _run_digest()
else:
    _run_chat()
