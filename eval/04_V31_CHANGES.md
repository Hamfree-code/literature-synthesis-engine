# 04 — What v3.1 Changed (grounded)

Baseline v3.0 was a working prototype (dual-reviewer extraction, provenance,
numpy stats, PMC-only, PDF reports, no tests). v3.1 turned it into an auditable
platform. Priorities executed in dependency order (P0 first = safety net).

## P0 — Tests + CI
`tests/` suite (xml parser, Kappa, scoring, run-context, meta-analysis,
credibility, sources, reports, resilience, mocked phase3/phase6). 103 non-live
tests at the v3.1 milestone; **now 117** after the post-v3.1 deltas below (`07`).
All APIs mocked (respx + fakes), < 15s. GitHub Actions: ruff + pytest + coverage.
Coverage utils + phase5 = 73% (measured this iteration — see `08`).

## P1 — Yield (eliminate JSON-parse failures)
Forced tool-use for all 3 extraction calls (`config/extraction_schema.py` =
single source of truth). `max_tokens` compression retries, Haiku repair pass,
`extraction_attempts` log. Fixed a double-parse failure-accounting bug.

## P2 — Credibility
- UMLS REST CUI verification (`utils/umls_client.py`) with rapidfuzz matching;
  offline fallback keeps `cui_verified=false`. `[VERIFIED]` badge.
- Retraction screening (`utils/retraction.py`): PubMed `Retracted
  Publication[pt]` exclusion + Crossref per-DOI check; retracted papers excluded
  from cross-analysis and listed in methods.

## P3 — Reference statistics
`utils/meta_stats.py`: PyMARE DerSimonian–Laird + statsmodels Egger, legacy
numpy retained as fallback with a dual-run diff (< 1%).

## P4 — Coverage
`pipeline/sources/openalex.py` (cursor-paginated discovery, server-side preprint
search replacing the slow medRxiv scan) + `unpaywall.py` (OA-only PDF + pymupdf
text). DOI-normalised dedup, PMC wins.

## P5 — Product
`runs` registry wired; `/runs`, export JSON/CSV, `/kappa`, `POST /ratings`
endpoints; cross-platform report opening.

## P6 — Enterprise reports
Run manifest + verifiable Run ID, PRISMA flow SVG, GRADE SoF, QA certificate,
one-pager, RIS/BibTeX, supplement ZIP, editable DOCX.

## P7 — Debt
Phase 2 removed; legacy COVID field names renamed (compat preserved); PMC author
↔ bibliography bleed fixed; README/.env/spec refreshed; version → 3.1.0.

## Post-P7 follow-ups (later iterations)
- `MIN_STUDIES_POOLING` guard (no DL pooling on n=2).
- Real `[VERIFIED]` badge wiring + honest cost floor.
- `bundled_credentials` import made optional (dev runs via `.env`).
- **Resilience hardening** (`utils/resilience.py`): circuit breakers + persistent
  `JsonFileCache` + a per-run health registry surfaced in the QA sheet.
  - Crossref retraction is now 3-state (`retracted/clean/error`); a Crossref
    outage marks the screen **INCOMPLETE**, never silently "0 retracted".
  - Breakers wired on Crossref, UMLS, **OpenAlex, Unpaywall**; `degraded_services`
    propagates to the QA certificate.
  - PDF extraction uses `get_text("text", sort=True)` so two-column papers keep
    reading order (protects literal-quote provenance).

## Post-v3.1 (current iteration — see `07` for `file:symbol` detail)
- **Unpaywall fail-secure + persistent cache**: an Unpaywall API outage during
  the OA-PDF lookup is no longer indistinguishable from a legitimate "no OA" — it
  feeds the breaker and surfaces in `degraded_services`. DOI→URL lookups persist
  across runs (`data/raw/unpaywall_cache.json`).
- **Opus arbiter**: reconciliation moved from a third Sonnet pass to a
  configurable Opus arbiter (`ANTHROPIC_ARBITER_MODEL`); `temperature` is gated
  on the model family (Opus 4.7/4.8 reject sampling params).
- **Cross-model Reviewer B (Gemini Flash)**: optional `REVIEWER_B_PROVIDER=gemini`
  routes Reviewer B to Gemini's Batch API for decorrelated extraction errors,
  fail-secure (breaker + fallback), output normalised through the existing repair
  pass. New `utils/gemini_client.py`, optional `[gemini]` extra.

## Design rules held throughout
Never fake rigor · provenance first · conservative DD · the code is the source
of truth · no merge without green tests · degrade loudly, never silently.
