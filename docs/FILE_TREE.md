# File Tree — `longcovid-app-build/`

Project root: `C:\Users\Hamsa\longcovid-app-build\` (Windows native — this is the active build dir used to produce the .exe; the original WSL source at `~/projects/long-covid-pipeline/` is a stale earlier copy and should be considered abandoned). Last updated 2026-05-17 (post Master Improvement Spec v3.0 implementation).

Raw `find . -type f | sort` output saved to `docs/FILE_TREE.txt` (80 entries; .venv excluded because it's a Windows symlink that find did not follow).

---

## Top-level entry points

| Path | What it is |
|---|---|
| `app_server.py` | **The canonical entry point**. Flask web server that serves `templates/app.html` on `localhost:7432-7434`, exposes `/start`, `/status`, `/stream` (SSE), `/cancel`, `/report` endpoints. **v3 architecture (2026-05-17)**: pipeline now runs in a `multiprocessing.Process` (spawn context), not a thread — Flask and pipeline no longer compete for the GIL. `multiprocessing.freeze_support()` called at module top for PyInstaller spawn compatibility. Relay thread mirrors `mp.Queue` events into a thread-local queue consumed by the SSE endpoint. This is what PyInstaller bundles. |
| `app_gui.py` | **Legacy / unused**. Earlier tkinter-based GUI before we pivoted to Flask+web. Kept in tree only because the file is still on disk; not referenced by the current spec. Can be deleted safely. |
| `run_pipeline.py` | Typer CLI entry. Used during development/testing (e.g. `python run_pipeline.py --max-papers 20 --max-deep 5 --skip-phases 2`). Not used by the .exe. |
| `longcovid.spec` | PyInstaller spec. Entry point = `app_server.py`. Bundles all prompts, templates, hidden Flask imports. `console=False`. |
| `bundled_credentials.py` | Hardcoded API keys (Anthropic, NCBI, Supabase). `install()` is called at app start to set env vars. **Critical override behavior**: uses plain assignment when existing env var is empty (Claude Code parent shell sets `ANTHROPIC_API_KEY=""` which would otherwise win against `os.environ.setdefault`). |
| `app_paths.py` | Cross-platform path helper. `RESOURCE_DIR` = `sys._MEIPASS` when frozen, project root in dev. `APP_DATA_DIR` = `%LOCALAPPDATA%\HamsCoResearch\LongCovid`. `USER_DESKTOP` resolves to OneDrive/Escritorio. |
| `app_pdf.py` | Lightweight Markdown→PDF renderer using reportlab. Replaces WeasyPrint (which needed GTK3 on Windows). Supports headers, paragraphs, tables, bullets, blockquotes, fenced code. **v3 fix (2026-05-17)**: `_inline()` now html-escapes `<`, `>`, `&` BEFORE markdown→tag substitution, so PMC reference contamination (e.g. `score </= 3?` in titles) no longer trips reportlab's strict HTML tag parser. |
| `.env` | **Secrets file** (gitignored). Contains the real Anthropic/NCBI/Supabase keys. Used in dev mode only. In production, `bundled_credentials.py` supersedes these because PyInstaller bundles the keys into the binary. |
| `README_instrucciones.txt` | User-facing instructions for the .exe (Spanish). Bundled in the zip we ship. |

---

## `config/` — Configuration and prompts

| Path | What it is |
|---|---|
| `config/settings.py` | Pydantic-settings BaseSettings class. Loads `.env`. Exposes `ANTHROPIC_API_KEY`, `NCBI_API_KEY`, `SUPABASE_URL`, model names, pipeline limits, **methodology constants** (QUADAS_CUTOFF=13, HETEROGENEITY_CRITICAL_THRESHOLD=90.0, LEAVE_ONE_OUT_INFLUENCE_THRESHOLD=0.10, Cohen effect-size cutoffs). **v3 (2026-05-17)**: `ARBITER_ENABLED` (default `True`) and `UMLS_NORMALIZATION_ENABLED` (default `True`) toggles. |
| `config/schema.sql` | v1 Supabase schema. Run once in Supabase SQL editor on a fresh project. Defines `papers`, `extractions`, `embeddings`, `contradictions` tables + pgvector extension. |
| `config/schema_v2_migration.sql` | v2 migration. Adds methodology + calibration columns to `extractions`, creates `provenance` table, adds typed contradiction columns. Run after v1. |
| `config/schema_v3_migration.sql` | **v3 migration (2026-05-17)**. Adds arbiter fields to `extractions` (`reconciliation_triggered`, `reviewer_a_raw`, `reviewer_b_raw`, `arbiter_notes`, `llm_judgment_flags`). Creates `extracted_phenotypes` (UMLS CUI + MeSH heading + llm_judgment flag), `human_ratings` (for Kappa validation), and `runs` (multi-run registry). Adds `run_id` foreign keys to `papers` and `extractions`. Run after v2. |
| `config/prompts/triage_haiku.txt` | Haiku abstract-triage prompt (Phase 3a). Uses `{topic_title}` placeholder for any disease. Returns JSON with `is_long_covid_focused`, `study_design`, `sample_size`, `long_covid_definition_weeks` (legacy names — see KNOWN ISSUES), `main_symptoms`, `extraction_confidence`. |
| `config/prompts/extraction_sonnet.txt` | Sonnet deep-extraction prompt (Phase 3d). Senior Clinical Methodologist persona. Returns 5-block JSON: study_metadata, factual_extraction, methodology_appraisal (NOS+GRADE+MCID), bias_audit (8 axes), phenotype_mapping, calibration, provenance, **quality_assessment (QUADAS 19-item)**, **effect_sizes_classified (Cohen's r)**, **moderators**. Uses `{topic_title}`. |
| `config/prompts/synthesis_sonnet.txt` | Sonnet synthesis prompt (Phase 5). v3 readability rules: top-N over enumeration, citation rule forbids DOI fabrication, uses `{topic_title}`. Outputs structured JSON with executive_summary, key_findings_summary, calibrated_findings, methodology_errors, contradictions, gaps, recommendations. |
| `config/prompts/due_diligence_sonnet.txt` | Sonnet pharma DD prompt (Phase 5). Senior Clinical Strategy Analyst persona. Target Trial Emulation, Blue/Red Ocean mechanisms, Methodological Risk Index, Phase II design skeleton. |
| `config/prompts/executive_summary_sonnet.txt` | **NEW** — Sonnet exec summary prompt (Phase 5). Non-technical 2-page brief. Forbidden jargon list (GRADE, QUADAS, I², MCID, etc.). Audience: investor / executive / journalist. |
| `config/prompts/heterogeneity_analysis.txt` | Sonnet ad-hoc prompt triggered when I² ≥ 90% on a factor. Performs meta-analytic ANOVA approximation over moderators. |
| `config/prompts/contradiction_check.txt` | Standalone pairwise contradiction detection prompt. Currently unused by the active flow (synthesis prompt handles contradictions inline), kept for potential cluster-level future use. |
| `config/prompts/arbiter_sonnet.txt` | **NEW v3 (2026-05-17)** — arbiter prompt for the two-step extraction. Reconciles Reviewer A (temp 0.1) and Reviewer B (temp 0.3) outputs; resolves quantitative discrepancies against source text; audits QUADAS / GRADE risk-of-bias against cited quotes; re-validates provenance; emits `reconciliation_triggered` boolean and `llm_judgment_flags` map. |
| `config/prompts/reflection_sonnet.txt` | **Legacy v2** — peer-reviewer pass. Removed from active flow in v3 (replaced by two-step arbiter in `phase3_extract.py`). Kept on disk only. |

---

## `pipeline/` — Sequential analysis phases

| Path | What it is |
|---|---|
| `pipeline/__init__.py` | empty package marker |
| `pipeline/runner.py` | **NEW v3 (2026-05-17)** — worker entry point for the multiprocessing migration. Exposes `execute_industrial_pipeline(queue, disease, mesh_terms, max_papers, max_deep)` which is the target of `multiprocessing.Process` from `app_server.py`. Defines the `PHASES` tuple and the event protocol (log / phase_start / phase_complete / spend / done / error). Re-installs `bundled_credentials` at child entry because Windows 'spawn' creates a fresh interpreter. Calls `clear_stale_state_if_topic_changed()` before Phase 1. |
| `pipeline/phase1_ingest.py` | **Phase 1**. `fetch_pmc_ids()`, `fetch_pmc_metadata()` (parser strips ref-list before extracting text), `fetch_pmc_fulltext()`, `enrich_with_fulltext()`, `fetch_medrxiv_papers()` (**FIXED 2026-05-17** — proper cursor-vs-total pagination since the API returns 30/page not 100, 90-day chunking, retries, topic-aware terms), **`expand_search_terms(topic)`** (NEW 2026-05-17 — single Haiku call ~$0.001 that returns up to 15 MeSH headings + synonyms + abbreviations for non-Long-COVID topics; OR-joined into both PubMed query and medRxiv search), `build_query(topic, mesh_terms, synonyms=...)`, `run()`. Calls `save_run_context()` at start to persist the topic for later phases. |
| `pipeline/phase2_filter.py` | **Phase 2**. ASReview filtering. Mostly a passthrough in current usage — Phase 3 has auto-promote fallback when `relevant_papers.jsonl` is missing. |
| `pipeline/phase3_extract.py` | **Phase 3**. `run_triage()` (Haiku batch), `select_for_deep_analysis()` (ranking). **v3 (2026-05-17) — two-step extraction**: `run_deep()` now branches on `settings.ARBITER_ENABLED`. When true, calls `_run_arbiter_pass()`: builds a single Batch with 2N reviewer requests (`build_reviewer_request(temperature=0.1|0.3, suffix='a'|'b')`), polls, then submits a second Batch of arbiter requests (`build_arbiter_request`, temp 0.0) for every paper where both reviewers succeeded. Final extraction is the arbiter output with reviewer raw kept for audit. When false, falls back to `_run_single_pass()`. `reviewer_custom_id()` keeps the Anthropic Batch 64-char custom_id limit. After deep extraction, calls `_run_umls_normalization()` which runs one Haiku tool call per paper via `utils.umls_normalizer`. Custom_id suffix convention: `__a` / `__b` / `__arb`. |
| `pipeline/phase4_store.py` | **Phase 4**. Upserts papers, triage extractions, deep extractions, and provenance entries to Supabase. Includes lossy column-projection mapping (`map_deep_to_schema`). **v3 additions (2026-05-17)**: writes `reconciliation_triggered`, `arbiter_notes`, `llm_judgment_flags`, `reviewer_a_raw`, `reviewer_b_raw` to `extractions`. At the end, reads `data/filtered/normalized_entities.jsonl` and bulk-inserts rows into the new `extracted_phenotypes` table. |
| `pipeline/phase5_analyze.py` | **Phase 5**. The largest file. Contains: numeric aggregators (symptom consensus, definition heterogeneity, study design distribution, QUADAS distribution, methodology quality), **meta-analytic statistics** (DerSimonian-Laird random-effects pooling, leave-one-out sensitivity, Egger's regression approximation, trim-and-fill), forest-plot text renderer, three Sonnet calls: `call_synthesizer()`, `call_due_diligence()`, **`call_executive_summary()`**, and the `heterogeneity_critical_synthesis()` for I² ≥ 90% cases. **v3 (2026-05-17)**: counts `reconciliations_triggered` from the arbiter outputs and exposes it in `aggregates` for the methodology section. |
| `pipeline/phase6_report.py` | **Phase 6**. Renders three Markdown reports + HTML + PDF. `CitationManager` class with DOI normalization (recovers fabricated DOIs that wrap PMC IDs, drops obvious placeholders). **CrossRef DOI resolver (NEW 2026-05-17)**: `resolve_crossref(doi)` + `crossref_vancouver(meta, n)` + `CitationManager.resolve_unresolved_via_crossref()` — for any cited DOI not present in `papers_by_id`, queries `api.crossref.org/works/{doi}` and renders the result as Vancouver. Module-level `_crossref_cache` is shared across the 3 reports. `build_markdown()`, `build_due_diligence_markdown()`, **`build_executive_summary_markdown()`**. **v3 (2026-05-17)**: three new Jinja2 filters `llm_badge` / `calc_badge` / `consensus_badge` registered under `\|llm` / `\|calc` / `\|consensus`. Each report copies to `USER_DESKTOP` automatically with `research_<topic_slug>_<date>.pdf` naming. |

---

## `utils/` — Shared helpers

| Path | What it is |
|---|---|
| `utils/__init__.py` | empty package marker |
| `utils/checkpointing.py` | `Checkpoint` class. Writes `data/checkpoints/{phase_name}.done` marker files for idempotency. |
| `utils/claude_client.py` | Anthropic SDK wrapper. `submit_batch()`, `poll_batch()`, `parse_json_response()` (strict + repair fallback), `sanitize_custom_id()` (PMC/DOI → batch-API-safe ID). |
| `utils/supabase_client.py` | Singleton Supabase client. `upsert_paper`, `upsert_papers_batch`, `upsert_extraction`, `store_provenance`. |
| `utils/run_context.py` | Per-run metadata (topic, mesh_terms) shared across phases via `data/raw/run_meta.json`. Exposes `save_run_context()`, `topic_title()` (e.g. "long covid" → "Long COVID"), `topic_slug()` (filesystem-safe). **v3 (2026-05-17)**: `clear_stale_state_if_topic_changed()` wipes `data/checkpoints` + `data/raw` + `data/filtered` when the new run's topic differs from the previously-stored one — prevents the silent-skip bug where checkpoints from a prior topic short-circuit a fresh run. |
| `utils/xml_parser.py` | **NEW v3 (2026-05-17)**. PMC XML semantic section extractor. `extract_structured_sections(xml)` buckets `<sec sec-type="...">` content into `{metadata, methods, results, discussion_limitations, conflicts_funding}` with per-section char caps (12k / 30k / 40k / 35k / 3k). Strips `<ref-list>` BEFORE extraction so the bibliography never bleeds into discussion text. `sections_to_compact_text()` concatenates the dict into a single string with `=== <SECTION NAME> ===` delimiters that the Sonnet prompt understands. |
| `utils/umls_normalizer.py` | **NEW v3 (2026-05-17)**. UMLS / MeSH normalisation via Anthropic tool calling. `normalize_extraction(extraction)` collects free-text phenotypes / mechanisms / biomarkers / risk factors from a deep extraction, makes one Haiku call with the `normalize_biomedical_entities` tool schema, and returns `[{verbatim_text, entity_type, umls_cui, mesh_heading, llm_judgment=True}, ...]`. Declared limitation: no real UMLS API key — CUIs are LLM-inferred from training data. |
| `utils/validation_engine.py` | **NEW v3 (2026-05-17)**. Cohen's Kappa engine. `compute_cohens_kappa()`, `compute_rmse()`, `compute_pearson()`, `interpret_kappa()` (Landis & Koch 1977 benchmarks), `validate_field()` (aligns human and AI ratings on `paper_id × field_name` and computes the appropriate statistic per `field_kind`). Reads from the v3 `human_ratings` table. UI integration deferred. |
| `utils/logging_setup.py` | Rich logging configuration. Not heavily used in the Flask path (logs go to the event queue / SSE instead). |

---

## `templates/` — Jinja2 templates

| Path | What it is |
|---|---|
| `templates/app.html` | **The dark-luxury web UI**. Single-page, Google Fonts (Playfair Display + DM Sans + JetBrains Mono), 🍀 HAMS & CO. 🍀 branding, dark theme (#080808 bg, #C9A84C gold, #2D6A4F green). Form: research topic, MeSH terms (optional), max papers, max deep. Live cost estimator. Phase timeline with active/done states. SSE-fed live log. API spend counter color-coded. Modal confirmation for max_deep > 100. |
| `templates/report.md.j2` | **Main research report** template. **v3 (2026-05-17)**: new mandatory "Methodology & Limitations at a Glance" section at the TOP with per-run metrics (papers triaged, deep-extracted, yield, reconciliations triggered, QUADAS distribution, API cost, kappa). [LLM] / [CALC] / [CONSENSUS] badges applied to QUADAS, heterogeneity, key findings, symptom landscape, mechanistic phenotypes. Sections: Methodology, Executive Summary, Methods (Brief), Methodological Quality (QUADAS), Heterogeneity Analysis, Publication Bias Assessment, Key Findings by Calibrated Certainty, Definitional Heterogeneity, Symptom Landscape, Methodology Quality, Bias Audit, Mechanistic Phenotypes, Major Contradictions, Research Gaps & Recommendations, Limitations, References. Uses `{{ topic_title }}` and `{{ cite }}` / `{{ cite_doi }}` / `{{ llm }}` / `{{ calc }}` / `{{ consensus }}` filters. |
| `templates/due_diligence.md.j2` | **Pharma DD report** template. **v3 (2026-05-17)**: small-corpus warning banner when n_deep < 10; Hams & Co. preface; Methodology section; explicit `preface_disclaimer` before the Phase II skeleton; `confidence_in_recommendation` and `threshold_met` visible on the target phenotype. Executive Summary, Committee Briefing (60-second bullets), Target Trial Emulation Landscape, Objective Biomarker Bridges, Clean Baseline Subset, Mechanistic Opportunity Map, Methodological Risk Index, Contradictions Matrix, Recommended Target Phenotype + Phase II Design Skeleton + Deal-breakers, References. Footer: "Hams & Co. Research Division — Literature Synthesis Engine". |
| `templates/executive_summary.md.j2` | **Non-technical 2-page brief** template. Sections: What is the topic, The problem with current research, What this system does, What this system found, Why it matters, Honest caveats. **v3 (2026-05-17)**: mandatory disclosure line at the bottom mirroring the methodology section. No citations in body. Targeted at non-scientist readers (investor / executive / journalist). |
| `templates/report.html.j2` | **Legacy** v1 HTML template. Superseded by the Markdown→HTML path. Kept on disk only. |

---

## `build/`, `dist/`, `package/` — Build artifacts (ephemeral)

| Path | What it is |
|---|---|
| `build/longcovid/` | PyInstaller intermediate working directory. Recreated every build. Safe to delete. |
| `dist/LongCovidResearch.exe` | **The compiled .exe** (~94 MB). Single-file PyInstaller bundle. This is what gets zipped and shipped. |
| `package/LongCovidResearch.exe` | Staging copy for the zip. |
| `package/README_instrucciones.txt` | Staging copy of the user instructions. |

---

## Helper scripts (development-time only)

| Path | What it is |
|---|---|
| `patch_paths.py` | One-shot script run during initial Windows build setup to rewrite `Path("data/...")` → `app_data("data/...")` and `Path("config/...")` → `resource("config/...")` across all pipeline modules. Idempotent. Already executed; safe to delete. |
| `patch_encoding.py` | One-shot script that added `encoding="utf-8"` to all `Path.open()` calls (Windows defaults to cp1252 which fails on `≥` characters in medical abstracts). Already executed; safe to delete. |
| `patch_readtext.py` | Same kind of one-shot for `Path.read_text()` / `Path.write_text()`. Already executed; safe to delete. |
| `poll_status.ps1` | PowerShell helper used during testing to poll `/status` endpoint every 12s. Not part of the shipped product. |
| `server.out` / `server.err` | Captured stdout/stderr of the most recent dev Flask run. Useful for debugging. |

---

## `__pycache__/` and pyc files

Throughout `__pycache__/` and `.pyc` files: standard Python bytecode cache. Safe to delete; recreated on import.

---

## `docs/` — Project documentation

| Path | What it is |
|---|---|
| `docs/SESSION_SNAPSHOT.md` | Chronological session log. Carries forward across sessions. Updated 2026-05-17 with the v3 chronology (Phases 7 and 8). |
| `docs/FILE_TREE.md` | This file. Per-file reference of the entire codebase. |
| `docs/FILE_TREE.txt` | Raw `find . -type f` output of the project tree (~80 entries). |
| `docs/SPEC_V3_DISCREPANCIES.md` | **NEW v3 (2026-05-17)**. Pre-implementation notes on where the Master Improvement Spec v3.0 diverged from the actual codebase (no `run_pipeline.py`, WeasyPrint already retired, Phase 2 already bypassed, no UMLS API key, etc.). Required by the spec's Rule of Gold #5 (document before overwriting). |

---

## Not in tree but important to know about

- **`.venv/`** exists but `find` did not enter it (Windows symlink to `C:\Users\Hamsa\AppData\...`). Contains `uv`-managed Python 3.12.10 + all dependencies. To recreate: `uv sync` from project root.
- **`%LOCALAPPDATA%\HamsCoResearch\LongCovid\`** is where the .exe writes runtime data (papers.jsonl, fulltext_cache.jsonl, triage_results.jsonl, deep_results.jsonl, deep_failures.jsonl, analysis.json, reports/...) when end users run it. Separate from this project tree.
- **`C:\Users\Hamsa\OneDrive\Escritorio\LongCovidResearch.zip`** is the most recent shippable artifact (92.8 MB), ready to send by WhatsApp/email.
- **WSL canonical at `~/projects/long-covid-pipeline/`** still exists but is stale — has the structure from earlier sessions before the Flask refactor and methodology-v2 upgrade.
