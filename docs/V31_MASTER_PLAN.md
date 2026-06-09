# Literature Synthesis Engine — Master Plan de Optimización v3.1

**Repo:** `Hamfree-code/literature-synthesis-engine`
**Baseline:** v3.0 (2026-05-17)
**Documento fuente:** `UPGRADE_v3_1_CLAUDE_CODE.md` (contrato de implementación)
**Estado de este documento:** Plan de trabajo — qué hacer, en qué orden, con qué
criterio de aceptación, y qué falta en el contrato para que el resultado sea
**presentable como producto**, no solo como motor técnico.

Este plan no reemplaza el contrato v3.1; lo **operacionaliza y lo extiende** en tres
ejes que el cliente pidió explícitamente: (1) especificación técnica aterrizada en el
código real, (2) metodología de investigación fase por fase, (3) capa de producto y
presentación. La §6 añade hallazgos del código que el contrato no menciona y que son
bloqueantes para vender esto.

---

## 0. Resumen ejecutivo

El motor v3.0 ya hace lo difícil: ingesta multi-fuente, extracción doble con árbitro,
provenance literal, meta-análisis y tres reportes calibrados. Lo que le falta para ser
**producto** no es capacidad analítica sino **confianza verificable**: red de tests,
yield del 99%, CUIs y retracciones validadas contra fuentes reales, estadística de
referencia, cobertura más allá de open-access, e historial + entregables que un cliente
de farma/consultoría reconozca (PRISMA, GRADE SoF, manifest reproducible, DOCX editable).

El orden del contrato es correcto y se respeta:

```
P0 tests+CI → P1 yield → P2 CUIs+retracciones → P3 stats → P4 fuentes
→ P5 producto(runs/Kappa/exports) → P6 reportes enterprise → P7 deuda
```

**Estado final esperado:** un tercero sin acceso al sistema puede reproducir la
búsqueda desde el manifest, verificar cualquier claim en <1 min vía provenance, abrir
el DOCX con formato intacto, importar la bibliografía a Zotero y juzgar la calidad del
run leyendo una sola página. CI verde, cobertura ≥70% en `utils/` + `phase5`, deep
success ≥99%.

---

## 1. Diagnóstico del estado actual (aterrizado en el código)

| Área | Estado v3.0 real | Riesgo para producto |
|---|---|---|
| Tests / CI | **Ninguno.** No hay `tests/`, no hay `.github/`. | Cada refactor es regresión silenciosa en un sistema cuyo valor es *precisión*. |
| Yield deep | ~94% en Long COVID por *JSON-parse failures*; el árbitro triplica la superficie de fallo (3 llamadas Sonnet). `_parse_batch_results` parsea texto crudo, sin tool-use. | Pagas Sonnet por extracciones que tiras. |
| CUIs | LLM-inferidos vía `umls_normalizer.py`, `llm_judgment=true` siempre. Sin verificación contra UMLS. | Un revisor externo lo tumba en 5 minutos. |
| Retracciones | **Sin detección.** Puede sintetizar papers retractados. | Inaceptable para una tesis de rigor. |
| Estadística | numpy artesanal (DL, Egger por OLS, trim-and-fill propio) en `phase5_analyze.py`. | README lo declara "not adequate for regulatory submission" → techo de producto. |
| Cobertura | Solo PMC OA (98.4% del top-500, sesgado a open-access). medRxiv = filtrado client-side lento; en Long COVID dio **0** (integración rota). | Sesgo open-access declarado. |
| Producto | Sin tabla `runs` cableada, sin Kappa en UI, sin exports CSV/JSON. (La migración v3 **ya crea** `runs` y `human_ratings`, pero no se escriben ni se leen.) | No hay historial, comparabilidad ni auditoría operativa. |
| Reportes | Técnicamente sólidos pero PDF no editable, sin PRISMA, sin GRADE SoF, sin manifest, verificación de claims exige acceso a Supabase. | No pasan el filtro de un cliente corporativo. |
| Deuda | Bug autores↔bibliografía; campos legacy COVID; Phase 2 muerta; README desactualizado. | Visible en cada PDF. |

**Conclusión:** el cuello de botella no es el cerebro, es la **cadena de custodia de la
evidencia y la presentación**. v3.1 ataca exactamente eso.

---

## 2. Principios rectores del producto (heredan las reglas de oro)

