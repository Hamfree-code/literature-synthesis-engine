"""Pipeline worker entry point — runs the analysis in a separate process.

Master Improvement Spec v3.0 — Priority 1.1.

This module is imported by both the Flask parent (to spawn the worker) and the
worker child process itself (which executes the pipeline). The communication
contract is a single multiprocessing.Queue passed as the first argument; the
worker pushes event dicts and the parent forwards them to Server-Sent Events
on the /stream endpoint.

The event protocol is identical to the in-process threading version used in
the previous build (so the HTML UI does not need to change). Event types:

  {"type": "log", "message": str, "level": "info"|"warn"|"error", "ts": float}
  {"type": "phase_start", "phase_index": int, "phase_key": str, "label": str}
  {"type": "phase_complete", "phase_index": int, "phase_key": str}
  {"type": "spend", "amount": float}
  {"type": "done", "pdf_path": str, "spend": float}
  {"type": "error", "message": str}
  {"type": "cancelled"}

PyInstaller / Windows note: multiprocessing on Windows uses the 'spawn' start
method, which re-imports the entry-point module in the child. Because our exe
is bundled, `multiprocessing.freeze_support()` MUST be called at the very start
of app_server.main() before any pipeline import.
"""

from __future__ import annotations

import multiprocessing
import time
from datetime import date

import bundled_credentials


COST_PER_TRIAGE = 0.003
COST_PER_DEEP_SINGLE = 0.15
COST_PER_DEEP_ARBITER = 0.45  # 3x for two reviewers + arbiter

PHASES = [
    ("ingest", "Ingestion — fetching papers from PubMed Central"),
    ("triage", "Triage — Claude Haiku 4.5 abstract pass"),
    ("enrich", "Enrichment — fetching full text from PMC OA"),
    ("deep", "Deep extraction — Sonnet (two-step arbiter)"),
    ("store", "Storage — persisting to Supabase"),
    ("analyze", "Cross-analysis — calibrated certainty + synthesis"),
    ("report", "Report — generating Markdown / HTML / PDF"),
]


def _emit(q: "multiprocessing.Queue", event: dict) -> None:
    q.put(event)


def _log(q, message: str, level: str = "info") -> None:
    _emit(q, {"type": "log", "message": message, "level": level, "ts": time.time()})


def _phase_start(q, idx: int) -> None:
    key, label = PHASES[idx]
    _emit(q, {"type": "phase_start", "phase_index": idx, "phase_key": key, "label": label})


def _phase_complete(q, idx: int) -> None:
    key, _ = PHASES[idx]
    _emit(q, {"type": "phase_complete", "phase_index": idx, "phase_key": key})


def _add_spend(q, total: dict, amount: float) -> None:
    total["spend"] = round(total.get("spend", 0.0) + amount, 4)
    _emit(q, {"type": "spend", "amount": total["spend"]})


def execute_industrial_pipeline(
    q: "multiprocessing.Queue",
    disease: str,
    mesh_terms: str | None,
    max_papers: int,
    max_deep: int,
) -> None:
    """The worker entry point. Runs the full pipeline; pushes events to q.

    Designed to be called via multiprocessing.Process. Re-installs the bundled
    credentials at entry because Windows 'spawn' creates a fresh interpreter
    without the parent's environment override.
    """
    try:
        bundled_credentials.install()

        # Topic-change guard: wipe stale state from a previous run if the
        # topic/mesh has changed.
        from utils.run_context import clear_stale_state_if_topic_changed

        wiped, prev_topic = clear_stale_state_if_topic_changed(disease, mesh_terms)
        if wiped:
            _log(q, f"Topic changed (previous: '{prev_topic}'). Cleared stale checkpoints + data.", "warn")

        from config.settings import settings
        from app_paths import APP_DATA_DIR, USER_DESKTOP

        per_deep_cost = COST_PER_DEEP_ARBITER if settings.ARBITER_ENABLED else COST_PER_DEEP_SINGLE
        total = {"spend": 0.0}

        _log(q, f"Starting analysis: topic='{disease}', max_papers={max_papers}, max_deep={max_deep}")
        if mesh_terms:
            _log(q, f"MeSH terms filter: {mesh_terms}")
        _log(q, f"App data: {APP_DATA_DIR}")
        est = max_papers * COST_PER_TRIAGE + max_deep * per_deep_cost + 0.50
        _log(q, f"Estimated cost: ${est:.2f} (arbiter={'ON' if settings.ARBITER_ENABLED else 'OFF'})")

        import asyncio
        from pipeline import phase1_ingest, phase3_extract, phase4_store, phase5_analyze, phase6_report

        # Phase 1 — Ingest
        _phase_start(q, 0)
        _log(q, "Querying PubMed Central via NCBI E-utilities...")
        asyncio.run(phase1_ingest.run(max_papers=max_papers, topic=disease, mesh_terms=mesh_terms))
        _phase_complete(q, 0)

        # Phase 3a triage
        _phase_start(q, 1)
        _log(q, f"Submitting Haiku batch (up to {max_papers} papers)...")
        phase3_extract.run_triage(max_papers=max_papers)
        _add_spend(q, total, max_papers * COST_PER_TRIAGE)
        _phase_complete(q, 1)

        # Phase 3c enrich
        _phase_start(q, 2)
        top_ids = phase3_extract.select_for_deep_analysis(top_n=max_deep)
        _log(q, f"Selected {len(top_ids)} papers for deep analysis")
        if top_ids:
            _log(q, "Fetching full text from PMC Open Access (XML section-based chunking)...")
            asyncio.run(phase1_ingest.enrich_with_fulltext(top_ids))
        _phase_complete(q, 2)

        # Phase 3d deep extraction (now with arbiter)
        _phase_start(q, 3)
        mode = "arbiter (A+B + reconciliation)" if settings.ARBITER_ENABLED else "single-pass"
        _log(q, f"Submitting Sonnet deep extraction ({len(top_ids)} papers, mode={mode})...")
        phase3_extract.run_deep(paper_ids=top_ids)
        _add_spend(q, total, len(top_ids) * per_deep_cost)
        _phase_complete(q, 3)

        # Phase 4 — Persist
        _phase_start(q, 4)
        _log(q, "Persisting papers / extractions / provenance / normalised entities to Supabase...")
        phase4_store.run()
        _phase_complete(q, 4)

        # Phase 5 — Cross-analysis
        _phase_start(q, 5)
        _log(q, "Cross-analysis + 3× Sonnet synthesis (research / DD / executive)...")
        phase5_analyze.run()
        _add_spend(q, total, 0.50)
        _phase_complete(q, 5)

        # Phase 6 — Reports
        _phase_start(q, 6)
        _log(q, "Generating Markdown / HTML / PDF reports...")
        phase6_report.run()
        _phase_complete(q, 6)

        today = date.today().isoformat()
        from utils.run_context import topic_slug as _slug

        slug = _slug()
        desktop_pdf = USER_DESKTOP / f"research_{slug}_{today}.pdf"
        archived_pdf = APP_DATA_DIR / "reports" / f"research_{slug}_{today}.pdf"
        pdf_path = ""
        if desktop_pdf.exists():
            pdf_path = str(desktop_pdf)
        elif archived_pdf.exists():
            pdf_path = str(archived_pdf)

        _emit(q, {"type": "done", "pdf_path": pdf_path, "spend": total["spend"]})
        _log(q, f"FINISHED. Report at {pdf_path}", "success")
    except Exception as e:
        tb = f"{type(e).__name__}: {e}"
        _log(q, f"ERROR: {tb}", "error")
        _emit(q, {"type": "error", "message": tb})
