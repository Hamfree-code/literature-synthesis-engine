# v3.1 — Confirmación de prelectura (Spec §0)

Confirmo lectura de README, ROADMAP, SPEC_V3_DISCREPANCIES, app_server, runner,
phase1/3/4/5/6, settings, schema + migraciones, validation_engine, xml_parser,
umls_normalizer y longcovid.spec antes de planificar. Resumen de entendimiento:

1. **Flujo real de fases.** No hay CLI Typer ni `run_pipeline.py`. El punto de
   entrada es `app_server.py` (Flask + SSE) que lanza
   `pipeline.runner.execute_industrial_pipeline()` en un `multiprocessing.Process`.
2. La secuencia real es: **Phase 1 ingest** → **3a triage (Haiku, Batch API)** →
   **3c enrich (PMC OA full text)** → **3d deep (Sonnet A+B+árbitro)** →
   **3d-bis UMLS norm (Haiku tool call)** → **Phase 4 store** → **Phase 5 analyze** →
   **Phase 6 report**. **Phase 2 (ASReview) está bypaseada**: `run_triage()`
   auto-promociona `papers.jsonl` → `relevant_papers.jsonl`.
3. **Dónde se parsea el JSON de Sonnet hoy.** En extracción: `parse_json_response()`
   sobre `r.result.message.content[0].text` en `_parse_batch_results()`
   (`phase3_extract.py`). En síntesis: `call_synthesizer` / `call_due_diligence` /
   `call_executive_summary` (`phase5_analyze.py`) parsean con `json.loads` y un
   fallback de "primera `{` … última `}`". **Ningún punto usa tool-use hoy** salvo
   `umls_normalizer.py`, que sí usa `tool_choice` forzado.
4. **Funciones estadísticas en phase5.** Todas numpy artesanal: `_pool_random_effects`
   (DerSimonian–Laird τ²/I²/Q), `leave_one_out_analysis`, `assess_publication_bias`
   (Egger por OLS sobre precisión + trim-and-fill propio), `select_model`,
   `meta_analyze_by_factor`, `forest_plot_text`, `propagate_uncertainty`,
   `compute_methodology_quality`, `collect_quadas_scores`, `collect_effect_sizes`.
5. **El código manda — contradicciones detectadas con el spec** (detalladas en el
   plan §6): el spec asume que P5.1 (`runs`) y `human_ratings` no existen, pero la
   migración v3 **ya las crea** (sin cablear a UI). El rename de P7
   (`is_long_covid_focused`) **no es cosmético**: `select_for_deep_analysis()` filtra
   por ese campo, y `propagate_uncertainty()` incrustra literalmente "Long COVID" en
   cada frase de consenso → contamina reportes de topics no-COVID. El instructivo de
   setup del README omite la migración v3. Donde código y spec difieren, documento y
   sigo el código.