1. **Never fake rigor.** Evidencia débil → dilo fuerte. PRISMA-conformant ≠ systematic review registrado; el reporte debe declararlo.
2. **Provenance first.** Todo claim con cita literal verificable en <1 min sin tocar la BD.
3. **Conservative by default**, especialmente en DD.
4. **Etiquetado de juicio:** `[LLM]` / `[CALC]` / `[CONSENSUS]` / `[VERIFIED]`.
5. **El código manda;** discrepancias se documentan antes de sobreescribir (ver §6).
6. **No romper lo que funciona:** multiprocessing, Flask, prompts, semántica de campos en P1.
7. **Ningún merge sin tests verdes** a partir de P0.
8. **(añadido) Disease-agnostic de verdad:** ningún string "Long COVID" hardcodeado debe aparecer en un reporte de Fibromyalgia (ver §6, F2/F4).
9. **(añadido) Reproducibilidad como feature de venta:** el manifest + Run ID son el producto tanto como el PDF.

---

## 3. Plan por prioridad (técnico, aterrizado en archivos reales)

Cada prioridad: **objetivo · archivos reales · especificación · cómo validar**. Una rama
por prioridad (`v31/p0-tests`, …), PR contra main, CI verde obligatorio.

### P0 — Red de seguridad: tests + CI *(bloquea todo)*

- **Objetivo:** suite que corre en <3 min y CI verde antes de tocar el camino crítico.
- **Archivos:** `tests/` (estructura del contrato §0.1), `pyproject.toml` (añadir `pytest-mock`, `respx`, `ruff` a `dev`), `.github/workflows/ci.yml`.
- **Especificación:**
  - Fixtures XML PMC OA reales versionados (documentar PMCIDs): uno limpio, uno con el bug bibliografía→authors (`pmc_sample_messy.xml`).
  - Unit: `test_xml_parser` (cada bucket recibe lo suyo; `<ref-list>` no contamina), `test_validation_engine` (Kappa contra Landis & Koch), `test_meta_analysis` (datasets sintéticos con efecto conocido; oráculo `statsmodels`/PyMARE — **escribir ahora**, `xfail` hasta P3), `test_scoring` (`sample_size × design_weight × extraction_confidence`), `test_run_context` (topic_slug, auto-wipe).
  - Integration: `test_phase3_mocked` (A+B+árbitro con Anthropic mockeado vía respx), `test_phase6_render` (MD→HTML→PDF con caracteres PMC contaminados).
  - e2e `test_smoke_30_3` marcado `@pytest.mark.live`, manual.
  - CI: `uv sync` → `ruff check` → `ruff format --check` → `pytest -m "not live"` → cobertura como artifact. Matrix Python 3.12.
- **Validar:** CI verde en main; cobertura ≥70% en `utils/` + `phase5_analyze.py`.

### P1 — Yield: eliminar JSON-parse failures en deep extraction

- **Objetivo:** deep success ≥99%, cero pérdidas silenciosas, toda falla con razón en BD.
- **Archivos:** `pipeline/phase3_extract.py`, `config/extraction_schema.py` (nuevo, fuente única de verdad), `config/schema_v31_migration.sql` (tabla `extraction_attempts`).
- **Especificación:**
  - Migrar las **3 llamadas** (Reviewer A, B, Árbitro) a **tool-use forzado** (`tool_choice:{type:tool,name:submit_extraction}`) con `input_schema` = el schema actual campo a campo (sin cambiar semántica — regla 6). El modelo no puede devolver JSON malformado por construcción.
  - `max_tokens` calculado del peor caso real (p99 de tamaño de extracción Long COVID + 20%).
  - `stop_reason=="max_tokens"` → reintento con compresión (≤8 provenance_quotes, headline ≤2 frases), máx 2; luego `extraction_failed` con razón persistida.
  - Log estructurado por intento → tabla `extraction_attempts {paper_id, attempt, stop_reason, tokens_out, parse_ok}` (alimenta el dashboard de P5 y el QA sheet de P6).
  - Red final: repair pass barato con Haiku ("repara este JSON al schema X").
  - **Aprovechar para limpiar F5** (§6): el doble `_parse_batch_results` sobre la misma lista. Con tool-use, separar A/B por `custom_id` sin generar fallos espurios.
- **Validar:** run 100/50 deep success ≥99%; `deep_failures.jsonl` + `extraction_attempts` coherentes; smoke 30/3 (~$2).

