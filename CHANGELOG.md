# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/).

## [3.2.0] — 2026-06-16

Methodological Hardening & Provenance Integrity (UPGRADE v3.2). Closes the
methodological and engineering defects surfaced in the Long COVID due-diligence
review so deliverables survive scrutiny by a clinical methodologist without
category errors, silent data loss, or broken provenance. New deterministic
engines live in `methodology/`, each backed by a unit-test group; CI runs the
full suite (`.github/workflows/tests.yml`).

### Added

- **EMCU product identity (WP-0).** Deliverables are framed as *Evidence Mapping
  with Calibrated Uncertainty*, not systematic reviews. A standing disclaimer
  block is rendered in every report and a build-gating lint
  (`methodology.emcu.assert_clean`) fails the build if a template affirmatively
  self-describes as a "systematic review".
- **Extraction integrity (WP-1).** Pydantic schema validation with typed
  failure reasons (`truncation` / `schema_violation` / `api_error` / `timeout`),
  a Haiku JSON-repair pass (`EXTRACTION_REPAIR_ENABLED`), a raised
  `max_tokens` ceiling, and a first-class PRISMA-style flow record. The deep set
  is selected *before* extraction; failures are retained (never dropped) and the
  conservation invariant `n_intended == n_extracted + n_failed_without_substitution`
  is enforced.
- **GRADE per outcome (WP-2).** New outcome-level `evidence_body` entity and a
  deterministic GRADE algorithm (`[CALC]` arithmetic over `[LLM]` domain
  judgements). Per-paper GRADE is removed from all templates.
- **Design-matched risk-of-bias routing (WP-3).** Each paper gets exactly one
  primary instrument chosen by design (RoB 2 / NOS / ROBINS-I / QUADAS-2 / JBI /
  PROBAST / AMSTAR-2). QUADAS-2 raises `ToolDesignMismatch` for any
  non-diagnostic-accuracy design.
- **Frequency vs prevalence split (WP-4).** Paper-mention frequency and pooled
  patient prevalence are distinct types; a render guard rejects mention-frequency
  under a prevalence column. The "Prevalence" mislabel is gone.
- **Controlled outcome vocabulary (WP-5/6).** `config/outcome_dictionary/<condition>.json`
  + a normalisation layer collapse synonyms (e.g. the three cognitive labels →
  `cognitive_function`) and log unmapped labels for review.
- **Gated quantitative synthesis (WP-6).** Pooling and Egger/funnel run only
  when preconditions hold; otherwise a structured refusal with the precondition
  message (no spurious p-values). Externally-reported statistics are quarantined
  as `[LLM]`, never laundered into `[CALC]`.
- **Case-definition gating (WP-7).** Aggregation happens within a case-definition
  stratum; mixing incommensurable strata raises `IncommensurableDefinitions`.
  Prevalence renders as a definition-stratified table.
- **Provenance integrity (WP-9).** Canonical paper registry (PMCID→DOI),
  run-stable citation numbering shared across documents, bibliography-bleed
  detection, references-section stripping, PMCID validation, and quote-drift
  removal.
- **Layer reconciliation (WP-9).** The calibrated layer is authoritative; a
  build gate fails if narrative certainty exceeds the calibrated tier
  (`RECONCILIATION_STRICT`).
- **Evidence-gated output ceiling (WP-10).** Prescriptive detail in the
  due-diligence brief is tied to the strongest evidence tier; a speculative-max
  corpus yields landscape + gaps only — no Phase II skeleton, no sample size, no
  named drug candidates.
- **Schema migration `config/schema_v3_2_migration.sql`** (additive): `evidence_body`,
  `run_citations`, `provenance_errors`, `symptom_landscape`, case-definition +
  status columns on `extractions`, `canonical_id` on `papers`, and run-registry
  reproducibility columns (PRISMA flow, dictionary version, RoB instruments,
  evidence bodies, reconciliation report, output-ceiling tier).
- **pytest suite** under `tests/` (11 groups mapping to spec §13) + repo-root
  `conftest.py`; pytest config in `pyproject.toml`.

### Changed

- Report / due-diligence / executive-summary templates reframed to EMCU: flow
  record, design-matched RoB, per-outcome GRADE, mention-frequency column, gated
  synthesis with honest refusals, and the output-ceiling gaps-only path.
- Synthesis / due-diligence / extraction prompts aligned to the calibrated
  ceiling, output ceiling, design-matched RoB, and case-definition extraction.

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
