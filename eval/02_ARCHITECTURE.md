# 02 — Architecture

## Process model
- `app_server.py` — Flask + Server-Sent Events UI (localhost:7432). Spawns the
  pipeline in a **separate `multiprocessing.Process`** so the GIL-heavy worker
  (numpy/reportlab/Sonnet polling) never blocks the UI. Comms via `mp.Queue`.
- `pipeline/runner.py::execute_industrial_pipeline(q, topic, mesh, max_papers,
  max_deep)` — the worker entry point; runs phases sequentially, emits events.
- Resume: each phase writes a `Checkpoint` marker; a crash resumes from the last
  completed phase. Topic change auto-wipes stale state (`utils/run_context.py`).

## Phase pipeline (data flow)
Files are JSONL/JSON under an app-data dir; each phase reads the previous one's.

| Phase | Module | Input → Output | External |
|---|---|---|---|
| 1 Ingest | `phase1_ingest.py` | query → `papers.jsonl` | NCBI/PMC, OpenAlex, (Unpaywall on enrich) |
| 3a Triage | `phase3_extract.py::run_triage` | abstracts → `triage_results.jsonl` | Anthropic Haiku (Batch) |
| 3c Enrich | `phase1_ingest.py::enrich_with_fulltext` | selected ids → `fulltext_cache.jsonl` | PMC OA, Unpaywall fallback |
| 3d Deep | `phase3_extract.py::run_deep` | full text → `deep_results.jsonl` | Reviewer A=Sonnet, B=Sonnet **or Gemini Flash**, arbiter=**Opus** |
| 3d-bis Norm | `umls_normalizer.py` + `umls_client.py` | entities → `normalized_entities.jsonl` | Anthropic Haiku tool-call, UMLS REST |
| 4 Persist | `phase4_store.py` | → Supabase + `retracted.jsonl` | Supabase, Crossref |
| 5 Analyze | `phase5_analyze.py` | → `analysis.json` | Anthropic Sonnet ×3 synthesis |
| 6 Report | `phase6_report.py` + `enterprise_report.py` | → reports + supplement ZIP | Crossref (citation resolve) |

(Phase 2 = old ASReview filter, **removed** in v3.1; Haiku triage replaced it.)

## Selection logic (triage → deep)
`select_for_deep_analysis` ranks by `sample_size × design_weight ×
extraction_confidence`; takes top `max_deep`. Topic-focus filter uses
`is_topic_focused` (legacy `is_long_covid_focused` still honoured).

## Extraction contract (the spine)
`config/extraction_schema.py` is the **single source of truth**: it defines the
forced Anthropic tool (`submit_extraction` / `submit_reconciled_extraction`).
Tool-use makes malformed JSON structurally impossible; `max_tokens` overflow
triggers a compression-retry, then a Haiku repair pass, then a persisted
`extraction_failed` (never silent loss). Per-attempt log →
`extraction_attempts`.

Reviewer B may run on Gemini Flash (`REVIEWER_B_PROVIDER=gemini`,
`_extract_b_via_gemini`). Gemini has no forced-tool guarantee, so its JSON goes
through the **same Haiku repair pass** before reconciliation — Reviewer B's
output is therefore shaped identically to a Sonnet extraction with no schema
translation. The arbiter model is configurable (`ANTHROPIC_ARBITER_MODEL`,
default Opus); `temperature` is only sent to models that accept it (Opus
4.7/4.8 and Fable reject sampling params). See `07`.

## Key modules (by responsibility)
- Stats: `phase5_analyze.py` (orchestration) + `utils/meta_stats.py` (PyMARE/statsmodels).
- Credibility: `utils/umls_client.py` (CUI verify), `utils/retraction.py` (Crossref).
- Sources: `pipeline/sources/{openalex,unpaywall}.py`.
- Cross-model reviewer: `utils/gemini_client.py` (Gemini Batch API wrapper; lazy
  SDK import so the engine runs unchanged with Gemini off).
- Resilience: `utils/resilience.py` (CircuitBreaker, JsonFileCache, health
  registry). Breakers wired on Crossref, UMLS, OpenAlex, Unpaywall, **Gemini**.
- Reporting: `utils/{run_manifest,report_builders,enterprise_report,export_docx,export_citations}.py`.
- Validation: `utils/validation_engine.py` (Cohen's Kappa / RMSE / Pearson panel).

## Persistence
Supabase (Postgres + pgvector). Schema: `config/schema.sql` + 3 migrations
(`schema_v2`, `schema_v3`, `schema_v31`). Tables: papers, extractions,
provenance, embeddings, contradictions, extracted_phenotypes, human_ratings,
runs, extraction_attempts, umls_cache. Migrations are additive/idempotent.

## Determinism & labeling
Every report header carries a badge: `[LLM]` (model inference), `[CALC]`
(deterministic computation), `[CONSENSUS]` (arbiter-reconciled), `[VERIFIED]`
(CUI confirmed against UMLS). Readers always know what produced a value.