### P2 — Credibilidad: CUIs verificados + retracciones

- **Objetivo:** % CUIs verificados ≥80% (con key); papers retractados excluidos y reportados.
- **Archivos:** `utils/umls_client.py` (nuevo), `pipeline/phase1_ingest.py`, `pipeline/phase4_store.py`, `config/schema_v31_migration.sql`.
- **Especificación CUIs:**
  - Capa A: UMLS REST (`uts-ws.nlm.nih.gov`), `UMLS_API_KEY` opcional en `.env`/`.env.example`/README. Por CUI inferido: `GET /CUI/{cui}`; si existe y el nombre preferido encaja con `verbatim_text`/`mesh_heading` (`rapidfuzz`, umbral 70) → `cui_verified=true`. Si no, `search?string=` y primer match MeSH/SNOMED; si tampoco, `cui_verified=false` conservando el valor LLM.
  - Capa B: sin key → comportamiento actual, `cui_verified=false`. **Nunca obligatoria.**
  - Caché `umls_cache(cui, preferred_name, verified_at)` (los corpus repiten conceptos). Semáforo 5 concurrentes, backoff en 429.
  - Badge `[VERIFIED]` en entidades verificadas; metodología reporta el %.
- **Especificación retracciones:**
  - Phase 4: por DOI, Crossref `works/{doi}` → `update-to` tipo retraction → marca `is_retracted`, `retraction_doi`, `retraction_date` en `papers`.
  - Phase 1: query esearch con `NOT "Retracted Publication"[Publication Type]` por defecto (flag `INCLUDE_RETRACTED=false`).
  - Retractado ya en corpus → excluir del cross-analysis (Phase 5) y listar en metodología: *"N papers excluded due to retraction: [DOIs]"*.
- **Validar:** % verificados ≥80% con key; DOI COVID retractado conocido → excluido y reportado.

### P3 — Estadística defendible: PyMARE / statsmodels

- **Objetivo:** estimadores de referencia; tests de P0 verdes sin `xfail`.
- **Archivos:** `pipeline/phase5_analyze.py`, `pyproject.toml`, `longcovid.spec` (hiddenimports).
- **Especificación:**
  - Añadir `pymare` + `statsmodels` (verificar PyInstaller + `longcovid.spec`).
  - DL pooling → `pymare.estimators.DerSimonianLaird` con **adaptador** que mantiene el formato de salida que ya consumen `forest_plot_text`, `heterogeneity_section`, etc. (no refactorizar consumidores).
  - Egger → `statsmodels` WLS sobre precisión.
  - Trim-and-fill → si no hay implementación mantenida, conservar la propia **validada por test** y declararlo en el reporte.
  - **Doble ejecución** vieja/nueva en el run de validación → `docs/V31_STATS_DIFF.md`; diferencias >1% en estimadores puntuales se investigan antes de cortar.
  - Actualizar limitación del README: de "aproximación numpy" a "PyMARE/statsmodels; trim-and-fill propia validada por tests". Badges `[CALC]` sin cambio.
- **Validar:** tests de meta-análisis verdes; diff <1% o explicado; `.exe` reconstruye y arranca con pymare.

### P4 — Cobertura: OpenAlex + Unpaywall (+ arreglo medRxiv)

- **Objetivo:** desglose por fuente en metodología; ≥1 paper enriquecido vía Unpaywall; dedup sin duplicados; fase 1 más rápida en topics raros.
- **Archivos:** `pipeline/sources/` (paquete nuevo: `openalex.py`, `unpaywall.py`), `pipeline/phase1_ingest.py` (orquestación multi-fuente), `pipeline/phase3_extract.py` (modo `flat_pdf`), settings + `.env.example`.
- **Especificación:**
  - OpenAlex: `works?search=&filter=` con cursor pagination y `mailto` (polite pool, sin key). Dedup por DOI normalizado (lowercase, sin prefijo URL); **PMC gana** (ya tiene full text).
  - **Sustituir el escaneo client-side de medRxiv por filtro server-side de OpenAlex** (`type:preprint` / source medRxiv). Mantener cliente legacy tras `MEDRXIV_LEGACY=false` por lagunas de recencia (<48h).
  - Unpaywall: para papers seleccionados a deep sin full text PMC OA, `v2/{doi}?email=` → `best_oa_location.url_for_pdf` → descargar y extraer con `pymupdf`; marcar `chunking_mode='flat_pdf'` para trazabilidad. **Solo URLs que Unpaywall declare OA legal** (sin scraping de publishers). **Resuelve F11** (§6): medRxiv hoy se selecciona a deep pero `enrich_with_fulltext` lo salta → caía a abstract en silencio.
