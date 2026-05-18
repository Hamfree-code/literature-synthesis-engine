# Literature Synthesis Engine — Roadmap v3.0

This roadmap is the implementation contract for v3.0 of the engine. All Priority 1–3 sections were implemented in the 2026-05-17 session; Priority 4 is deferred. This document is retained as the record of design decisions, success criteria, and the rationale behind each priority.

**Implementation status (2026-05-17):**
- Priority 1.1 (multiprocessing) — ✅ done
- Priority 1.2 (XML section chunking) — ✅ done
- Priority 2.1 (two-step arbiter) — ✅ done
- Priority 2.2 (UMLS / MeSH normalisation) — ✅ done (declared limitation: CUIs are LLM-inferred, not validated against the UMLS REST API)
- Priority 2.3 (Cohen's Kappa validation) — ✅ engine implemented; UI integration deferred
- Priority 3.1 (LLM / CALC / CONSENSUS badges) — ✅ done
- Priority 3.2 (conservative DD) — ✅ done
- Priority 3.3 (mandatory methodology section) — ✅ done
- Priority 4 (run history & API) — ⏸ deferred

**Discrepancies between this spec and the existing codebase** are catalogued in `docs/SPEC_V3_DISCREPANCIES.md` (notably: no `run_pipeline.py` — the entry point is `app_server.py`; WeasyPrint was already retired before this spec; UMLS REST API access is unavailable so CUIs are LLM-generated and flagged accordingly).

---

## Antes de empezar

Lee en este orden:
1. `app_server.py` — arquitectura actual del servidor
2. `run_pipeline.py` — entry point y flujo de fases
3. `pipeline/phase3_extract.py` — extracción actual (single-pass)
4. `pipeline/phase1_ingest.py` — ingestión y truncamiento actual
5. `pipeline/phase5_analyze.py` — análisis estadístico actual
6. `config/prompts/extraction_sonnet.txt` — prompt de extracción actual
7. `config/prompts/due_diligence_sonnet.txt` — prompt de DD actual
8. `config/schema.sql` + `config/schema_v2_migration.sql` — esquema actual

Solo después de leer todo esto, implementa en el orden de prioridad que se indica abajo.

---

## PRIORIDAD 1 — Correcciones críticas (bloquean producción)

### 1.1 Migración de threading a multiprocessing

**Problema:** El uso de `threading.Thread` dentro de Flask provoca bloqueo intermitente del bucle de eventos por el GIL cuando NumPy ejecuta estadísticas pesadas o WeasyPrint compila PDFs. Los Server-Sent Events (`/stream`) se desconectan de forma sistemática.

**Solución:** Separar completamente la capa web del motor de pipeline usando `multiprocessing`. El intercambio de eventos se canaliza mediante `multiprocessing.Queue`.

```python
import multiprocessing
from run_pipeline import execute_industrial_pipeline

event_queue = multiprocessing.Queue()

def worker_process(queue, disease, mesh_terms, max_papers, max_deep):
    try:
        execute_industrial_pipeline(queue, disease, mesh_terms, max_papers, max_deep)
    except Exception as e:
        queue.put({"event": "error", "message": str(e)})
    finally:
        queue.put({"event": "done"})

@app.route("/start", methods=["POST"])
def start_pipeline():
    p = multiprocessing.Process(
        target=worker_process,
        args=(event_queue, disease, mesh_terms, max_papers, max_deep)
    )
    p.start()
    return {"status": "process_spawned", "pid": p.pid}
```

**Archivos a modificar:** `app_server.py`. No tocar `run_pipeline.py` — solo añadir `execute_industrial_pipeline()` como entry point alternativo que acepta una queue como primer argumento.

---

### 1.2 Chunking inteligente de texto completo basado en secciones XML

**Problema:** El límite plano de 120.000 caracteres trunca los documentos de forma ciega. Las secciones de Discusión, Limitaciones, Conflictos de Interés y Financiación están siempre al final del XML de PMC y son eliminadas sistemáticamente.

**Solución:** Reemplazar el truncamiento por un parser semántico que explota las etiquetas `<sec sec-type="...">` nativas de PMC XML.

```python
from bs4 import BeautifulSoup

def extract_structured_sections(xml_content: str) -> dict:
    soup = BeautifulSoup(xml_content, "xml")
    sections = {
        "metadata": {},
        "methods": "",
        "results": "",
        "discussion_limitations": "",
        "conflicts_funding": ""
    }
    for sec in soup.find_all("sec"):
        sec_type = sec.get("sec-type", "").lower()
        text = sec.get_text(separator=" ", strip=True)
        if any(k in sec_type for k in ["method", "material"]):
            sections["methods"] += f"\n{text}"
        elif "result" in sec_type:
            sections["results"] += f"\n{text}"
        elif any(k in sec_type for k in ["discuss", "limit", "caveat"]):
            sections["discussion_limitations"] += f"\n{text}"
        elif any(k in sec_type for k in ["conflict", "fund", "financ"]):
            sections["conflicts_funding"] += f"\n{text}"
    return sections
```

**Archivos a modificar:** `pipeline/phase3_extract.py` — función de enriquecimiento de texto completo. Añadir `utils/xml_parser.py` con `extract_structured_sections()`. El prompt de extracción de Sonnet debe recibir las secciones por separado, no el texto completo concatenado.

---

## PRIORIDAD 2 — Rigor científico (calidad del output)

### 2.1 Extracción en dos pasos con árbitro metodológico

**Problema:** La extracción single-pass con Sonnet introduce sesgo de anclaje. Si la primera inferencia es incorrecta, el error se consolida en Supabase sin contrastación — violación directa de las directrices Cochrane de doble revisión.

**Solución:** Dos agentes de extracción independientes a temperaturas distintas, cuyas salidas convergen en un tercer agente árbitro.

```
Revisor A (Sonnet, temp=0.1) ──┐
                                ├──→ Árbitro / Juez Metodológico (Sonnet, temp=0.0)
Revisor B (Sonnet, temp=0.3) ──┘
```

**System prompt del árbitro:**
```
Eres el Revisor Clínico Principal de Hams & Co. Research Division. 
Tu tarea es resolver discrepancias entre dos extracciones independientes.

Reglas obligatorias:
1. Para variables cuantitativas con discrepancia: localiza el valor en el texto original. 
   Si ningún revisor coincide con el texto, extrae directamente y levanta 
   "reconciliation_triggered": true.
2. Para evaluaciones QUADAS-2/GRADE con discordancia entre Alto y Bajo riesgo: 
   audita las citas textuales asociadas. Si la cita no justifica penalización 
   metodológica, prevalece la calificación de menor riesgo.
3. Output: objeto JSON estricto con array "provenance_quotes" con citas literales validadas.
4. Añade "llm_judgment": true a todo campo que sea inferencia del modelo.
   Añade "llm_judgment": false a todo campo derivado de regla determinística.
```

**Archivos a modificar:** `pipeline/phase3_extract.py` — refactorizar `deep_extract_paper()` para ejecutar dos llamadas paralelas y una tercera de arbitraje. Añadir `config/prompts/arbiter_sonnet.txt`. Añadir campo `reconciliation_triggered` y `llm_judgment` al schema de Supabase.

---

### 2.2 Normalización ontológica UMLS/MeSH

**Problema:** Los fenotipos y mecanismos se almacenan como texto libre. "POTS", "Síndrome de Taquicardia Ortostática Postural" y "Disautonomía Cardiovascular" se cuentan como tres entidades distintas en las agregaciones.

**Solución:** Forzar structured outputs con tool calling que obligue al modelo a asociar cada entidad con su CUI de UMLS y su MeSH heading oficial.

```json
{
  "name": "normalize_biomedical_entities",
  "properties": {
    "extracted_phenotypes": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "verbatim_text": {"type": "string"},
          "umls_cui": {"type": "string"},
          "mesh_heading": {"type": "string"}
        },
        "required": ["verbatim_text", "umls_cui", "mesh_heading"]
      }
    }
  }
}
```

**Agregación limpia en Supabase:**
```sql
SELECT umls_cui, mesh_heading, count(distinct paper_id) as unique_papers
FROM extracted_phenotypes
GROUP BY umls_cui, mesh_heading
ORDER BY unique_papers DESC;
```

**Archivos a modificar:** `pipeline/phase3_extract.py` — añadir tool call de normalización post-extracción. Añadir tabla `extracted_phenotypes` al schema con campos `paper_id`, `verbatim_text`, `umls_cui`, `mesh_heading`. Añadir `config/schema_v3_migration.sql`.

---

### 2.3 Validación estadística frente a benchmark humano (Cohen's Kappa)

**Problema:** El sistema no tiene marco de evaluación cuantitativo de ground truth. Sin métricas de concordancia inter-observador, no se puede defender la precisión ante auditores externos.

**Solución:** Motor de validación que calcula Cohen's Kappa para variables discretas (GRADE, NOS, bias flags) y RMSE/Pearson para variables continuas.

```python
import numpy as np

def compute_cohens_kappa(human_ratings: list, ai_ratings: list, num_classes: int = 4) -> float:
    h = np.array(human_ratings, dtype=int)
    a = np.array(ai_ratings, dtype=int)
    n = len(h)
    if n == 0:
        return 0.0
    confusion_matrix = np.zeros((num_classes, num_classes))
    for i in range(n):
        confusion_matrix[h[i]][a[i]] += 1
    p_o = np.trace(confusion_matrix) / n
    row_sums = np.sum(confusion_matrix, axis=1) / n
    col_sums = np.sum(confusion_matrix, axis=0) / n
    p_e = np.sum(row_sums * col_sums)
    return float((p_o - p_e) / (1 - p_e)) if p_e != 1 else 1.0
```

**Archivos a añadir:** `utils/validation_engine.py` con `compute_cohens_kappa()`, `compute_rmse()`, `compute_pearson()`. La UI debe mostrar las métricas de validación cuando existan ratings humanos en la base de datos. Añadir tabla `human_ratings` al schema v3.

---

## PRIORIDAD 3 — Rigor epistemológico en outputs

### 3.1 Distinción LLM judgment vs regla determinística

En **todos** los outputs (research report, due diligence, executive summary), añadir indicador visual en cada hallazgo:

- `[LLM]` — inferencia del modelo, verificar contra provenance quote
- `[CALC]` — derivado de cálculo determinístico (DL pooling, Egger's, etc.)
- `[CONSENSUS]` — resultado del árbitro tras reconciliación de dos revisores

Implementar como campo en el JSON de extracción y como badge en los templates Jinja2.

---

### 3.2 Due diligence más conservador

**Modificar `config/prompts/due_diligence_sonnet.txt`:**

- Añadir al system prompt: *"Never recommend a Phase II target unless at least 2 papers with GRADE Moderate or higher support the mechanism. If this threshold is not met, describe the mechanism as hypothesis-generating only."*
- Añadir campo obligatorio `confidence_in_recommendation` (0–100) a cada recomendación del DD.
- La sección "Recommended Target Phenotype & Phase II Skeleton" debe estar precedida por: *"The following is a hypothesis-generating framework, not a clinical or investment recommendation. All design choices require independent expert validation."*
- Si `n_deep < 10`, el DD debe abrirse con una advertencia visible: *"This brief is based on fewer than 10 deeply-analysed papers. Conclusions are hypothesis-generating only."*

---

### 3.3 Sección "Methodology & Limitations" obligatoria

En **todos** los reportes generados, la sección de limitaciones debe:
- Aparecer visible, no enterrada al final
- Incluir siempre: número de papers triados, número de deep analyses, porcentaje open-access, coste del run, si el árbitro fue activado (reconciliation_triggered count), y si hay ratings humanos disponibles para validación
- Incluir siempre: *"All structured extractions are LLM-generated. The provenance layer enables literal-quote verification but does not prevent model error."*

---

## PRIORIDAD 4 — Mejoras de producto (post-estabilización)

### 4.1 Historial de runs y comparación

- Añadir tabla `runs` a Supabase: `run_id`, `topic`, `date`, `n_papers`, `n_deep`, `api_cost`, `runtime_seconds`, `grade_distribution` (jsonb)
- La UI debe mostrar el historial de runs y permitir comparar dos runs sobre el mismo topic
- Export: JSON completo del run, CSV de extractions, PDF mejorado

### 4.2 API limpia

- Exponer endpoints documentados: `POST /runs`, `GET /runs/{id}`, `GET /runs/{id}/report`, `GET /runs/{id}/due-diligence`
- Swagger/OpenAPI automático via FastAPI (cuando se migre desde Flask)

### 4.3 Migración Flask → FastAPI (diferible)

Solo cuando las prioridades 1-3 estén implementadas y estables. No bloquear el trabajo actual por esto.

---

## Reglas de oro (no negociables en ningún cambio)

1. **Never fake rigor** — Si la evidencia es débil, decirlo fuerte y claro en el output.
2. **Provenance first** — Todo claim necesita cita literal verificable. El árbitro debe validar las citas, no solo los valores.
3. **Conservative by default** — Especialmente en DD. Menos recomendaciones, más condicionadas.
4. **LLM judgment siempre etiquetado** — El lector debe saber qué es inferencia del modelo y qué es cálculo determinístico.
5. **El código manda** — Si algo en el código actual difiere de esta spec, documentarlo antes de sobreescribirlo.
6. **No romper lo que funciona** — Las mejoras de PRIORIDAD 1 se implementan primero y se validan con un run de prueba (100 papers, 50 deep) antes de continuar.

---

## Validación post-implementación

Después de implementar cada prioridad, ejecutar:

```bash
uv run python run_pipeline.py --topic "Fibromyalgia" --max-papers 100 --max-deep 50 --skip-phases 2
```

El run de validación debe:
- Completarse sin desconexiones de SSE (valida PRIORIDAD 1.1)
- Mostrar secciones de Discusión y Limitaciones en las extracciones (valida PRIORIDAD 1.2)
- Mostrar `reconciliation_triggered` count en el resumen del run (valida PRIORIDAD 2.1)
- Mostrar `umls_cui` en al menos el 70% de los fenotipos extraídos (valida PRIORIDAD 2.2)
- Mostrar `[LLM]` / `[CALC]` badges en el research report (valida PRIORIDAD 3.1)
- Mostrar la advertencia de corpus pequeño si n_deep < 10 (valida PRIORIDAD 3.2)
