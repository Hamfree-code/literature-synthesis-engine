# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/).

## [3.0.0] — 2026-05-17

Major architectural upgrade per the Master Improvement Spec v3.0
(`ROADMAP.md`). All Priority 1–3 items implemented; Priority 4 deferred.

### Added

- **Two-step deep extraction with arbiter reconciliation.** Each paper is
  extracted independently by two Sonnet reviewers at different temperatures
  (0.1 and 0.3) and reconciled by a third Sonnet pass (0.0). Surfaces
  `reconciliation_triggered`, `arbiter_notes`, and a per-field
  `llm_judgment_flags` map. Toggle: `ARBITER_ENABLED`.
- **Semantic XML section chunking.** `utils.xml_parser.extract_structured_sections()`
  buckets PMC `<sec sec-type="...">` content into metadata / methods / results /
  discussion_limitations / conflicts_funding with per-section character caps.
  Replaces the flat 120k truncation that was systematically cutting off
  discussion / limitations / conflicts / funding.
- **UMLS / MeSH normalisation via Anthropic tool calling.**
  `utils.umls_normalizer` maps each extracted entity to a UMLS CUI and a MeSH
  heading. Persisted to the new `extracted_phenotypes` Supabase table. Toggle:
  `UMLS_NORMALIZATION_ENABLED`.
- **Cohen's Kappa validation engine.** `utils.validation_engine` computes
  Kappa (Landis & Koch bands) / RMSE / Pearson against human ratings stored in
  the `human_ratings` Supabase table.
- **LLM / CALC / CONSENSUS badges.** Three Jinja2 filters (`|llm`, `|calc`,
  `|consensus`) render superscript markers on every section header so the
  reader instantly knows whether content is model inference, deterministic
  computation, or arbiter consensus.
- **Conservative due-diligence prompt.** Never recommends a Phase II target
  unless ≥ 2 papers support the mechanism at GRADE Moderate or higher.
  Surfaces `confidence_in_recommendation` (0–100), `small_corpus_warning` for
  n_deep < 10, and a mandatory hypothesis-generating disclaimer.
- **Mandatory Methodology & Limitations section** at the top of every
  rendered report (research / DD / executive summary).
- **Multiprocessing server architecture.** Pipeline runs in a
  `multiprocessing.Process` separate from Flask, communicating via
  `multiprocessing.Queue`. NumPy / reportlab / Sonnet polling no longer
  compete with Flask for the GIL; SSE remains responsive throughout heavy
  analysis.
- **Topic-change auto-wipe.** `utils.run_context.clear_stale_state_if_topic_changed()`
  detects when the new run's topic differs from the last and wipes
  checkpoints + raw + filtered to prevent silent same-state short-circuits.
- **v3 SQL migration** (`config/schema_v3_migration.sql`): arbiter columns on
  `extractions`, plus three new tables (`extracted_phenotypes`,
  `human_ratings`, `runs`).

### Changed

- `pipeline/runner.py` is the new worker entry point for the multiprocessing
  pipeline. `app_server.py` was rewritten to spawn it.
- The extraction Sonnet prompt now references the labelled `=== SECTION ===`
  blocks produced by the semantic XML chunker.
- Cost per deep-extracted paper triples under the default `ARBITER_ENABLED=True`
  (one reviewer A + one reviewer B + one arbiter call). Prompt caching
  partially offsets this. Single-pass mode remains available.

### Fixed

- **medRxiv consistently returning 0 candidates.** Root cause: biorxiv API
  returns 30 papers per page, not 100; the prior `if len(collection) < 100:
  break` killed the loop after the first page. Replaced with proper
  cursor-vs-total pagination + 90-day chunking + retry logic + topic-aware
  query terms.
- **Silent skip after topic change.** Stale `.done` markers from a previous
  run caused every phase to short-circuit. Resolved via the topic-change
  guard.
- **Main research PDF silently failing to render.** PMC reference
  contamination embedded `</= 3?` in author / title fields; reportlab's
  strict paraparser read `</=` as a malformed closing tag. Fixed by
  html-escaping `<`, `>`, `&` in `app_pdf._inline()` before applying the
  markdown→tag substitutions.

## [2.5.0] — 2026-05-17 (morning)

### Added

- MeSH synonym expansion via a single Haiku call (`expand_search_terms`) for
  non-Long-COVID topics. OR-joined into the PubMed query and re-used as the
  medRxiv keyword set.
- CrossRef DOI resolution fallback in `pipeline.phase6_report`. When the
  synthesis cites a DOI not present in the corpus, the renderer looks it up
  at `api.crossref.org/works/{doi}` and formats a Vancouver citation.
- 90-day date chunking for medRxiv ingestion (cursor pagination, retry on
  timeout).

## [2.0.0] — 2026-05-16

### Added

- Calibrated certainty layer (Established / Probable / Possible / Speculative
  / Contradicted) with literal-quote provenance.
- Methodology emulation of Siciliano et al. *Movement Disorders* 2024:
  QUADAS-adapted 0–19 scoring with cutoff 13, DerSimonian–Laird τ²
  random-effects pooling, leave-one-out sensitivity (10% influence
  threshold), Egger's regression + trim-and-fill (n ≥ 10).
- Three-report pipeline (research + due-diligence + executive summary).
- Schema v2 migration: methodology + calibration columns, `provenance`
  table.

## [1.0.0] — 2026-05-15

Initial Long COVID demonstration build. Flask UI, single-pass Sonnet
extraction, two reports (research + DD), reportlab PDF rendering.