- **Validar:** topic no-COVID muestra papers por fuente; dedup limpio; tiempo fase 1 menor que legacy.

### P5 — Producto: runs + Kappa UI + exports

- **Objetivo:** dos runs del mismo topic comparables en UI; CSV abre limpio; Kappa visible con ≥5 ratings.
- **Archivos:** `config/schema_v31_migration.sql` (extender la tabla `runs` existente), `utils/supabase_client.py`, `pipeline/runner.py` (escribir el run row al cerrar), `app_server.py` (+endpoints), `templates/app.html` (vistas).
- **Especificación:**
  - **Reconciliar la tabla `runs` que la migración v3 ya creó** con las columnas que pide el contrato P5.1: añadir `deep_success_rate`, `kappa_summary jsonb`, `sources_breakdown jsonb`, `engine_version`. Hoy la tabla existe pero **nadie la escribe** — cablear `runner.py` para hacer el upsert al final. `papers`/`extractions` referencian `run_id` (FK **nullable** para legacy).
  - UI: vista "Run history" + detalle + comparación lado a lado de dos runs del mismo topic (deltas en n_deep, GRADE, top fenotipos por CUI).
  - Kappa UI: panel por variable con banda Landis & Koch coloreada (el engine ya existe en `utils/validation_engine.py`) + **formulario de input de ratings humanos** (sin input, `human_ratings` nunca se llena y el engine es código muerto).
  - Exports por run: `/runs/{id}/export.json`, `/runs/{id}/extractions.csv`, además de PDFs. **Flask, no FastAPI.** Documentar en `docs/API.md`.
- **Validar:** comparación de dos runs; CSV limpio en Excel/Numbers; Kappa tras ≥5 ratings de prueba.

### P6 — Reportes enterprise-grade *(el salto a "vendible")*

- **Objetivo (test de cliente):** un tercero puede (a) reproducir la búsqueda con el manifest, (b) verificar cualquier claim en <1 min, (c) editar el DOCX, (d) importar la bibliografía a Zotero, (e) entender la calidad leyendo la página 2.
- **Archivos:** `utils/run_manifest.py` (nuevo), `utils/export_docx.py` (nuevo), `utils/export_citations.py` (nuevo), `pipeline/phase6_report.py`, `templates/` (SoF, one-pager, portada DOCX, QA sheet).
- **Especificación:**
  - **PRISMA 2020:** flow diagram autogenerado por run (identificados por fuente → cribados → excluidos con razones → elegibles → incluidos), SVG embebido (matplotlib o SVG templado, sin deps pesadas). **PRISMA-S:** apéndice con query exacta por fuente, fecha/hora, filtros, nº resultados — generado del manifest. **Encuadre honesto obligatorio:** *"PRISMA-conformant reporting of an AI-assisted evidence mapping. Not a registered systematic review."*
  - **Run manifest** (`run_manifest.json`): `engine_version` (git SHA + semver), queries por fuente, ventana temporal, modelos + versiones exactas, temperaturas, flags activos, **SHA-256 de cada prompt** de `config/prompts/`, recuentos por fase, coste **real** (de `usage`, no estimado — corrige F12). **Run ID = SHA-256 del manifest** en portada. El apéndice "Methods in full" se genera del manifest (fuente única; reporte y ejecución no pueden divergir).
  - **Trazabilidad clicable:** cada claim `[LLM]`/`[CONSENSUS]` enlaza a su provenance (cita + autor/año + DOI). Anchor interno en HTML, link en PDF.
  - **Supplement ZIP** por run: `extractions.csv`, `provenance.csv`, `evidence_table.xlsx` (una fila por paper deep: diseño, n, NOS, GRADE, efecto, certeza). Reutiliza exports de P5.3.
  - **Bibliografía** `references.ris` + `references.bib` desde metadatos CrossRef (Zotero/EndNote).
  - **GRADE SoF:** por outcome/mecanismo con ≥2 papers, tabla estándar (n estudios + diseños, n total, efecto pooled + CI `[CALC]`, I², certeza GRADE con razones de downgrade explícitas). Plantilla `templates/sof_table.html`. Es **presentación**, los datos ya están en las extracciones.
  - **DOCX editable** (python-docx) con plantilla Hams & Co. (portada, confidencialidad, control de versiones, TOC, numeración). **Executive one-pager** separado (1 pág: hallazgo, 3 bullets con GRADE, 1 advertencia, mini-QA). **Página legal** en todo entregable (scope, disclaimer médico/regulatorio/inversión, **fecha de vigencia** "Evidence current as of [fecha de búsqueda]" — base del modelo de re-run recurrente).
  - **QA sheet** fija en página 2: deep success, % CUIs verificados, reconciliaciones, Kappa, retractados excluidos, % cobertura full-text, desglose por fuente, coste, runtime, engine version + Run ID.
