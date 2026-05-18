# SECURITY_REVIEW.md

Pre-publication audit. **The credential values themselves are NOT reproduced in this file.** This document only identifies where they live in the source tree, so they can be rotated and the publication can be made safe.

## Hardcoded credentials

### `bundled_credentials.py` (project root) — CRITICAL

This file bakes API keys into the PyInstaller bundle so the end-user never enters them. The keys are present as Python string literals on:

- Line 7 — `ANTHROPIC_API_KEY` (Anthropic API key, prefix `sk-ant-api03-...`)
- Line 10 — `NCBI_API_KEY` (NCBI E-utilities key, 32-char hex)
- Line 13 — `SUPABASE_KEY` (Supabase **service_role** key, prefix `sb_secret_...`)

The Supabase key is the **service_role** — full database read/write. It bypasses RLS. If this key leaks publicly, anyone can read or wipe the Supabase project.

**Action required before publishing the source repo — step-by-step playbook is in `PUBLISHING.md`. Summary:**

1. **Rotate all three keys at their respective consoles** (URLs and exact UI steps in PUBLISHING.md §1).
2. **`bundled_credentials.py` is already gitignored** in the shipped `.gitignore`. Only the safe `bundled_credentials.template.py` (empty values) is committed.
3. **No `.exe` will be published** per the user's decision (2026-05-18). The `.gitignore` excludes `*.exe`, `dist/`, `build/`, and `package/`. The local desktop `.exe` keeps the OLD keys baked in — it will stop working as soon as the keys are rotated, and that is expected. Rebuild the `.exe` locally with the new keys if you want to keep using it on this machine.
4. For local development from source (no `.exe` involved), `config.settings` reads from `.env` via pydantic-settings — no `bundled_credentials.py` is needed at all.

### `.env` (project root) — gitignored

Contains the same three keys for local development. Already excluded by `.gitignore`. Verify before pushing that `.env` is not staged: `git status --ignored`.

### `docs/SESSION_SNAPSHOT.md`

This internal session log includes a "Rotate API credentials before any external distribution" note that **reproduces the first 8 and last 5 characters of the keys** as memory aids. Before any public publication, either:
- Remove the credential references from the snapshot, OR
- Do not include `docs/SESSION_SNAPSHOT.md` in the published repo (it is internal documentation).

The shipped `docs/` folder in this publication package contains only `technical_documentation.md`, `scientific_preprint.md`, and `executive_summary.md` — `SESSION_SNAPSHOT.md` is intentionally excluded.

## Verification checklist before `git push`

- [ ] All three keys have been rotated at their consoles (see `PUBLISHING.md` §1).
- [ ] `bundled_credentials.py` is not in `git ls-files` (only `bundled_credentials.template.py` is).
- [ ] `.env` is not in `git ls-files`.
- [ ] `docs/SESSION_SNAPSHOT.md` is not in the public repo (or has been redacted).
- [ ] No `.exe`, `dist/`, `build/`, `package/` artefact is staged.
- [ ] `git ls-files | xargs grep -lE 'sk-ant-api03|sb_secret_|af63911aac'` returns empty.
- [ ] Optional: `gitleaks detect` returns clean.

## What is safe to publish

- All Python source in `pipeline/`, `utils/`, `config/` (excluding `bundled_credentials.py`).
- All prompts in `config/prompts/`.
- All templates in `templates/`.
- All SQL migrations in `config/*.sql`.
- The `.env.example` template (no real values).
- The example PDFs in `examples/` — generated outputs only, no API state.
- The documentation in `docs/` (technical / preprint / executive summary).

## What MUST NOT be published

- `bundled_credentials.py`
- `.env`
- `data/raw/`, `data/checkpoints/`, `data/filtered/`
- `dist/`, `build/`, `package/` (PyInstaller output)
- Any `.exe` artefact (those contain the baked credentials)
- Internal session logs (`docs/SESSION_SNAPSHOT.md`)

---

*Generated 2026-05-18 as part of the github_publication preparation.*
