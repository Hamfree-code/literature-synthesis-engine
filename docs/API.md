# HTTP API (Flask) — v3.1

The desktop app serves a localhost Flask server (default `http://localhost:7432`).
v3.1 stays on Flask (no FastAPI migration, per the contract). Endpoints:

## Run control
| Method | Path | Purpose |
|---|---|---|
| GET | `/` | The analysis UI (HTML). |
| GET | `/ping` | Liveness probe. |
| GET | `/status` | Current run state (running, phase, spend, pdf_path). |
| POST | `/start` | Start a run. Body: `{topic, mesh_terms?, max_papers?, max_deep?}`. |
| POST | `/cancel` | Terminate the worker process. |
| GET | `/stream` | Server-Sent Events stream of run logs/phases. |
| GET | `/report` | Open the generated PDF (cross-platform; returns the path if it can't auto-open). |

## Run history & exports (P5)
| Method | Path | Purpose |
|---|---|---|
| GET | `/runs` | List recent runs from the `runs` registry. |
| GET | `/runs/<id>/export.json` | Full run record as JSON. |
| GET | `/runs/<id>/extractions.csv` | Extractions for a run as CSV (Excel/Numbers-clean). |

## Validation (P5.2)
| Method | Path | Purpose |
|---|---|---|
| GET | `/kappa` | Cohen's Kappa / RMSE panel of human ratings vs AI extractions (per variable, Landis & Koch bands). |
| POST | `/ratings` | Insert one human rating. Body: `{paper_id, rater_id, field_name, field_kind, rating_value}`. This is the only way `human_ratings` fills up. |

All Supabase-backed endpoints degrade gracefully (return an error JSON, never
crash) when Supabase is unreachable.

## Report artefacts (P6)
Each run writes, under the app data `reports/` directory:
- `research_<slug>_<date>.{md,html,pdf,docx}` — main report (DOCX is editable),
- `research_<slug>_<date>_due_diligence.{…}` and `_executive_summary.{…}`,
- `one_pager_<slug>_<date>.md` — C-level one-pager,
- `supplement_<slug>_<date>.zip` — `run_manifest.json`, `prisma_flow.svg`,
  `references.ris`, `references.bib`, `extractions.csv`, `provenance.csv`,
  `evidence_table.xlsx`.

The **Run ID** (SHA-256 of the manifest) on the report cover is reproducible:
`utils.run_manifest.verify_manifest(json.load(open("run_manifest.json")))`.
