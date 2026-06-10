# UPGRADE v3.1 — Discrepancies with the contract

Per the contract's rule "el código manda — documenta la desviación antes de
sobreescribir". Deviations from `UPGRADE_v3_1_CLAUDE_CODE.md` made during
implementation:

## D1 — End-to-end validation runs not executed here
The contract's validation runs (smoke 30/3 ≈ $2, validación 100/50 ≈ $23 each)
require live `ANTHROPIC_API_KEY` / `SUPABASE` / `NCBI` credentials and budget that
are not available in this environment (already flagged for v3.0 in
`SPEC_V3_DISCREPANCIES.md`, D6). Everything is validated at unit/integration
level with mocked Anthropic/UMLS/Crossref (respx) instead. The live smoke test
exists at `tests/e2e/test_smoke_30_3.py` marked `@pytest.mark.live` for the
operator to run. **Action required by the user:** run the 100/50 validations to
confirm deep-success ≥99%, %CUIs verified, and a known retracted DOI exclusion.

## D2 — `runs` / `human_ratings` tables already existed (P5.1)
The contract describes creating the `runs` table. The v3 migration already
created `runs` and `human_ratings`. v3.1 therefore **extends** `runs` additively
(`deep_success_rate`, `kappa_summary`, `sources_breakdown`, `engine_version`,
`manifest_sha256`, …) and **wires** the previously-unused tables, rather than
recreating them. (Finding F13 in the master plan.)

## D3 — ruff config kept at line-length 110 (not 100)
The contract suggests `line-length 100`. The existing v3.0 `pyproject.toml`
already pinned 110; lowering it would reformat the entire pre-existing codebase
for no functional gain. We kept 110, ignore `E501` (long prompt/SQL strings) and
`E402` (the credential-before-import pattern the entry points require). CI runs
`ruff check` + `ruff format --check` green.

## D4 — Local dev on Python 3.11; CI pins 3.12
The project pins `>=3.12,<3.13`. The local validation environment only had 3.11;
all code is 3.12-compatible and the GitHub Actions matrix pins 3.12. Tests pass
on both.

## D5 — Retraction screen is bounded to the deep set
The contract says screen "cada paper con DOI" in Phase 4. Screening every
ingested paper (potentially thousands) against Crossref is slow and unnecessary;
v3.1 screens the **deep-extracted set** (≤ max_deep), which is what feeds the
cross-analysis. The PubMed `Retracted Publication[pt]` exclusion already removes
the bulk at ingest. Configurable via `RETRACTION_CHECK_ENABLED`.

## D6 — `chunking_mode='flat_pdf'` recorded at enrichment, not in the extraction JSON
The contract asks to mark `chunking_mode='flat_pdf'` in the extraction. Because
the extraction body is model-generated, v3.1 records the full-text route
(`fulltext_source: pmc_oa | unpaywall_flat_pdf`) in the full-text cache instead,
which the QA sheet uses for the coverage breakdown. Equivalent traceability,
cleaner provenance.

## D7 — DOCX/PDF: DOCX added; PDF path unchanged
DOCX export (python-docx) is added as specified. The existing reportlab PDF path
is retained as-is (WeasyPrint was already retired in v3.0). The DOCX is a faithful
structural render (headings, tables, lists), not a pixel match of the PDF.
