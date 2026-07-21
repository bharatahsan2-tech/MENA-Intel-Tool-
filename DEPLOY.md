# Deploying the MENA Intel Monitor

Two parts: (1) push the code to GitHub, (2) deploy the Streamlit app to the cloud.
Secrets are **never** in the repo — they go into the cloud host's secrets box.

---

## 1. Push to GitHub

The repo is already committed locally with a `.gitignore` that excludes every
secret (`.env`, `*.session`, `telegram_string_session.txt`, `.streamlit/secrets.toml`).

**Create the repo and push** (recommended: **private**):

Option A — GitHub website:
1. Go to https://github.com/new → name it e.g. `mena-intel-tool` → **Private** → Create.
2. In this folder, run the commands GitHub shows under *"…or push an existing repository"*:
   ```
   git remote add origin https://github.com/<you>/mena-intel-tool.git
   git branch -M main
   git push -u origin main
   ```

Option B — GitHub CLI (if installed):
```
gh repo create mena-intel-tool --private --source=. --push
```

**Before pushing, sanity-check nothing secret is staged:**
```
git status --porcelain        # should NOT list .env, *.session, or secrets.toml
git ls-files | grep -E "\.env$|\.session|secrets\.toml$"   # must print NOTHING
```

---

## 2. Deploy to Streamlit Community Cloud (free)

Best fit for a Streamlit app; supports **private** repos.

1. Go to https://share.streamlit.io → sign in with GitHub → authorize.
2. **New app** → pick your repo, branch `main`, main file `streamlit_app.py`.
3. **Advanced → Python version 3.11+**, then open **Secrets** and paste
   (from `.streamlit/secrets.toml.example`):
   ```toml
   GEMINI_API_KEY = "your-key"
   X_BEARER_TOKEN = "your-token"          # optional
   TELEGRAM_API_ID = "37xxxxxx"           # optional
   TELEGRAM_API_HASH = "your-hash"        # optional
   TELEGRAM_STRING_SESSION = "1Bv...long" # optional — see below
   ```
4. **Deploy.** First build installs `requirements.txt` (a few minutes).
5. Share the app URL with your team. Manage viewer access in the app's settings.

### Telegram on the cloud — the one catch
The `.session` login file can't exist on a cloud host, and the phone-login can't
run there. So generate a portable **StringSession** locally, once:
```
python tools/export_session_string.py    # writes telegram_string_session.txt
```
Paste its contents into the `TELEGRAM_STRING_SESSION` secret, then delete the file.
The app auto-detects the StringSession and uses it.

> ⚠️ **Telegram caveat:** Telegram may challenge or rate-limit logins from a
> datacenter IP it hasn't seen. X + Bing + RSS work on cloud with no such issue;
> Telegram is best-effort there. The StringSession is a full login credential —
> treat it like a password.

### Without Telegram secrets
The app still runs: X (if token set) + Bing relay + native RSS cover officials,
Iran, Gulf, maritime, cyber, and all media. Only the direct Telegram channel
reads are skipped, falling back to the web preview where available.

---

## Alternatives
- **Hugging Face Spaces** (Streamlit SDK) — similar flow, secrets under *Settings → Secrets*.
- **Render / Railway** — set the start command to
  `streamlit run streamlit_app.py --server.port $PORT --server.address 0.0.0.0`
  and add the same env vars.
