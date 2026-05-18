# Contributing

Thanks for your interest in the Literature Synthesis Engine. This document
covers how to file issues, propose changes, and run the test loop locally.

---

## Filing an issue

Useful issue reports include:

- The condition / topic string and parameters (`max_papers`, `max_deep`) you
  ran.
- The OS, Python version, and whether you ran from source or from the
  PyInstaller `.exe` build.
- The phase at which the problem appeared (Phase 1 ingest, Phase 3a triage,
  etc.) and the last few lines of the SSE log.
- A minimal reproduction if the bug is in a specific module (e.g. a PMC ID
  whose XML triggers the issue).

Avoid pasting raw API responses that contain your credentials. The repo's
`.gitignore` and `SECURITY_REVIEW.md` describe what should never be shared
publicly.

## Pull request flow

1. Fork the repo and create a feature branch from `main`.
2. Set up a local environment per `README.md` — install via
   `pip install -e .` or `uv sync`. Copy `.env.example` to `.env` and fill in
   your own keys.
3. Apply the Supabase migrations in order (`config/schema.sql`,
   `schema_v2_migration.sql`, `schema_v3_migration.sql`) against a clean
   project.
4. Make your change. Keep diffs surgical — one concern per PR. Update
   `CHANGELOG.md` under `## [Unreleased]` if your change is user-visible.
5. Run a smoke test from source: `python -c "import app_server"` should
   complete without import errors.
6. Open a PR describing the change and the validation you ran. Link the
   issue if applicable.

## Style

- Python: targeted at `3.12`. Black-compatible formatting; lines under 110
  chars (matches `pyproject.toml`).
- Prompts (`config/prompts/*.txt`): keep the JSON schema explicit and
  preserve the existing placeholder names (`{topic_title}`, `{topic}`,
  `{full_text}`, etc.). Reviewers / arbiter prompts have a contract with
  `pipeline/phase3_extract.py` — coordinated changes only.
- Schema migrations: every column / table addition uses `if not exists` /
  `add column if not exists` so the migration is idempotent.
- Templates (`templates/*.j2`): Jinja2 trim/lstrip blocks enabled; tables
  use Markdown pipe syntax so the reportlab renderer picks them up.

## Areas that welcome contributions

- A real UMLS REST API integration to replace the LLM-inferred CUIs.
- A second-reviewer UI for human-in-the-loop QUADAS scoring (feeds the
  `human_ratings` table that powers the Kappa engine).
- OpenAlex source ingestion (queued in `ROADMAP.md` under Priority 4 / TASK
  2).
- ClinicalTrials.gov source for protocol-context retrieval.

## Code of conduct

Be considerate, professional, and assume good faith.