- **Validar:** los 13 ítems del checklist final del contrato.

### P7 — Deuda técnica (rápida, al final)

- **Bug autores↔bibliografía:** ya hay mitigación en `xml_parser.py` (`<ref-list>` se elimina antes de clasificar). Cerrar con el test de regresión `pmc_sample_messy.xml` de P0 y verificar que `parse_pmc_xml` (metadata, `phase1_ingest.py`) no recolecta autores de `<ref>`.
- **Rename legacy:** `is_long_covid_focused`→`is_topic_focused`, `long_covid_definition_weeks`→`definition_threshold_weeks`. Migración con vistas de compatibilidad; actualizar prompts y código. **Subir prioridad** — ver F1/F2 (§6): hoy rompe correctitud multi-topic, no es cosmético.
- **Phase 2 ASReview:** eliminar del codebase y del diagrama (Haiku la sustituyó de facto); documentar en CHANGELOG. `phase2_filter.py` + excludes de `longcovid.spec` ya lo anticipan.
- **README:** actualizar limitaciones (CUIs verificados, stats de referencia, retracciones, multi-fuente) + badges CI/cobertura. **Corregir el instructivo de setup** (hoy omite `schema_v3_migration.sql`; ver F7).

---

## 4. Metodología de investigación, fase por fase

El producto se vende como *evidence synthesis auditable*. Cada fase debe mapear a un
estándar reconocible y declarar qué es máquina y qué es método.

| Fase (código) | Acto metodológico | Estándar que emula | Upgrade v3.1 | Etiqueta |
|---|---|---|---|---|
| **1 Ingest** (`phase1_ingest`) | Search strategy reproducible | PRISMA-S | Multi-fuente (PMC+OpenAlex+Unpaywall), exclusión de retractados, query congelada en manifest | — |
| ~~2 Filter~~ | Screening automatizado | (ASReview) | **Eliminada**; el triage Haiku es el screening de facto | — |
| **3a Triage** (`run_triage`) | Cribado de elegibilidad | PRISMA screening box | Tool-use estricto, conteo por fuente para el flow diagram | `[LLM]` |
| **3c Enrich** (`enrich_with_fulltext`) | Recuperación de texto completo | — | Unpaywall fallback (`flat_pdf`), % cobertura reportado | — |
| **3d Deep** (`_run_arbiter_pass`) | Extracción doble independiente + reconciliación | Doble revisor de SR con arbitraje | Tool-use (yield 99%); NOS/GRADE/QUADAS/8-axis bias; provenance ≥5/paper | `[CONSENSUS]` |
| **3d-bis Norm** (`umls_normalizer`) | Linkage ontológico | UMLS/MeSH | Verificación contra UMLS REST → `[VERIFIED]`; % verificados | `[VERIFIED]`/`[LLM]` |
| **4 Persist** (`phase4_store`) | Custodia + cribado de integridad | Retraction screening | `is_retracted` vía Crossref; exclusión + listado | — |
| **5 Analyze** (`phase5_analyze`) | Meta-análisis + certeza | DerSimonian–Laird, Egger, GRADE | PyMARE/statsmodels (`[CALC]`), leave-one-out, GRADE SoF | `[CALC]` |
| **6 Report** (`phase6_report`) | Reporting + reproducibilidad | PRISMA 2020 flow + manifest | Flow diagram, Run ID, claims clicables, DOCX, SoF, QA sheet | mixto |

**Encuadre honesto transversal (regla 1):** PRISMA-conformant *reporting*, no SR
registrado — sin pre-registro de protocolo ni doble cribado humano. Cumplir el formato
sin fingir el proceso. Esto se declara en portada, página legal y QA sheet.

---

## 5. Capa de producto y presentación

