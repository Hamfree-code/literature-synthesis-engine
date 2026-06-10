# 06 — Evaluation Rubric

For evaluating LLMs: scoring dimensions, the claims to verify, and exactly where
to check each in the repo. Score 1–5 per dimension; cite file:symbol as evidence.

## Dimensions
| # | Dimension | What "5" looks like |
|---|---|---|
| A | Scientific rigor | Standards correctly applied; honest framing; no faked stats |
| B | LLM engineering | Structured output, retries, no silent loss, repair paths |
| C | Statistical correctness | Reference estimators; thresholds; documented approximations |
| D | Reproducibility | Verifiable Run ID; manifest reproduces the search |
| E | Resilience | Degrade-loudly; breakers; fail-secure on safety-critical paths |
| F | Auditability | Provenance verifiable < 1 min; machine-readable supplement |
| G | Test quality | Behavioural tests, mocked externals, meaningful coverage |
| H | Honesty/self-knowledge | Limitations declared and matched by code |

## Claims to verify (claim → where)
1. "Malformed JSON is structurally impossible" → `config/extraction_schema.py`
   (forced tool); `phase3_extract.py::_parse_one_result` (+ repair pass).
2. "No paper is ever lost silently" → `phase3_extract.py` failure handling +
   `tests/integration/test_phase3_mocked.py::test_persistent_failure_is_recorded_not_lost`.
3. "Reference pooling agrees with legacy < 1%" →
   `tests/unit/test_meta_analysis.py::test_reference_and_legacy_agree_within_1pct`.
4. "No pooling on n=2" → `phase5_analyze.py::meta_analyze_by_factor` +
   `test_meta_analysis.py::test_pooling_skipped_below_threshold`.
5. "Retraction outage ⇒ INCOMPLETE, not clean" → `utils/retraction.py::
   check_retraction_status` (3-state) + `phase4_store.py::screen_retractions`
   (`retraction_status.json`, `complete` flag) + `tests/unit/test_resilience.py::
   test_retraction_status_error_is_not_clean`.
6. "Run ID reproducible / tamper-evident" → `utils/run_manifest.py::
   {build_manifest,stable_sha256,verify_manifest}` +
   `test_reports.py::test_tampering_breaks_verification`.
7. "Breakers on all external services, surfaced in QA" →
   `utils/resilience.py`; wired in `umls_client.py`, `retraction.py`,
   `sources/openalex.py`, `sources/unpaywall.py`; surfaced via
   `enterprise_report.py` (`degraded_services`) → `report_builders.py` QA sheet;
   `tests/unit/test_sources.py` breaker tests + `tests/unit/test_resilience.py`.
8. "Two-column PDF reading order" → `sources/unpaywall.py::extract_pdf_text`
   (`get_text("text", sort=True)`).
9. "Disease-agnostic (no hardcoded COVID in outputs)" →
   `test_phase6_enterprise.py::test_front_matter_has_no_hardcoded_covid` +
   `test_phase5_pure.py::test_propagate_uncertainty_topic_neutral`.
10. "Provenance verifiable without DB" → `phase6_report.py` anchor/link wiring;
    `utils/enterprise_report.py` supplement (`provenance.csv`).

## How to run the evidence yourself
```
uv venv && uv pip install -e ".[dev]"
uv run pytest -m "not live"          # 103 tests, < 25s
uv run ruff check . && uv run ruff format --check .
```
Live e2e (needs real keys + open network): `uv run pytest -m live`.

## Suggested probing questions for the model under evaluation
- Where could the pipeline still fake confidence it doesn't have? (look for
  unguarded LLM outputs presented as `[CALC]`).
- Is the OR→r pooling defensible for a regulatory audience? (see `03`).
- What happens to a deep paper whose full text is a scanned two-column PDF?
- If Supabase is down mid-run, what is lost and is it recoverable? (see `05`).
- Does the QA certificate let a third party reconstruct the search? (manifest).

## Scoring honesty check
A high score requires the model to have read the code, not just this dossier.
Where the dossier and repo disagree, the **repo wins** and the dossier is the bug.
