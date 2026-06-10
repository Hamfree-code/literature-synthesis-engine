"""Run manifest — the reproducibility package (UPGRADE v3.1 — P6.2).

Freezes everything a third party needs to reproduce a run: engine version + git
SHA, exact queries per source, temporal window, model strings + temperatures,
active settings flags, SHA-256 of every prompt, per-phase counts and real cost.
The SHA-256 of the manifest is the verifiable **Run ID** printed on the report
cover; the report's "Methods in full" appendix is rendered from this single
source of truth so the document and the execution can never diverge.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import date, datetime, timezone

from config.settings import settings


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def prompt_hashes(prompts_dir) -> dict[str, str]:
    """SHA-256 of each prompt file (sorted by name)."""
    from pathlib import Path

    prompts_dir = Path(prompts_dir)
    out: dict[str, str] = {}
    if not prompts_dir.exists():
        return out
    for f in sorted(prompts_dir.glob("*.txt")):
        out[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def active_flags() -> dict:
    """The settings flags that materially change the run (not secrets)."""
    keys = [
        "ARBITER_ENABLED",
        "UMLS_NORMALIZATION_ENABLED",
        "EXTRACTION_TOOL_USE",
        "DEEP_MAX_TOKENS",
        "DEEP_MAX_RETRIES",
        "UMLS_VERIFY_ENABLED",
        "INCLUDE_RETRACTED",
        "RETRACTION_CHECK_ENABLED",
        "STATS_REFERENCE_IMPL",
        "OPENALEX_ENABLED",
        "MEDRXIV_LEGACY",
        "UNPAYWALL_ENABLED",
        "QUADAS_CUTOFF",
        "HETEROGENEITY_CRITICAL_THRESHOLD",
        "MIN_STUDIES_PUBLICATION_BIAS",
        "LEAVE_ONE_OUT_INFLUENCE_THRESHOLD",
    ]
    return {k: getattr(settings, k, None) for k in keys}


def stable_sha256(manifest: dict) -> str:
    """Deterministic hash of the manifest with the run_id field itself excluded."""
    payload = {k: v for k, v in manifest.items() if k != "run_id"}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def build_manifest(
    *,
    topic: str,
    mesh_terms: str | None,
    queries_by_source: dict,
    sources_breakdown: dict,
    phase_counts: dict,
    cost_usd: float,
    runtime_seconds: float | None,
    prompts_dir,
    search_date: str | None = None,
) -> dict:
    """Assemble the manifest. The Run ID (SHA-256) is filled in last so it is a
    stable function of all other content."""
    manifest = {
        "engine_version": settings.ENGINE_VERSION,
        "git_sha": _git_sha(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "search_date": search_date or date.today().isoformat(),
        "topic": topic,
        "mesh_terms": mesh_terms,
        "queries_by_source": queries_by_source,
        "sources_breakdown": sources_breakdown,
        "models": {
            "triage": settings.ANTHROPIC_HAIKU_MODEL,
            "extraction": settings.ANTHROPIC_SONNET_MODEL,
            "arbiter": settings.ANTHROPIC_SONNET_MODEL,
            "synthesis": settings.ANTHROPIC_SONNET_MODEL,
        },
        "temperatures": {"reviewer_a": 0.1, "reviewer_b": 0.3, "arbiter": 0.0},
        "flags": active_flags(),
        "prompt_sha256": prompt_hashes(prompts_dir),
        "phase_counts": phase_counts,
        "api_cost_usd": round(float(cost_usd), 4),
        "runtime_seconds": round(runtime_seconds, 1) if runtime_seconds else None,
    }
    manifest["run_id"] = stable_sha256(manifest)
    return manifest


def write_manifest(manifest: dict, out_path) -> str:
    from pathlib import Path

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest["run_id"]


def verify_manifest(manifest: dict) -> bool:
    """True when the stored run_id matches a freshly computed hash."""
    return manifest.get("run_id") == stable_sha256(manifest)
