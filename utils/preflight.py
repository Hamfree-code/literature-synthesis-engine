"""Pre-run validation — catch misconfiguration and budget overruns BEFORE any
paid API call is made.

The pipeline spends real money in Phase 3 (Haiku triage + Sonnet deep
extraction). A missing prompt file, an empty API key, or an oversized run
should fail loudly up front rather than after ingestion has already run.
"""
from __future__ import annotations

from dataclasses import dataclass

from config.settings import settings

# Prompt templates the pipeline loads at import / runtime. A missing one only
# surfaces deep into a (paid) run today, so we check them first.
REQUIRED_PROMPTS = [
    "config/prompts/triage_haiku.txt",
    "config/prompts/extraction_sonnet.txt",
    "config/prompts/arbiter_sonnet.txt",
    "config/prompts/synthesis_sonnet.txt",
    "config/prompts/due_diligence_sonnet.txt",
    "config/prompts/executive_summary_sonnet.txt",
    "config/prompts/heterogeneity_analysis.txt",
]

# Rough per-item cost constants (USD), conservative point estimates for the
# up-front budget gate and progress display. Actual spend is reconciled from
# real output counts in runner.py.
COST_PER_TRIAGE = 0.002        # Gemini Flash, one abstract
COST_PER_DEEP_SINGLE = 0.15    # Claude Sonnet single-pass (ARBITER off)
# Multi-provider arbiter path, per paper:
#   Reviewer A (Claude Sonnet ~$0.15) + Reviewer B (Gemini Pro ~$0.12)
#   + Arbiter (Claude Opus ~$0.40) ≈ $0.67 → round up.
COST_PER_DEEP_ARBITER = 0.70
SYNTHESIS_COST = 0.50          # 3 Sonnet synthesis passes + MeSH expansion


def estimate_cost(max_papers: int, max_deep: int, *, arbiter_enabled: bool) -> float:
    """Up-front worst-case cost estimate for a run."""
    per_deep = COST_PER_DEEP_ARBITER if arbiter_enabled else COST_PER_DEEP_SINGLE
    return max_papers * COST_PER_TRIAGE + max_deep * per_deep + SYNTHESIS_COST


def budget_exceeded(estimate: float, cap: float) -> bool:
    """True when a positive cap is set and the estimate exceeds it. A cap of 0
    disables the gate."""
    return cap > 0 and estimate > cap


def check_prompt_files() -> list[str]:
    """Return the list of required prompt files that are missing."""
    from app_paths import resource
    missing = []
    for rel in REQUIRED_PROMPTS:
        try:
            if not resource(rel).exists():
                missing.append(rel)
        except Exception:
            missing.append(rel)
    return missing


def check_config() -> tuple[list[str], list[str]]:
    """Return (fatal_errors, warnings) for the current settings."""
    errors: list[str] = []
    warnings: list[str] = []
    if not (settings.ANTHROPIC_API_KEY or "").strip():
        errors.append("ANTHROPIC_API_KEY is empty — Reviewer A / arbiter cannot run.")
    if not (settings.GEMINI_API_KEY or "").strip():
        errors.append("GEMINI_API_KEY is empty — triage filter and Reviewer B run on Gemini.")
    if not (settings.NCBI_API_KEY or "").strip():
        warnings.append("NCBI_API_KEY empty — PubMed fetching will be heavily rate-limited.")
    if settings.OPENALEX_ENABLED and not (settings.OPENALEX_API_KEY or "").strip():
        warnings.append("OPENALEX_API_KEY empty — OpenAlex limited to 100 works/day (free key recommended).")
    if not settings.supabase_enabled:
        warnings.append("Supabase not configured — Phase 4 storage will be skipped (data stays local).")
    return errors, warnings


@dataclass
class PreflightReport:
    errors: list[str]
    warnings: list[str]
    estimate_usd: float

    @property
    def ok(self) -> bool:
        return not self.errors


def run_preflight(max_papers: int, max_deep: int) -> PreflightReport:
    """Aggregate all pre-run checks into a single report. Does NOT make network
    calls — it is safe to run for free."""
    errors, warnings = check_config()

    missing = check_prompt_files()
    if missing:
        errors.append("Missing prompt files: " + ", ".join(missing))

    estimate = estimate_cost(max_papers, max_deep, arbiter_enabled=settings.ARBITER_ENABLED)
    if budget_exceeded(estimate, settings.MAX_SPEND_USD):
        errors.append(
            f"Estimated cost ${estimate:.2f} exceeds MAX_SPEND_USD ${settings.MAX_SPEND_USD:.2f}. "
            f"Lower max_deep, disable ARBITER_ENABLED, or raise/disable the cap (0 = no cap)."
        )

    return PreflightReport(errors=errors, warnings=warnings, estimate_usd=estimate)
