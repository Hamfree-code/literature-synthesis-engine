# Literature Synthesis Engine

[![Cost](https://img.shields.io/badge/cost%2Frun-%2485--100-blue)]() [![Runtime](https://img.shields.io/badge/runtime-~1h-blue)]() [![Demo corpus](https://img.shields.io/badge/demo-4%2C666%20papers-blue)]() [![Architecture](https://img.shields.io/badge/architecture-v3.0-purple)]() [![License](https://img.shields.io/badge/license-MIT-green)]()

An automated pipeline that ingests open-access scientific literature, performs structured methodological extraction with literal-quote provenance, and emits three calibrated reports: a research synthesis, a pharma due-diligence brief, and a non-technical executive summary.

The system is **disease-agnostic**. Long COVID is the primary demonstration corpus (4,666 papers triaged in ~1 hour); Narcolepsy and Prostatic Neoplasms have been validated as secondary demonstrations on distinct therapeutic areas. Any condition queryable in PubMed Central can be analysed.

**v3.0 (2026-05-17) introduces**: two-step extraction with arbiter reconciliation (eliminates anchoring bias), semantic XML section chunking (no more discussion / limitations / conflicts truncation), UMLS / MeSH normalisation (entity-level ontological linking), Cohen's Kappa validation engine (human-vs-AI agreement), [LLM] / [CALC] / [CONSENSUS] badge auditability (reader knows model inference vs deterministic computation vs arbiter consensus), conservative due-diligence rules (Phase II threshold, confidence integer 0–100, small-corpus warning), and a multiprocessing server architecture that keeps the UI responsive during heavy analysis.

---

## What this does

For a given medical condition, the pipeline:

1. Ingests papers from PubMed Central (NCBI E-utilities) and medRxiv preprints (biorxiv.org date-interval API).
2. Triages every abstract with Claude Haiku 4.5 into a structured JSON (design, sample size, headline finding, extraction confidence, self-flagged caveats).
3. Selects the top N papers by `sample_size × design_weight × extraction_confidence`.
4. Fetches PMC Open Access full text (references stripped, hard cap 120k chars).
5. Deep-extracts each selected paper with Claude Sonnet 4.6 under a two-stage protocol (factual extraction → methodological appraisal), producing NOS, GRADE, MCID assessment, 8-axis bias audit, QUADAS-adapted scoring, Cohen-classified effect sizes, mechanistic phenotype mapping, calibrated certainty tier, and a minimum of 5 literal-quote provenance entries per paper.
6. Persists everything to Supabase (papers / extractions / provenance / contradictions tables, pgvector embeddings).
7. Cross-analyses the corpus: random-effects pooling (DerSimonian–Laird τ²), leave-one-out sensitivity, Egger's regression + trim-and-fill (for ≥10 studies per factor), moderator analysis when I² ≥ 90%, plus three Sonnet synthesis passes (research / DD / executive summary).
8. Renders three Markdown reports → HTML → PDF, each cited in Vancouver style with CrossRef DOI resolution for citations outside the in-corpus paper set.

Nothing is asserted without a traceable source. Every numeric or qualitative claim in the deep-extraction layer is grounded in a literal quote from the paper, stored in a `provenance` table keyed to the paper ID.

---

## What's new in v3.0 (2026-05-17)

| Improvement | Why it matters |
|---|---|
| **Two-step extraction + arbiter** | Each paper is extracted independently by two Sonnet reviewers (temp 0.1 and 0.3) then reconciled by a third (temp 0.0). Anchoring bias is eliminated; disagreements are surfaced as `reconciliation_triggered=true`. |
| **XML section-based semantic chunking** | PMC papers are now bucketed by `<sec sec-type="...">` into methods / results / discussion / limitations / conflicts / funding, each with its own char cap. The discussion and limitations sections — previously truncated at the flat 120k cap — are now always preserved. |
| **UMLS / MeSH normalisation** | Every extracted phenotype / mechanism / biomarker / risk factor is mapped to a UMLS CUI and a MeSH heading via an Anthropic tool call. Cross-paper aggregation by canonical concept instead of free-text string. |
| **Cohen's Kappa engine** | Built-in `utils.validation_engine` computes Kappa (Landis & Koch 1977 bands) / RMSE / Pearson against human ratings stored in the `human_ratings` Supabase table. Quantitative defence against external auditors. |
| **[LLM] / [CALC] / [CONSENSUS] badges** | Every section header in the rendered report carries a superscript tag so the reader instantly knows: `[LLM]` = model inference, `[CALC]` = deterministic statistical computation, `[CONSENSUS]` = arbiter-reconciled. |
| **Conservative due diligence** | Never recommends a Phase II target unless ≥ 2 papers support the mechanism at GRADE Moderate or higher. Every recommendation carries `confidence_in_recommendation` (0–100). Auto-banner when `n_deep < 10`. Mandatory hypothesis-generating disclaimer before any Phase II skeleton. |
| **Methodology at the top** | Mandatory "Methodology & Limitations at a Glance" table at the start of every report — yields, reconciliations triggered, QUADAS distribution, API cost, kappa summary. |
| **Multiprocessing server** | Pipeline runs in a separate `multiprocessing.Process` from Flask. NumPy / reportlab / Sonnet polling no longer hold the GIL; the UI stays responsive and SSE events arrive in real time even during heavy analysis. |
| **Topic-change auto-wipe** | When the user re-runs with a different topic, stale checkpoints from the previous run are wiped automatically. Resolves the bug where same-data short-circuit produced empty reports. |

---

## Architecture (v3.0)

```
┌──────────────────────────────────────────────────────────────────────┐
│  LongCovidResearch.exe  (Flask process)                              │
│  • localhost:7432 UI · /stream SSE · spawns worker via mp.Process    │
└────────────────────────┬─────────────────────────────────────────────┘
                         ↓  multiprocessing.Queue
┌──────────────────────────────────────────────────────────────────────┐
│  Worker process — pipeline.runner.execute_industrial_pipeline()       │
│                                                                       │
│  Phase 1   PMC + medRxiv ingest                                       │
│           • Haiku MeSH synonym expansion (non-LC topics)              │
│           • biorxiv 90-day chunks, cursor-vs-total pagination         │
│           • utils.xml_parser → sec-type bucketed sections             │
│                                                                       │
│  Phase 3a  Haiku triage (Batch API, 50% discount)                     │
│                                                                       │
│  Phase 3c  PMC OA full-text enrich (semantic chunking)                │
│                                                                       │
│  Phase 3d  Sonnet two-step extraction (3× cost, anchoring eliminated) │
│           ┌──────────────────────────┐                                │
│           │  Reviewer A   (temp 0.1) │─┐                              │
│           ├──────────────────────────┤ │                              │
│           │  Reviewer B   (temp 0.3) │─┼─→  Arbiter (temp 0.0)        │
│           └──────────────────────────┘ │   • reconciliation_triggered │
│                                        │   • llm_judgment_flags map   │
│                                        │   • provenance re-validated  │
│                                                                       │
│  Phase 3d-bis  UMLS / MeSH normalisation (Haiku tool call per paper)  │
│                                                                       │
│  Phase 4   Supabase upsert                                            │
│           • papers / extractions / provenance                         │
│           • extracted_phenotypes (v3 — CUI + MeSH)                    │
│           • arbiter fields + llm_judgment_flags                       │
│                                                                       │
│  Phase 5   Cross-analysis (numpy meta-analysis + 3× Sonnet)           │
│           • DL τ² random-effects pooling                              │
│           • Leave-one-out (10% influence threshold)                   │
│           • Egger's + trim-and-fill (n ≥ 10)                          │
│           • Moderator analysis when I² ≥ 90%                          │
│           • Synthesis + Conservative DD + Executive summary           │
│                                                                       │
│  Phase 6   Markdown → HTML → PDF                                      │
│           • Methodology section at the top                            │
│           • [LLM] / [CALC] / [CONSENSUS] badges                       │
│           • CrossRef DOI fallback for off-corpus citations            │
│           • html-escape for PMC reference contamination               │
└──────────────────────────────────────────────────────────────────────┘
```

A Phase 2 (ASReview filter) exists in the codebase but is bypassed in the current flow — Phase 3a auto-promotes `papers.jsonl` directly to `relevant_papers.jsonl` when no ASReview output is present.

---

## Quick start

The shipped artifact is a self-contained Windows `.exe` (PyInstaller bundle, ~94 MB) that opens a localhost dark-themed web UI. For development from source:

1. **Install Python 3.12** (the pipeline pins to `>=3.12,<3.13` because some methodology libs lag on 3.14).
2. **Clone and install deps** with `uv sync` (or `pip install` the modules listed in `longcovid.spec` under `hiddenimports`).
3. **Create `.env`** at the repo root with `ANTHROPIC_API_KEY`, `NCBI_API_KEY`, `NCBI_EMAIL`, `SUPABASE_URL`, `SUPABASE_KEY`. A `.env.example` is provided as a template.
4. **Initialise the Supabase schema** by running `config/schema.sql` followed by `config/schema_v2_migration.sql` in the Supabase SQL editor.
5. **Run the server**: `python app_server.py`. A browser tab opens at `http://localhost:7432` with the analysis UI; enter a topic, optional MeSH filter, max papers, max deep, and start.

To rebuild the `.exe`: `pyinstaller --clean --noconfirm longcovid.spec`.

---

## Adapting to a new disease

The pipeline is parameterised by a `topic` string passed through the UI or directly to `pipeline.phase1_ingest.run(topic=..., mesh_terms=...)`. The topic is persisted into `data/raw/run_meta.json` by `utils.run_context.save_run_context()` and read by every later phase through `topic_title()`, `topic_lower()`, `topic_slug()`.

Behaviour per topic:

- For **Long COVID** (the default), `build_query()` in `pipeline/phase1_ingest.py` uses the hand-tuned PubMed query `("long covid"[Title/Abstract] OR "post-acute sequelae"[Title/Abstract] OR "PASC"[Title/Abstract] OR "post-COVID condition"[Title/Abstract])`.
- For **any other topic**, a single Haiku call (`expand_search_terms(topic)`) returns up to 15 MeSH headings, synonyms, abbreviations, and related conditions. These are OR-joined into the PubMed Title/Abstract clause and re-used as the medRxiv client-side filter.
- An optional `mesh_terms` filter (raw PubMed MeSH expression) is AND-joined as an additional constraint.

All prompts in `config/prompts/` substitute `{topic_title}` and `{topic}` placeholders at call time. Legacy field names `is_long_covid_focused` and `long_covid_definition_weeks` are preserved in the JSON schema but semantically mean "is topic-focused" and "definition threshold weeks" — renaming requires a Supabase migration and is deferred.

---

## Demo results

| Metric | Long COVID (2026-05-16, v2) | Narcolepsy (2026-05-17, v2.5) | Prostatic Neoplasms (2026-05-17, v2.5) |
|---|---|---|---|
| Papers triaged | 4,666 | 30 | 1,000 |
| Triage success rate | 4,665 / 4,667 (99.98%) | 30 / 30 (100%) | ~100% |
| Deep-extracted | 470 | 3 | 300 |
| Deep success rate | ~94% (JSON-parse failures on oversized output) | 3 / 3 (100%) | not measured |
| Provenance entries | 7,369 | 50 | per-paper avg ~16 |
| medRxiv coverage | 0 (API integration broken at the time) | 360 scanned, 15 matched | not measured |
| PMC OA full-text yield | 98.4% of selected papers | 100% (3 / 3) | not measured |
| Runtime | ~1 hour | 644.6 s (10.7 min) | ~6.5 min (resumed from checkpoints; deep extraction reused prior result) |
| API cost | ~$85–100 | ~$1.04 | ~$0 marginal (resumed) |
| Reports emitted | research + due-diligence (executive summary added later) | research + DD + executive summary | research + DD + executive summary |

**Pre-v3 demos** ran with single-pass extraction. **v3.0 onward** uses two-step extraction with arbiter, which triples Sonnet deep-extraction cost. Estimated cost under v3 arbiter mode:

| Scale | Triage (Haiku) | Deep (Sonnet × 3 calls) | Synthesis (Sonnet × 3) | Total (Batch API) |
|---|---|---|---|---|
| 30 / 3 | $0.09 | $1.35 | ~$0.50 | **~$2** |
| 100 / 50 | $0.30 | $22.50 | ~$0.50 | **~$23** |
| 1,000 / 300 | $3.00 | $135 | ~$0.50 | **~$140** |
| 5,000 / 500 | $15 | $225 | ~$0.50 | **~$240** |

To bypass the arbiter and revert to single-pass cost, set `ARBITER_ENABLED = False` in `config/settings.py` and rebuild the `.exe`.

---

## Supported databases

Currently implemented:

- **PubMed Central** via NCBI E-utilities (`esearch.fcgi` + `efetch.fcgi`). Concurrency capped at 3 with a 0.4s per-request sleep to stay under the 10 req/s ceiling.
- **medRxiv preprints** via the biorxiv.org `details/medrxiv/{start}/{end}/{cursor}/json` endpoint. 90-day date chunks; client-side keyword filter on title + abstract.
- **CrossRef** (`api.crossref.org/works/{doi}`) for resolving citation metadata when the synthesis cites a DOI outside the ingested corpus.

Not yet implemented (in the project's UPGRADE_SPEC backlog): OpenAlex, Unpaywall fulltext fallback, ClinicalTrials.gov, and a Supabase `runs` table for multi-condition tracking.

---

## Limitations

- **LLM-generated structured extraction.** Every field in the deep-extraction layer is produced by Claude Sonnet, not human reviewers. The provenance layer enables literal-quote verification, but the schema mapping itself can misread nuance, over-simplify, or — rarely — hallucinate fields. The prompt forces `null` rather than guessing when confidence drops below 0.70, but this is a soft constraint enforced by the model. **v3 mitigation**: two-step extraction with arbiter reconciles disagreements between two independent reviewers; LLM-judgment flagging makes the inference / computation distinction explicit in every output.
- **QUADAS is double-LLM scored under v3** (two reviewers + arbiter), but not by trained human reviewers with arbitration. Closer to the formal standard than v2, still not the formal standard.
- **Random-effects pooling, Egger's regression, and trim-and-fill are pure-numpy approximations** of the R `meta` / ProMeta 3 implementations. Adequate for cross-paper signal detection, not adequate for regulatory submission.
- **UMLS CUI assignments are LLM-generated, not validated against the UMLS REST API.** Accuracy is high for common concepts and unverified for rare ones. Every CUI carries `llm_judgment=true` in the database.
- **PMC OA full-text only.** Subscription-journal papers and many recent high-impact studies are not retrievable. Coverage was 98.4% on the Long COVID validation top-500 selection — heavily biased toward open-access journals.
- **medRxiv coverage is client-side filtered**, which is robust but slow. For a 6-year window the pipeline scans up to 26 90-day chunks; non-COVID topics with low match rates may scan thousands of preprints to find tens of matches.
- **Not a systematic review.** No PRISMA flow, no protocol pre-registration, no external risk-of-bias scoring beyond integrated NOS/GRADE per paper. The output is best understood as *structured cartography* of a literature, not a meta-analytic verdict.
- **Author / reference parsing from PMC XML is currently imperfect** — bibliography entries occasionally bleed into the `authors` field on cited papers. CrossRef fallback compensates for off-corpus citations; PDF rendering is hardened against the contamination via html-escape (v3 fix).

---

*Hams & Co. Research Division — Literature Synthesis Engine. MIT License.*