Lo que convierte "informe generado por IA" en "deliverable de consultoría":

1. **Identidad y plantillas.** Marca *Hams & Co. Research Division* consistente entre PDF, DOCX, HTML y one-pager: portada, confidencialidad, control de versiones, TOC, numeración. Hoy el HTML lleva `<title>Long COVID Research Analysis</title>` hardcodeado (F4) → parametrizar por topic.
2. **Tres audiencias, tres documentos** (ya existe la estructura: research / DD / executive). Añadir **one-pager C-level** y **QA sheet** como entregables de primera clase.
3. **Reproducibilidad como portada.** Run ID (hash del manifest) visible; "Methods in full" derivado del manifest; "Evidence current as of [fecha]" → gancho de re-run recurrente (ingreso recurrente).
4. **Verificabilidad sin BD.** Claims `[LLM]`/`[CONSENSUS]` enlazados a provenance; supplement ZIP + bibliografía `.ris`/`.bib`. Criterio duro: verificar cualquier claim en <1 min.
5. **Formato que el comprador ya lee.** GRADE SoF y PRISMA flow eliminan la fricción "¿y esto cómo lo interpreto?" para medical affairs / BD&L.
6. **Cobertura cross-plataforma del entregable.** El `.exe` es Windows, pero el `pyproject` declara POSIX. `os.startfile` (F8) y la copia a Desktop fallan fuera de Windows → degradar con gracia (abrir vía `webbrowser`/`xdg-open`, devolver ruta) para no romper demos en Mac/Linux.
7. **Distribución y secretos.** `bundled_credentials` empotra claves en el `.exe` (F9) — aceptable para demo interna, **no** para distribución comercial. Decidir modelo: BYO-key (el cliente pone su `ANTHROPIC_API_KEY`) vs. SaaS con proxy. Documentar en `PUBLISHING.md`.

---

## 6. Hallazgos del código no cubiertos por el contrato (el código manda)

Bloqueantes o trampas detectadas leyendo el repo; cada uno con dónde y qué hacer.

- **F1 — `is_long_covid_focused` es load-bearing, no cosmético.** `select_for_deep_analysis()` (`phase3_extract.py:172`) **filtra** por ese campo. En un topic no-COVID el triage debe seguir emitiéndolo con semántica "topic-focused". El rename de P7 afecta correctitud de selección → **subir a antes de P4** o blindar con test.
- **F2 — "Long COVID" incrustado en reportes no-COVID.** `propagate_uncertainty()` (`phase5_analyze.py:519`) genera `"... that {symptom} is a Long COVID manifestation ..."` literal. Un reporte de Fibromyalgia diría "Long COVID manifestation". **Templatizar con `topic_title()`.** Viola el principio disease-agnostic (regla 8).
- **F3 — Ejes de sesgo COVID-específicos.** `compute_methodology_quality()` (`phase5_analyze.py:438`) cuenta `variant_vaccine_confounding`, `circular_case_definition`, etc. Para producto multi-topic, hacer el set de sesgos genérico o topic-aware.
- **F4 — Título HTML hardcodeado** (`phase6_report.py:339`). Parametrizar por `topic_title()`.
- **F5 — Doble `_parse_batch_results` sobre la misma lista** (`phase3_extract.py:287-293`) genera fallos espurios que luego se filtran a mano. Frágil; **limpiar al migrar a tool-use en P1.**
- **F6 — Las llamadas de síntesis NO usan tool-use.** `call_synthesizer`/`call_due_diligence`/`call_executive_summary` (`phase5_analyze.py`) parsean con fallback "primera `{`…última `}`" y `max_tokens` 16384/4096. **Misma superficie de fallo oversized que P1 ataca en extracción, pero estas generan el reporte final.** Recomendación: extender la disciplina de P1 (structured output / guard de `max_tokens` + retry de compresión) a estas tres llamadas. El contrato solo cubre extracción.
- **F7 — Drift de esquema y setup roto.** `schema.sql` es mínimo; columnas que `phase4_store` escribe (`grade_certainty`, `nos_score`, `bias_audit`, `phenotype_mapping`, `calibrated_certainty`, `reconciliation_triggered`, `arbiter_notes`, `llm_judgment_flags`, `reviewer_a/b_raw`) viven en migraciones v2/v3. El README solo manda correr `schema.sql` + v2 → **omite v3**. Un cliente que siga el setup obtiene errores de columna. Corregir en P7 y consolidar la migración v3.1 como **aditiva**.
- **F8 — Windows-only en runtime** (`os.startfile`, copia a Desktop, `app_server.py:207`). Romper demos POSIX. Degradar.
- **F9 — Secretos empotrados** (`bundled_credentials`) — decidir modelo de distribución (ver §5.7).
- **F10 — Una sola corrida activa.** Checkpoints en filesystem (`utils/checkpointing.py`) + `runner` único; no hay concurrencia de runs. Aceptable para v3.1, declararlo; la tabla `runs` habilita historial pero no ejecución paralela.
- **F11 — medRxiv seleccionado a deep pero sin full text.** `enrich_with_fulltext()` salta `medrxiv_*` (`phase1_ingest.py:239`) → cae a abstract en silencio. **P4/Unpaywall lo resuelve**; marcar `chunking_mode` para trazabilidad.
- **F12 — Coste estimado, no real.** `runner.py` suma `max_papers*COST_PER_TRIAGE + ...` estático. El manifest de P6 debe registrar coste **real** desde `response.usage`.
- **F13 — `runs`/`human_ratings` ya existen sin cablear.** La migración v3 las crea; nadie las escribe/lee. P5 no parte de cero: **extiende y cabla** (evita duplicar tablas y diverger esquemas — reconciliar columnas con el contrato P5.1).

