# Master Improvement Spec v3.0 — Discrepancies with current code

Per the spec's Rule of Gold #5 ("El código manda — Si algo en el código actual difiere de esta spec, documentarlo antes de sobreescribirlo"), the following discrepancies were identified before implementation began.

## Discrepancy 1 — No `run_pipeline.py`

The spec instructs to read `run_pipeline.py` and to invoke validation via `uv run python run_pipeline.py --topic ...`. This file does not exist in the build directory. The actual entry point is `app_server.py`, which runs the pipeline phases sequentially in a background thread (now migrated to a separate process — see Priority 1.1 below). There is no Typer CLI in the build.

**Implication:** the spec's `execute_industrial_pipeline(queue, disease, mesh_terms, max_papers, max_deep)` entry point will be added to a new module `pipeline/runner.py`, not to `run_pipeline.py`. The function name `execute_industrial_pipeline` is preserved verbatim.

## Discrepancy 2 — WeasyPrint already retired

The spec text under Priority 1.1 mentions "WeasyPrint compila PDFs" as a source of GIL contention. WeasyPrint was already retired from this codebase in the previous session and replaced with `app_pdf.markdown_to_pdf()` (reportlab-based) to avoid the GTK3 dependency on Windows. The PDF compilation is now pure-Python reportlab. The GIL/threading concern still applies (reportlab's `SimpleDocTemplate.build()` is CPU-bound), so the migration to multiprocessing is still required, but the WeasyPrint citation in the spec is historical.

## Discrepancy 3 — Phase 2 (ASReview) is bypassed

The spec's validation command includes `--skip-phases 2`. This flag does not exist in the current code because Phase 2 is already bypassed: `phase3_extract.run_triage()` auto-promotes `data/raw/papers.jsonl` to `data/filtered/relevant_papers.jsonl` when no ASReview output exists. No change is needed for this.

## Discrepancy 4 — Legacy field names

`is_long_covid_focused` and `long_covid_definition_weeks` are still hardcoded in the JSON schema and in `triage_haiku.txt`. They semantically mean "is topic-focused" and "definition threshold weeks". Renaming requires a Supabase migration and a prompt update. Deferred — not in scope of this spec.

## Discrepancy 5 — UMLS API unavailable

Priority 2.2 specifies UMLS CUI assignment for every extracted entity. UMLS REST API access requires registration and an API key, which is not present in `bundled_credentials.py`. The implementation falls back to LLM-generated CUIs via Anthropic tool calling: the model returns its best-guess CUI based on training-data knowledge. Accuracy is unmeasured. This is a known limitation and will be noted in the report's methodology section.

## Discrepancy 6 — Validation run cost

The spec's validation command (`100 papers, 50 deep`) would cost approximately:
- 100 × $0.003 Haiku = $0.30
- 50 × 3 × $0.15 Sonnet (now with two-step arbiter) = $22.50
- 3 Sonnet synthesis calls ≈ $0.50
- 1 Haiku MeSH expansion ≈ $0.001
- **Total ≈ $23.30**

This exceeds the user's stated remaining API budget. Validation is therefore performed at the smoke-test level only (imports, schema validity, prompt rendering). End-to-end validation is deferred to user action.

---

*Generated 2026-05-17.*
