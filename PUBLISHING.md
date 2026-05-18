# Publishing checklist

Step-by-step actions to make this repo safe for `git push public`. Read once before doing anything, then execute top-to-bottom.

---

## 1. Rotate the three exposed credentials (REQUIRED)

The current `bundled_credentials.py` has these three keys baked in as Python string literals. They have been visible in chat transcripts and embedded in compiled `.exe` artefacts — treat them as **compromised**. They must be rotated before any of this code becomes public.

### 1.1 Anthropic API key

1. Open https://console.anthropic.com/settings/keys
2. Locate the API key currently in use by this project (check the "Last used" timestamp).
3. Click **Delete** on that key.
4. Click **Create Key** → name it something like `longcovid-app-build-v3` → copy the new value once (you only see it once).
5. Paste the new value into your local `.env` file (project root): `ANTHROPIC_API_KEY=sk-ant-api03-...`

### 1.2 NCBI E-utilities key

1. Open https://www.ncbi.nlm.nih.gov/account/settings/
2. Scroll to **API Key Management**.
3. Click **Delete** next to the existing key.
4. Click **Create new API Key**, copy the value.
5. Paste into `.env`: `NCBI_API_KEY=...`

### 1.3 Supabase service_role key (most urgent — full DB R/W)

1. Open your Supabase project dashboard at https://supabase.com/dashboard
2. Select your project → **Project Settings** → **API**.
3. Click **Reset service_role secret**.
4. Copy the new value.
5. Paste into `.env`: `SUPABASE_KEY=sb_secret_...`

### 1.4 (Optional) Re-bake the `.exe`

If you keep using the desktop `.exe`, you also need to update `bundled_credentials.py` with the new keys and rebuild. The shipped artefact on your desktop right now still has the OLD keys baked in — they will return 401 / 403 as soon as you rotate.

```powershell
# In C:\Users\Hamsa\longcovid-app-build\
# 1. Edit bundled_credentials.py with the three new key values
# 2. Rebuild
pyinstaller --noconfirm longcovid.spec
# 3. Copy the new .exe to the desktop, overwriting the old one
Copy-Item .\dist\LongCovidResearch.exe "$env:USERPROFILE\OneDrive\Escritorio\LongCovidResearch.exe" -Force
```

After this, the rotated keys are functional locally and the compromised ones are dead.

---

## 2. Verify the public repo is clean BEFORE the first push

```powershell
cd <your-cloned-repo-root>

# Confirm no .env, no bundled_credentials.py, no .exe is staged
git status --ignored

# Search the staged tree for any residual secret patterns (Anthropic + Supabase prefixes)
git ls-files | xargs grep -lE 'sk-ant-api03-[A-Za-z0-9]|sb_secret_[A-Za-z0-9]' 2>$null
# ↑ Expected output: empty. If any file appears, fix before pushing.
# (These regex patterns match *any* Anthropic/Supabase secret, including future ones
#  you might accidentally paste — they are not specific to your rotated keys.)

# Confirm .gitignore is actually doing its job
echo "TESTING" > bundled_credentials.py
git status   # bundled_credentials.py should NOT appear in the output
Remove-Item bundled_credentials.py
```

If any of the checks above show problems, do not push.

---

## 3. What this repo contains and what it doesn't

**Contains (safe to push):**
- `README.md`, `ROADMAP.md`, `LICENSE`, `.gitignore`, `.env.example`
- `bundled_credentials.template.py` (no real values; users copy and fill in)
- `docs/technical_documentation.md` · `docs/scientific_preprint.md` · `docs/executive_summary.md`
- `examples/*.pdf` — automated demo outputs (no API state inside)
- `examples/README.md`
- `SECURITY_REVIEW.md`, `PUBLISHING.md` (this file)
- The source code from `C:\Users\Hamsa\longcovid-app-build\` (excluding the items below)

**Excluded (covered by `.gitignore`):**
- `.env` (your real local credentials)
- `bundled_credentials.py` (real values baked for `.exe` builds)
- `data/raw/`, `data/checkpoints/`, `data/filtered/`, `reports/`
- `dist/`, `build/`, `package/` (PyInstaller output)
- Any `.exe` file
- `docs/SESSION_SNAPSHOT.md` (internal log; references the rotated key prefixes)
- Helper scripts (`patch_*.py`, `poll_status.ps1`, `gen_pdfs.py`, `run_narcolepsy_test.py`)

---

## 4. Recommended first commit

```powershell
# In the public repo root:
git init
git add .
git status   # eyeball this — everything listed should be from the "Contains" list above
git commit -m "Initial public release — Literature Synthesis Engine v3.0"
git branch -M main
git remote add origin https://github.com/<your-user>/<your-repo>.git
git push -u origin main
```

---

## 5. Post-publication

- Optional: enable GitHub's **secret scanning** in repo settings (Settings → Code security and analysis). It will alert on any future leak of the rotated keys.
- Optional: enable **Dependabot alerts** for dependency vulnerabilities.
- Add a CODEOWNERS file if you want PR-level review gates.

---

*If anything in this checklist is unclear, the supporting context is in `SECURITY_REVIEW.md`.*