---

## 7. Secuencia, gating y presupuesto

- **Ramas:** una por prioridad (`v31/p0-tests`, `v31/p1-tool-use`, `v31/p2-credibility`, `v31/p3-stats`, `v31/p4-sources`, `v31/p5-product`, `v31/p6-reports`, `v31/p7-debt`). PR contra main, CI verde obligatorio.
- **Gating:** P0 bloquea todo. Run de validación tras **P1, P3 y P4** (camino crítico).
- **Ajuste recomendado al orden:** atender **F1/F2** (rename + de-COVID-ización de outputs) **antes o durante P4**, porque P4 introduce el primer topic no-COVID de validación (Fibromyalgia) y esos bugs se manifestarían justo ahí.
- **Comando de validación:** `uv run python app_server.py` → UI topic "Fibromyalgia", 100 papers, 50 deep.
- **Presupuesto API (del contrato):** smoke 30/3 ~$2 · validación 100/50 ~$23 · validación final topic nuevo ~$23 · **total ~$50**. UMLS/OpenAlex/Unpaywall/Crossref = $0.

---

## 8. Definición de *Done* (checklist de producto)

Técnico (del contrato):
- [ ] Deep success ≥99% (P1) · CI verde, cobertura ≥70% utils+phase5 (P0)
- [ ] % CUIs verificados ≥80% con key, reportado (P2) · 0 retractados en cross-analysis, listados (P2)
- [ ] Estimadores PyMARE/statsmodels en reporte, tests stats verdes, diff <1% o explicado (P3)
- [ ] Desglose por fuente + ≥1 paper vía Unpaywall + dedup limpio (P4)
- [ ] Run en historial y comparable con run previo; CSV limpio; Kappa con ≥5 ratings (P5)
- [ ] PRISMA flow por fuente · Run ID en portada · manifest reproduce búsqueda · claim verificable <1 min · GRADE SoF (≥2 papers) · DOCX editable · ZIP supplement · `.ris` a Zotero · QA sheet en pág. 2 (P6)
- [ ] `.exe` reconstruye y arranca con deps nuevas

Producto (añadido por este plan):
- [ ] Cero "Long COVID" hardcodeado en un reporte de Fibromyalgia (F2/F4)
- [ ] Setup del README reproduce el esquema completo de cero (F7)
- [ ] Entregable no rompe en macOS/Linux en demo (F8)
- [ ] Coste reportado = coste real medido (F12)
- [ ] Modelo de distribución/secretos decidido y documentado (F9, §5.7)
- [ ] Encuadre honesto presente en portada + página legal + QA sheet

---

## Qué NO hacer en v3.1 (del contrato)

No migrar a FastAPI · no living-review / multi-proveedor / ClinicalTrials.gov (v4) ·
no cambiar semántica de campos de extracción en P1 (solo el mecanismo de entrega) · no
tocar la arquitectura multiprocessing · ningún cambio sin test desde P0.

---

*Hams & Co. Research Division — Master Plan de Optimización v3.1. 2026-06-09.*
