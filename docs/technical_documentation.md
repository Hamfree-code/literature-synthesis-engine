# Technical Documentation — Literature Synthesis Engine

**Author:** Hamsa
**Last revised:** 2026-06-16 (v3.1 — multi-provider extraction engine)
**Build version:** v3.1
**Scope:** the canonical Windows build at `C:\Users\Hamsa\longcovid-app-build\` (the earlier WSL source at `~/projects/long-covid-pipeline/` is stale and is not described here).

---

> **v3.1 update (2026-06-16) — read this first.** The extraction engine is now
> **multi-provider**. Where this document says "Haiku triage", triage now runs on
> **Gemini Flash**; where it says the two deep-extraction reviewers are both
> Sonnet, **Reviewer A is Claude Sonnet 4.6 and Reviewer B is Gemini Pro**, and
> the **arbiter is Claude Opus 4.8** (the `temperature` parameter is omitted
> because Opus 4.8 deprecated it). Gemini work runs as concurrency-bounded async
> calls in `utils/gemini_client.py`; Claude work stays on the Anthropic Batch
> API with a resumable batch registry. A third ingestion source, **OpenAlex**,
> joins PMC and medRxiv in Phase 1. A preflight budget gate (`utils/preflight.py`,
> `settings.MAX_SPEND_USD`) aborts before any paid batch. The
> `config/prompts/*_haiku.txt` / `*_sonnet.txt` filenames are kept for path
> stability and are now provider-agnostic templates. The v3.0 prose below is
> retained for the parts that did not change (chunking, normalisation, stats,
> rendering).

## 1. System overview

A two-process Python application that:

1. Runs a **Flask web UI** in the parent process on `localhost:7432-7434` (`app_server.py`) and auto-opens the user's browser.
2. Spawns the **analysis pipeline in a separate process** via `multiprocessing.Process` (spawn context) — runs `pipeline.runner.execute_industrial_pipeline()`.
3. Mirrors worker events from a `multiprocessing.Queue` into a thread-local queue via a relay thread, then streams them over **Server-Sent Events** on `/stream`.
4. Writes three PDFs (research / due-diligence / executive summary) to the user's desktop on completion.

The whole stack is bundled into a single Windows executable via PyInstaller (`longcovid.spec`, `console=False`, ~96 MB). Credentials are baked at build time via `bundled_credentials.py` (the end user never enters API keys). Runtime data lives in `%LOCALAPPDATA%\HamsCoResearch\LongCovid\` so the executable can run from any directory without write conflicts.

The analysis pipeline itself runs through six numbered phases (Phase 2 deprecated, see §4).

**v3 architectural changes (2026-05-17):**
- Pipeline split from Flask process — no more GIL contention during NumPy / reportlab work.
- Deep extraction is now a two-step process (Reviewer A + Reviewer B + Arbiter).
- PMC XML is parsed semantically by `sec-type` rather than flat-truncated.
- Every extracted entity is mapped to a UMLS CUI + MeSH heading.
- All reports carry `[LLM]` / `[CALC]` / `[CONSENSUS]` badges and a mandatory methodology section at the top.

---

## 2. Stack & dependencies

| Layer | Library |
|---|---|
| Language | Python 3.12 (pinned `>=3.12,<3.13` — ASReview and some methodology libs lag on 3.14) |
| Web server | Flask + Werkzeug (single-process, `threaded=True`) |
| Templating | Jinja2 (`env.filters["cite"]` / `env.filters["cite_doi"]` registered by Phase 6) |
| LLM SDK | `anthropic` (sync + Batch API) |
| HTTP | `httpx` (HTTP/2, async, `Limits(max_connections=3)`) |
| Retries | `tenacity` (`stop_after_attempt(5)`, `wait_exponential(min=2, max=60)`) |
| Config | `pydantic-settings` (`BaseSettings`, `env_file=".env"`) |
| Database | `supabase` (REST), pgvector extension on the DB side |
| Ingest | `Bio.Entrez` (NCBI E-utilities), `lxml.etree` (PMC XML) |
| Numerics | `numpy` (meta-analysis), `sklearn.cluster` (loaded but underused) |
| Reporting | `markdown` (MD→HTML), `reportlab` (HTML→PDF) — note: **not** WeasyPrint, which was rejected because GTK3 is brittle on Windows |
| Logging | `rich` (console output) |
| Bundling | PyInstaller (`onefile`, no console window) |

The full hidden-import list is in `longcovid.spec` under the `hiddenimports=` array. The exclude list (`weasyprint`, `asreview`, `matplotlib`, `tornado`, `PyQt5`, `PyQt6`, `PySide2`, `PySide6`, `tkinter`) is deliberate — those packages either fail at bundle time or are not needed in the production flow.

---

## 3. File layout

Relative to the project root `C:\Users\Hamsa\longcovid-app-build\`:

```
.
├── app_server.py              # Flask entry point — bundled as the .exe
├── app_paths.py               # Cross-platform RESOURCE_DIR / APP_DATA_DIR / USER_DESKTOP
├── app_pdf.py                 # Markdown→PDF (reportlab; replaces WeasyPrint)
├── app_gui.py                 # Legacy tkinter UI — not bundled, kept on disk only
├── bundled_credentials.py     # Baked API keys + override-on-empty install()
├── longcovid.spec             # PyInstaller spec
├── README_instrucciones.txt   # End-user instructions (Spanish), shipped in the zip
├── config/
│   ├── settings.py            # pydantic-settings, methodology constants + v3 toggles
│   ├── schema.sql             # v1 Supabase schema
│   ├── schema_v2_migration.sql# v2 add columns + provenance table
│   ├── schema_v3_migration.sql# v3 arbiter + UMLS + Kappa + runs tables (NEW)
│   └── prompts/
│       ├── triage_haiku.txt
│       ├── extraction_sonnet.txt    # v3: section-aware
│       ├── arbiter_sonnet.txt       # NEW v3 — two-step reconciliation prompt
│       ├── synthesis_sonnet.txt
│       ├── due_diligence_sonnet.txt # v3: conservatism rules
│       ├── executive_summary_sonnet.txt
│       ├── heterogeneity_analysis.txt
│       └── contradiction_check.txt
├── pipeline/
│   ├── runner.py              # NEW v3 — multiprocessing worker entry point
│   ├── phase1_ingest.py       # PMC + medRxiv ingest, fulltext fetch, MeSH expansion
│   ├── phase2_filter.py       # ASReview (bypassed; Phase 3a auto-promotes)
│   ├── phase3_extract.py      # Haiku triage + Sonnet two-step arbiter
│   ├── phase4_store.py        # Supabase persistence + arbiter + UMLS rows
│   ├── phase5_analyze.py      # Aggregators + meta-analysis + 3 Sonnet syntheses
│   └── phase6_report.py       # MD→HTML→PDF, CitationManager, CrossRef, badges
├── templates/
│   ├── app.html               # Dark-luxury web UI (single page)
│   ├── report.md.j2           # Research report (v3: methodology section + badges)
│   ├── due_diligence.md.j2    # Pharma DD brief (v3: conservatism warnings)
│   └── executive_summary.md.j2# 2-page non-technical brief
├── utils/
│   ├── claude_client.py       # Anthropic wrapper + sanitize_custom_id + batch helpers
│   ├── supabase_client.py     # Supabase singleton + bulk inserters
│   ├── run_context.py         # Per-run topic + topic-change auto-wipe (v3)
│   ├── xml_parser.py          # NEW v3 — semantic PMC <sec sec-type=...> bucketing
│   ├── umls_normalizer.py     # NEW v3 — UMLS CUI + MeSH heading via tool calling
│   ├── validation_engine.py   # NEW v3 — Cohen's Kappa / RMSE / Pearson engine
│   ├── checkpointing.py       # File-based phase idempotency
│   └── logging_setup.py       # Rich logging (mostly unused in the Flask path)
└── docs/
    ├── SESSION_SNAPSHOT.md
    ├── FILE_TREE.md
    └── SPEC_V3_DISCREPANCIES.md  # NEW v3 — spec-vs-code pre-implementation notes
```

Build artifacts (`build/`, `dist/`, `package/`) are reproduced from source by PyInstaller and are safe to delete.

---

## 4. Pipeline phases

### Phase 1 — Ingest (`pipeline/phase1_ingest.py`)

Two sources merged via DOI dedup:

1. **PMC** via NCBI E-utilities. `esearch.fcgi` returns up to 10,000 IDs for the query; `efetch.fcgi?rettype=xml` returns metadata per paper. XML parsing (`lxml.etree`) extracts title, abstract, authors, year, journal, DOI, PMC ID. Concurrency: `httpx.Limits(max_connections=3)` + `asyncio.Semaphore(3)` + 0.4s per-request sleep (without these limits, the previous integration hit NCBI 429s at ~6 req/s and lost ~50% of papers).
2. **medRxiv** via `https://api.biorxiv.org/details/medrxiv/{start}/{end}/{cursor}/json`. The API returns **30 papers per page** (not 100, as the previous implementation assumed). Pagination uses the cursor offset and exits when `cursor >= messages[0].total`. Date range is split into 90-day chunks to stay within the API's interval handling. Filtering is client-side: title + abstract are concatenated, lowercased, and matched against any term in the query list.

For non-Long-COVID topics, `expand_search_terms(topic)` makes a single Haiku call (~$0.001) that returns up to 15 MeSH headings, synonyms, abbreviations, and related conditions as a JSON array. The parser handles Haiku's tendency to wrap output in ` ```json ` fences. These terms are OR-joined into the PubMed query and reused as the medRxiv keyword set.

Output: `data/raw/papers.jsonl` (one record per line).

### Phase 2 — ASReview filter (`pipeline/phase2_filter.py`) — bypassed

Exists in the codebase but is not exercised by the .exe flow. Phase 3a has an auto-promote path: if `data/filtered/relevant_papers.jsonl` does not exist, it copies `data/raw/papers.jsonl` to that location and continues.

### Phase 3a — Gemini Flash triage (`pipeline/phase3_extract.run_triage`)

**v3.1:** triage runs on **Gemini Flash** as concurrency-bounded async calls
(`utils.gemini_client.gather_json`, capped by `settings.GEMINI_CONCURRENCY`)
rather than a Claude Haiku Anthropic batch. Failed abstracts are recorded to
`triage_failures.jsonl`. The triage prompt asks the model to return a fixed
JSON object per abstract: `is_long_covid_focused`, `study_design`, `sample_size`, `long_covid_definition_weeks`, `main_symptoms`, `main_biomarkers`, `risk_factors_identified`, `population_country`, `headline_finding`, `extraction_confidence`, `confidence_flags`.

The field names `is_long_covid_focused` and `long_covid_definition_weeks` are legacy holdovers from the original Long COVID build. They semantically mean "is topic-focused" and "definition threshold weeks"; renaming is deferred because the Supabase schema references them.

Batch IDs use a sanitised version of the paper ID (`sanitize_custom_id()` replaces every non-alphanumeric character with `_` and truncates to 64 chars, satisfying the Anthropic Batch API regex `^[a-zA-Z0-9_-]{1,64}$`). A `cid_to_pid` map is held in memory to recover the original ID when parsing batch results.

Output: `data/filtered/triage_results.jsonl`.

### Phase 3b — Selection (`pipeline/phase3_extract.select_for_deep_analysis`)

Ranks triaged papers by `(sample_size or 0) × design_weight × (extraction_confidence or 0.5)` and returns the top N IDs. Design weights:

```python
{"RCT": 1.0, "cohort": 1.0, "meta_analysis": 1.2}
# anything else → 0.5
```

Only papers with `is_long_covid_focused=True` are eligible.

### Phase 3c — PMC OA full-text enrichment (`pipeline/phase1_ingest.enrich_with_fulltext` → `utils.xml_parser`)

**v3 (2026-05-17): semantic XML section parsing.** For each selected PMC ID, fetches the full structured XML via `efetch.fcgi?rettype=full&retmode=xml`. The parser is `utils.xml_parser.extract_structured_sections()`:

1. Strips all `<ref-list>` nodes BEFORE extracting text (without this, the bibliography injects 30k+ tokens of irrelevant references into Sonnet's input).
2. Walks `<sec>` elements and buckets each by `sec-type` attribute or title keyword into one of five named blocks: `metadata` (abstract + intro), `methods`, `results`, `discussion_limitations`, `conflicts_funding`.
3. Each bucket has its own char cap: 12k / 30k / 40k / 35k / 3k. Total fits well under the 120k flat cap used in v2 but **always preserves discussion + limitations + conflicts + funding** — the sections that v2 frequently truncated.
4. `sections_to_compact_text()` concatenates the dict into a single string with explicit `=== <SECTION NAME> ===` delimiters so the Sonnet prompt can attend to specific blocks.

Results are written to `data/raw/fulltext_cache.jsonl` and to the `papers.full_text` column in Supabase. Coverage on the 2026-05-16 Long COVID run was 98.4% of selected papers; on the 2026-05-17 Narcolepsy run it was 100% (3 / 3).

### Phase 3d — Cross-provider two-step extraction with arbiter (`pipeline/phase3_extract.run_deep`)

**v3.1 (2026-06-16):** Each paper goes through TWO independent reviewers from
different model families and a third reconciliation pass. **Reviewer A = Claude
Sonnet 4.6** (Anthropic Batch API, temp 0.1); **Reviewer B = Gemini Pro** (async
`gather_json`, temp 0.3, resume cache `reviewer_b_cache.jsonl`); **Arbiter =
Claude Opus 4.8** (Anthropic Batch API, no `temperature` — deprecated on Opus).
A and B can no longer share a single batch. When both reviewers succeed the
arbiter reconciles; when only one succeeds, its output is used unilaterally with
`reconciliation_triggered=false`. Toggle: `settings.ARBITER_ENABLED` (default
`True`); when false the pipeline falls back to a single Sonnet pass.

**Flow (when ARBITER_ENABLED is True):**

```
                                ┌─────────────────────────────┐
                                │  Reviewer A   (temp 0.1)    │──┐
                                ├─────────────────────────────┤  │
                                │  Reviewer B   (temp 0.3)    │──┼──→  Arbiter  (temp 0.0)
                                └─────────────────────────────┘  │     • reconciles disagreements
                                  (one Batch — 2N requests)      │     • re-validates provenance
                                                                 │     • emits llm_judgment_flags
                                                          (second Batch — N requests)
```

The reviewer prompt is `config/prompts/extraction_sonnet.txt` (unchanged from v2 schema; section markers added in the FULL TEXT preamble). The arbiter prompt is the new `config/prompts/arbiter_sonnet.txt`. Reconciliation rules:

1. **Quantitative disagreements** are resolved against the source text; if neither reviewer matches the source, the arbiter extracts directly and sets `reconciliation_triggered: true`.
2. **QUADAS / GRADE risk-of-bias** discordance: the arbiter audits the provenance quote — if the quote does not objectively justify a methodological penalty, the lower-risk rating prevails.
3. **Provenance validation**: the arbiter discards fabricated quotes; if more than half of a reviewer's provenance is discarded, `reconciliation_triggered: true`.
4. **LLM judgment flagging**: every leaf field in the output carries a parallel boolean in `llm_judgment_flags` — `true` for inferences, `false` for direct quotes or deterministic computations (e.g. `quadas_total`).
5. **Confidence propagation**: final `extraction_confidence` is `min(A, B)` — disagreement IS a signal of uncertainty.

The arbiter output is the canonical extraction stored downstream. The two raw reviewer JSONs are preserved as `reviewer_a_raw` and `reviewer_b_raw` for audit. Top-level fields produced (same as v2, plus three new):

- `study_metadata`, `factual_extraction`, `methodology_appraisal`, `bias_audit`, `phenotype_mapping`, `calibration`, `provenance`, `quality_assessment`, `effect_sizes_classified`, `moderators`
- **`reconciliation_triggered`** (boolean)
- **`arbiter_notes`** (1–3 sentences explaining what was reconciled)
- **`llm_judgment_flags`** (dict: field path → bool)

**Cost:** 3× per deep-extracted paper. Use `ARBITER_ENABLED=False` for cost-conscious runs (single-pass at temp 0.1 only).

**Fallback:** when only one reviewer succeeds (the other failed API or JSON parse), the surviving reviewer's output is used unilaterally with `reconciliation_triggered: false` and a note in `arbiter_notes`.

Failures are captured in `data/filtered/deep_failures.jsonl` with `reason ∈ {reviewer_a_*_AND_b_failed, arbiter_*}` and a 300-char detail prefix.

Output: `data/filtered/deep_results.jsonl`.

### Phase 3d-bis — UMLS / MeSH normalisation (`utils.umls_normalizer`)

**NEW v3 (2026-05-17).** After deep extraction completes, one Haiku tool call is made per paper to attach a UMLS Concept Unique Identifier (CUI) and a MeSH heading to each free-text biomedical entity:

- All keys from `factual_extraction.symptoms_prevalence` → entity_type `phenotype`
- All keys from `factual_extraction.biomarker_findings` → entity_type `biomarker`
- Every `risk_factors_quantified[].factor` → entity_type `risk_factor`
- `phenotype_mapping.primary_mechanism` and each `secondary_mechanisms` → entity_type `mechanism`

The tool schema (`normalize_biomedical_entities`) returns:

```json
{
  "normalized_entities": [
    {
      "verbatim_text": "fatigue",
      "entity_type": "phenotype",
      "umls_cui": "C0015672",
      "mesh_heading": "Fatigue"
    },
    ...
  ]
}
```

Every record carries `llm_judgment=true` because we do not have a UMLS REST API key in the bundle — the CUIs come from Haiku's training-data knowledge of the UMLS Metathesaurus. Accuracy is high for common concepts (fatigue, dyspnoea, cognitive impairment) and unverified for rare ones. Toggle: `settings.UMLS_NORMALIZATION_ENABLED` (default `True`).

Output: `data/filtered/normalized_entities.jsonl`. Phase 4 inserts these rows into the new `extracted_phenotypes` table.

### Phase 4 — Persist to Supabase (`pipeline/phase4_store.py`)

Bulk-upserts:

- `papers` (id, source, title, authors[], year, journal, abstract, full_text, url, ingested_at).
- `extractions` (paper_id + extraction_level → JSONB columns for symptoms / biomarkers / risk_factors, plus the v2-added scalar columns for GRADE, NOS, calibration, phenotype mapping, pandemic era). **v3 additions**: `reconciliation_triggered`, `arbiter_notes`, `llm_judgment_flags`, `reviewer_a_raw`, `reviewer_b_raw`.
- `provenance` (paper_id FK, extraction_level, field_name, claim, quote, section, page, confidence) — bulk-inserted here (not in Phase 3d, to avoid foreign-key violations).
- `extracted_phenotypes` **(NEW v3)** — one row per normalised entity, with `verbatim_text`, `umls_cui`, `mesh_heading`, `entity_type`, `llm_judgment` (always `true` because we lack a UMLS API key).
- `contradictions` (typed since v2 — `contradiction_type`, `likely_resolution`, `detection_confidence`, `provenance_a`, `provenance_b`).

The `map_deep_to_schema()` function performs lossy projection from the rich JSON output of Phase 3d into the flatter Supabase columns; rich nested structures are stored as JSONB.

### Phase 5 — Cross-analysis (`pipeline/phase5_analyze.py`)

The largest module (~840 lines). Two halves:

**A. Numeric aggregators** (no LLM):

- `compute_symptom_consensus()` — Counter over triage `main_symptoms`, top 30 with count + percentage.
- `compute_definition_heterogeneity()` — distribution of `long_covid_definition_weeks` across the corpus.
- `compute_study_design_distribution()` — Counter over `study_design`.
- `compute_methodology_quality()` — GRADE distribution, NOS mean, 6-axis bias audit counts, phenotype counts.
- `propagate_uncertainty()` — projects per-paper `calibrated_certainty` × `extraction_confidence` into per-symptom consensus tiers using rules: any "contradicted" → CONTRADICTED; n≥5 and (established+probable)/n ≥ 0.6 → ESTABLISHED or PROBABLE; n≥2 → POSSIBLE; otherwise SPECULATIVE.
- `collect_quadas_scores()` + `quadas_distribution` summary (mean / median / range / acceptable_n / excluded_n at cutoff 13).
- `collect_effect_sizes()` with variance approximation `Var(r) ≈ (1 - r²)² / (n - 1)`.
- `_pool_random_effects()` — DerSimonian–Laird τ² estimator with inverse-variance weighting in pure numpy. Returns pooled r, SE, 95% CI, I², Q, τ², n_studies, RE weights.
- `leave_one_out_analysis()` — re-pool excluding each paper; flag as "influential" if removal shifts the pooled estimate by ≥ 10% (configurable via `LEAVE_ONE_OUT_INFLUENCE_THRESHOLD`).
- `assess_publication_bias()` — Egger's regression of standardised effect on precision + simple trim-and-fill estimate; activates only at n ≥ 10 studies per factor. Returns funnel symmetry classification, Egger p, intercept, estimated missing studies, adjusted effect, risk tier.
- `select_model()` — chooses the right model name based on I² thresholds:
  - I² < 25% → `fixed_effects`
  - 25–74% → `random_effects_recommended`
  - 75–89% → `random_effects_mandatory`
  - ≥ 90% → `random_effects_critical` (triggers extra Sonnet call + forest plot)
- `heterogeneity_critical_synthesis()` — for I² ≥ 90% outcomes, one Sonnet call with the moderator JSON for ANOVA-approximation moderator analysis.
- `forest_plot_text()` — renders a text forest plot with weights, 95% CIs, influential-paper star, Q / I² / τ² footer, model line, and caution statement.

**B. Three Sonnet narrative passes**:

- `call_synthesizer()` → research report JSON (executive_summary, field_state_one_line, key_findings_summary by certainty tier, definition_problem_narrative, symptom_landscape_narrative, methodology_quality_overview, bias_audit_summary, phenotype_breakdown, contradictions, gaps_and_recommendations, limitations_of_this_analysis).
- `call_due_diligence()` → pharma DD JSON (investment_thesis_one_line, target_trial_emulation_inventory, objective_biomarker_bridges, clean_baseline_subset, mechanistic_opportunity_map, methodological_risk_index, contradictions_matrix, recommended_target_phenotype + phase_ii_design_skeleton + deal_breakers, committee_briefing_one_pager).
- `call_executive_summary()` → 2-page non-technical brief JSON. Forbidden-jargon list in the prompt: GRADE, QUADAS, I², MCID, etc.

All three calls use the slimmed inputs (`_slim_deep()` caps each paper at ~600 chars; `_slim_aggregates()` caps the consensus tables at 50 entries) to stay under the 200K context window. Raw output is also persisted to `data/filtered/{synthesis,due_diligence,executive_summary}_raw.txt` for debugging.

Output: `data/filtered/analysis.json` with `aggregates`, `synthesis`, `due_diligence`, `executive_summary`, `meta`.

### Phase 6 — Report rendering (`pipeline/phase6_report.py`)

Three reports built from the same `analysis.json` via three different Jinja2 templates:

1. `templates/report.md.j2` — full research report with QUADAS table, forest plots, publication bias assessment, calibrated certainty tiers, definitional heterogeneity, symptom landscape, methodology quality, bias audit, mechanistic phenotypes, contradictions, gaps, limitations.
2. `templates/due_diligence.md.j2` — pharma DD brief.
3. `templates/executive_summary.md.j2` — 2-page non-technical brief.

The `CitationManager` class replaces `(CITE:DOI)` markers with `[N]` ordering by first citation. Defensive logic recovers from LLM-fabricated DOIs:

- Recognises `(PMC\d{5,})` anywhere in the token and matches against the in-corpus PMC index.
- Strips fake DOI prefixes like `10.1101/2025.05.` and retries with the suffix.
- Drops obvious placeholders (`conceptual`, `not a paper`, `placeholder`, `unspecified`).
- **CrossRef fallback** (added 2026-05-17): any cited DOI not present in `papers_by_id` triggers a CrossRef API lookup (`api.crossref.org/works/{doi}`). The returned metadata is formatted as a Vancouver string via `crossref_vancouver()`. A module-level `_crossref_cache` dict shares results across the three reports.

Each report is written as Markdown → HTML (via `markdown.markdown(extensions=["tables","fenced_code","toc"])`) → PDF (via `app_pdf.markdown_to_pdf()` using reportlab). The PDF is copied to `USER_DESKTOP` (`~/OneDrive/Escritorio/` if present, else `~/Desktop/`) with the filename `research_<topic_slug>_<date>.pdf`.

---

## 5. Database schema

`config/schema.sql` defines the v1 schema; `config/schema_v2_migration.sql` extends it without dropping tables.

```sql
-- v1
create extension if not exists vector;

create table papers (
  id text primary key,
  source text not null,
  title text not null,
  authors text[],
  year int,
  journal text,
  abstract text,
  full_text text,
  url text,
  ingested_at timestamptz default now()
);

create table extractions (
  paper_id text references papers(id) on delete cascade,
  extraction_level text not null,
  long_covid_definition text,
  sample_size int,
  population text,
  study_design text,
  symptoms jsonb,
  biomarkers jsonb,
  risk_factors jsonb,
  limitations text[],
  authors_conclusions text,
  methodology_quality int,
  extracted_at timestamptz default now(),
  primary key (paper_id, extraction_level)
);

create table embeddings (
  paper_id text references papers(id) on delete cascade primary key,
  embedding vector(1024)
);

create table contradictions (
  id serial primary key,
  topic text not null,
  paper_a text references papers(id),
  paper_b text references papers(id),
  claim_a text,
  claim_b text,
  likely_cause text,
  detected_at timestamptz default now()
);

create index on embeddings using ivfflat (embedding vector_cosine_ops);
create index on papers (year);
create index on extractions (extraction_level);

-- v2 additions
alter table extractions
  add column if not exists extraction_confidence float,
  add column if not exists confidence_flags jsonb,
  add column if not exists calibrated_certainty text,
  add column if not exists calibrated_certainty_rationale text,
  add column if not exists uncertainty_sources jsonb,
  add column if not exists probabilistic_summary text,
  add column if not exists grade_certainty text,
  add column if not exists grade_rationale text,
  add column if not exists nos_score int,
  add column if not exists bias_audit jsonb,
  add column if not exists phenotype_mapping jsonb,
  add column if not exists pandemic_era text;

create table if not exists provenance (
  id serial primary key,
  paper_id text references papers(id) on delete cascade,
  extraction_level text not null,
  field_name text not null,
  claim text not null,
  quote text not null,
  section text,
  page int,
  confidence float,
  created_at timestamptz default now()
);

create index if not exists idx_provenance_paper on provenance (paper_id);
create index if not exists idx_provenance_field on provenance (field_name);

alter table contradictions
  add column if not exists contradiction_type text,
  add column if not exists likely_resolution text,
  add column if not exists detection_confidence float,
  add column if not exists provenance_a jsonb,
  add column if not exists provenance_b jsonb;

-- v3 additions (Master Improvement Spec v3.0 — 2026-05-17)
alter table extractions
  add column if not exists reconciliation_triggered boolean default false,
  add column if not exists reviewer_a_raw jsonb,
  add column if not exists reviewer_b_raw jsonb,
  add column if not exists arbiter_notes text,
  add column if not exists llm_judgment_flags jsonb;

create table if not exists extracted_phenotypes (
  id            serial primary key,
  paper_id      text references papers(id) on delete cascade,
  verbatim_text text not null,
  umls_cui      text,
  mesh_heading  text,
  entity_type   text,
  llm_judgment  boolean default true,
  created_at    timestamptz default now()
);

create table if not exists human_ratings (
  id             serial primary key,
  paper_id       text references papers(id) on delete cascade,
  rater_id       text not null,
  field_name     text not null,
  field_kind     text not null,
  rating_value   text not null,
  rated_at       timestamptz default now(),
  unique (paper_id, rater_id, field_name)
);

create table if not exists runs (
  id                       uuid primary key default gen_random_uuid(),
  topic                    text not null,
  mesh_terms               text,
  run_date                 date not null default current_date,
  n_papers_ingested        int,
  n_papers_triaged         int,
  n_papers_deep            int,
  n_provenance_entries     int,
  n_reconciliations        int,
  api_cost_usd             numeric(10,2),
  runtime_seconds          int,
  grade_distribution       jsonb,
  created_at               timestamptz default now(),
  unique (topic, run_date)
);

alter table papers add column if not exists run_id uuid references runs(id);
alter table extractions add column if not exists run_id uuid references runs(id);
```

The `embeddings` table is provisioned but not populated by the current pipeline; it is reserved for a future similarity-search feature.

---

## 6. Methodology constants

From `config/settings.py`. These mirror the analytical standard of Siciliano et al., *Movement Disorders* 2024 (DOI: 10.1002/mds.29649):

| Constant | Value | Purpose |
|---|---|---|
| `QUADAS_CUTOFF` | 13 | Papers with QUADAS ≤ 13 are excluded from quantitative synthesis |
| `QUADAS_MAX` | 19 | Maximum possible QUADAS score |
| `HETEROGENEITY_LOW_THRESHOLD` | 25.0 | I² < 25% → fixed effects |
| `HETEROGENEITY_MODERATE_THRESHOLD` | 50.0 | (information-only band; the actual decision uses LOW/HIGH/CRITICAL) |
| `HETEROGENEITY_HIGH_THRESHOLD` | 75.0 | I² 75–89% → random effects mandatory |
| `HETEROGENEITY_CRITICAL_THRESHOLD` | 90.0 | I² ≥ 90% → random effects + moderator analysis + forest plot |
| `MIN_STUDIES_PUBLICATION_BIAS` | 10 | Egger's regression / trim-and-fill require ≥ 10 studies |
| `LEAVE_ONE_OUT_INFLUENCE_THRESHOLD` | 0.10 | ≥ 10% shift in pooled estimate → flag as influential |
| `EFFECT_SIZE_NEGLIGIBLE` | 0.10 | Cohen r < 0.10 → negligible |
| `EFFECT_SIZE_WEAK` | 0.29 | Cohen r 0.10–0.29 → weak |
| `EFFECT_SIZE_MODERATE` | 0.49 | Cohen r 0.30–0.49 → moderate (≥ 0.50 strong, implicit) |
| `MAX_PAPERS` | 5000 | Default Phase 1 cap |
| `MAX_DEEP_ANALYSIS` | 500 | Default Phase 3d cap |
| `BATCH_SIZE` | 100 | (Reserved; current code uses 1000 chunks for triage batches) |
| `ARBITER_ENABLED` | True | **NEW v3** — When True, each paper is extracted by two Sonnet reviewers + arbiter; triples Sonnet cost. |
| `UMLS_NORMALIZATION_ENABLED` | True | **NEW v3** — When True, one Haiku tool call per paper maps entities to UMLS CUI + MeSH heading. |

Models pinned in code: `ANTHROPIC_SONNET_MODEL = "claude-sonnet-4-6"` (Reviewer A), `ANTHROPIC_OPUS_MODEL = "claude-opus-4-8"` (arbiter), `ANTHROPIC_HAIKU_MODEL = "claude-haiku-4-5-20251001"` (UMLS normalisation tool call), `GEMINI_FLASH_MODEL = "gemini-3.5-flash"` (triage), `GEMINI_PRO_MODEL = "gemini-3.1-pro"` (Reviewer B).

---

## 7. Adapting to a new disease domain

The pipeline is parameterised on a single string: the `topic` passed through the Flask UI (or directly to `phase1_ingest.run(topic=..., mesh_terms=...)`). What changes per topic:

1. **PubMed query** — `build_query()` constructs `("<topic>"[Title/Abstract] OR "<synonym_1>"[Title/Abstract] OR …) AND ("2000"[PDAT] : "3000"[PDAT])`. For Long COVID (the default), a hand-tuned query is used; for any other topic, the synonym list comes from `expand_search_terms()` via one Haiku call.
2. **medRxiv keyword filter** — the same synonym list is used to filter biorxiv's date-range response client-side.
3. **All prompt templates** substitute `{topic_title}` (title-cased) and `{topic}` (lowercase) at render time using `utils.run_context.topic_title()` / `topic_lower()`. Both the triage and the deep-extraction prompts contain ~6 instances of these placeholders.
4. **Report titles and slugs** use `utils.run_context.topic_slug()` (alphanumeric-only, lowercased, underscore-separated) for the filename `research_<topic_slug>_<date>.pdf`.

What stays Long-COVID-flavoured:

- The JSON field names `is_long_covid_focused` and `long_covid_definition_weeks` (legacy; semantically "is topic-focused" and "definition threshold weeks"). Renaming requires a Supabase migration.
- The DD prompt's canonical-4 mechanism examples (`viral_reservoir`, `autoimmunity`, `vascular_endothelial`, `autonomic_metabolic`). Sonnet correctly returns topic-appropriate mechanisms when running for non-COVID topics (e.g. `dopaminergic_deficit_basal_ganglia` for Parkinson, `hypocretin_deficiency` for Narcolepsy), but the prompt's example list still names the COVID phenotypes.
- The `definition_heterogeneity` table in the report template is weeks-based; for conditions without a temporal definition (most non-COVID topics) it renders empty.

---

## 8. Configuration

The `.env` file at the repo root (dev mode) or `bundled_credentials.py` (frozen exe) provides:

```env
ANTHROPIC_API_KEY=sk-ant-api03-…
ANTHROPIC_HAIKU_MODEL=claude-haiku-4-5-20251001
ANTHROPIC_SONNET_MODEL=claude-sonnet-4-6
NCBI_API_KEY=…
NCBI_EMAIL=…@example.com
SUPABASE_URL=https://….supabase.co
SUPABASE_KEY=sb_secret_…
MAX_PAPERS=5000
MAX_DEEP_ANALYSIS=500
BATCH_SIZE=100
LOG_LEVEL=INFO
```

`bundled_credentials.install()` uses `if not existing: os.environ[k] = v` instead of `os.environ.setdefault` because Claude Code's parent shell sets `ANTHROPIC_API_KEY=""`, which would have won against `setdefault`.

When running frozen, **never set `WERKZEUG_RUN_MAIN`** — that puts Werkzeug into reloader-child mode, which then crashes with `KeyError: 'WERKZEUG_SERVER_FD'` trying to inherit a file descriptor from a non-existent parent. The startup banner is suppressed via `logging.getLogger("werkzeug").setLevel(logging.ERROR)` instead.

---

## 9. Idempotency and resumption

Each phase writes a marker file `%LOCALAPPDATA%\HamsCoResearch\LongCovid\data\checkpoints\<phase_name>.done` on completion. The `Checkpoint` class in `utils/checkpointing.py` checks for the marker and short-circuits the phase if present.

Phase 1's PMC ingest is resumable at paper level: it indexes `data/raw/papers.jsonl` by `pmc_id` and skips already-fetched papers. The full-text enrichment indexes `data/raw/fulltext_cache.jsonl` by `paper_id` similarly.

To force a clean re-run, delete the checkpoint markers and the corresponding `data/raw/` and `data/filtered/` files.

---

## 10. Known issues (tracked at session close, 2026-05-17 post-v3)

| Severity | Issue | Status |
|---|---|---|
| MEDIUM | PMC XML parser concatenates each paper's article-title with its bibliography text into a single string, which ends up in the `authors[]` field on cited papers and inflates the References section in PDFs. The CrossRef fallback compensates for off-corpus DOIs; the html-escape fix in `app_pdf._inline()` prevents the PDF generation from crashing on the contamination, but the visible artefact remains for in-corpus citations. Fix: scope the XPath to `.//front//article-meta//article-title` and `.//front//contrib[@contrib-type='author']`. | Open |
| MEDIUM | Legacy field names `is_long_covid_focused` and `long_covid_definition_weeks` are still hardcoded in the JSON schema and the database. They functionally mean "is topic-focused" and "definition threshold weeks" but the names are misleading for non-COVID topics. Renaming requires a schema migration. | Deferred |
| LOW | Phenotype canonical-4 (`viral_reservoir`, `autoimmunity`, `vascular_endothelial`, `autonomic_metabolic`) are Long-COVID-specific in the DD prompt's example list. Sonnet correctly returns topic-appropriate mechanisms for non-COVID topics, but the prompt's prose still names the COVID phenotypes. | Open |
| LOW | "Quantitative breakdown" and "Definition heterogeneity" tables in the report still render even when empty, which looks awkward for non-COVID topics. | Open |
| LOW | UMLS CUIs come from Haiku's training data, not the real UMLS REST API. Every CUI carries `llm_judgment=true`. Adding a real UMLS API key would close this gap. | Open / Future |
| LOW | The Cohen's Kappa validation engine works but has no UI yet — humans must insert ratings into the `human_ratings` Supabase table manually, then a separate script can compute the stats. | Deferred |

**Resolved during 2026-05-17 session:**
- medRxiv "returns 0 papers" — biorxiv API returns 30 papers per page, not 100. Fixed.
- Topic-change silent short-circuit — every phase skipped because of stale checkpoints. Fixed via `clear_stale_state_if_topic_changed()`.
- Main research PDF silently failing — reportlab choked on `</=` in PMC reference contamination. Fixed by html-escaping in `_inline()`.
- Anchoring bias in single-pass extraction — replaced with two-step + arbiter.
- Discussion / limitations / conflicts truncation — replaced flat 120k cap with per-section caps.

---

## 11. Performance characteristics

From the two demo runs:

- **Throughput (PMC ingest)**: ~90 papers / minute with `max_connections=3` and 0.4s per-request sleep. Higher concurrency (10) triggered NCBI 429s.
- **Throughput (Haiku triage, Batch API)**: 30 papers in ~1–2 minutes (small batches); 4,666 papers in ~25 minutes on the Long COVID run. Batch API SLA is 24 hours but small batches typically complete in single-digit minutes.
- **Throughput (Sonnet deep, Batch API, v2 single-pass)**: 3 papers in ~3–4 minutes; 470 papers in ~35 minutes.
- **Throughput (Sonnet deep, Batch API, v3 two-step + arbiter)**: two batches per run (reviewer batch with 2N requests + arbiter batch with up to N requests). Wall-clock roughly 2× single-pass because the second batch can only start after the first completes.
- **Cost (Haiku triage)**: ~$0.003 / paper.
- **Cost (Sonnet deep, v2 single-pass)**: ~$0.15 / paper.
- **Cost (Sonnet deep, v3 arbiter)**: ~$0.45 / paper (3× — one per reviewer + one per arbiter call). Prompt caching reduces the cost slightly because the prompt schema is shared between Reviewer A and Reviewer B.
- **Cost (Phase 5 synthesis)**: ~$0.50 total for all three Sonnet passes combined (input + output).
- **Cost (UMLS normalisation, v3)**: ~$0.001 / paper (one Haiku tool call).
- **Total cost example (v3 arbiter)**: Narcolepsy 30 / 3 → ~$2. Mid-size 100 / 50 → ~$23. Full 5000 / 500 → ~$240.
- **Total cost example (v2 single-pass)**: Long COVID 4,666 / 470 → ~$85–100. Same run under v3 arbiter would be ~$240.

The Anthropic Batch API gives a flat 50% discount over the standard rate. Without it, costs double across the board.

---

## 12. Limitations (carry-overs)

- LLM extraction is **single-pass** (no second reviewer with arbitration). Provenance enables verification but does not constitute independent review.
- QUADAS scoring is **LLM-generated**, not the two-reviewer-plus-arbitration standard.
- Random-effects pooling, Egger's regression, and trim-and-fill are **pure-numpy approximations** of formal R / ProMeta 3 implementations. Adequate for cross-paper signal detection; not adequate for regulatory submission.
- PMC OA only — subscription-journal and many recent high-impact papers are not retrievable. Coverage was 98.4% on the Long COVID top-500 selection; the absolute corpus is biased toward open-access journals.
- medRxiv coverage is **client-side filtered**, which is correct but slow for low-match-rate topics (the pipeline may scan thousands of preprints to find tens of matches).
- This is **not a systematic review** (no PRISMA flow, no protocol pre-registration, no formal external risk-of-bias scoring beyond integrated NOS / GRADE per paper). The output is structured cartography of a literature, not a meta-analytic verdict.

---

*Maintained by Hamsa. See `docs/SESSION_SNAPSHOT.md` for chronological session decisions and `docs/FILE_TREE.md` for a per-file reference.*
