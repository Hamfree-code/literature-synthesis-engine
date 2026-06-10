"""Run registry writes + reads (UPGRADE v3.1 — P5.1).

Persists one row per run to the Supabase ``runs`` table (created by the v3
migration, extended by v3.1) so the UI can show history and compare runs.
Everything is best-effort: a missing/unreachable Supabase never breaks a run.
"""

from __future__ import annotations


def build_run_row(manifest: dict, qa: dict) -> dict:
    """Shape a runs-table row from the manifest + QA certificate."""
    sources = qa.get("sources_breakdown") or {}
    phase = manifest.get("phase_counts") or {}
    return {
        "id": None,  # let DB default a uuid; we keep manifest run_id as manifest_sha256
        "topic": manifest.get("topic"),
        "mesh_terms": manifest.get("mesh_terms"),
        "sources": list(sources.keys()),
        "sources_breakdown": sources,
        "n_papers_triaged": phase.get("screened"),
        "n_papers_deep": phase.get("included_deep"),
        "deep_success_rate": qa.get("deep_success_rate"),
        "cui_verified_pct": qa.get("cui_verified_pct"),
        "fulltext_coverage_pct": qa.get("fulltext_coverage_pct"),
        "n_retracted_excluded": qa.get("n_retracted_excluded"),
        "n_reconciliations": qa.get("reconciliations"),
        "api_cost_usd": qa.get("api_cost_usd"),
        "runtime_seconds": qa.get("runtime_seconds"),
        "manifest_sha256": manifest.get("run_id"),
        "engine_version": manifest.get("engine_version"),
    }


def upsert_run(manifest: dict, qa: dict) -> bool:
    """Insert a run row. Returns True on success, False on any failure."""
    row = {k: v for k, v in build_run_row(manifest, qa).items() if v is not None and k != "id"}
    try:
        from utils.supabase_client import sb

        sb().table("runs").upsert(row, on_conflict="manifest_sha256").execute()
        return True
    except Exception:
        return False


def list_runs(limit: int = 50) -> list[dict]:
    try:
        from utils.supabase_client import sb

        res = sb().table("runs").select("*").order("created_at", desc=True).limit(limit).execute()
        return res.data or []
    except Exception:
        return []


def get_run(run_id: str) -> dict | None:
    try:
        from utils.supabase_client import sb

        res = sb().table("runs").select("*").eq("id", run_id).limit(1).execute()
        rows = res.data or []
        return rows[0] if rows else None
    except Exception:
        return None
