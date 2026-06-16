"""Centralized configuration — loads .env and exposes typed settings."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Anthropic
    ANTHROPIC_API_KEY: str
    ANTHROPIC_HAIKU_MODEL: str = "claude-haiku-4-5-20251001"
    ANTHROPIC_SONNET_MODEL: str = "claude-sonnet-4-6"

    # NCBI / PubMed
    NCBI_API_KEY: str = ""
    NCBI_EMAIL: str = ""

    # Supabase
    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = ""

    # Pipeline limits
    MAX_PAPERS: int = 5000
    MAX_DEEP_ANALYSIS: int = 500
    BATCH_SIZE: int = 100
    LOG_LEVEL: str = "INFO"

    # Methodology — emulating Siciliano et al. 2024 (Movement Disorders)
    QUADAS_CUTOFF: int = 13                            # Papers with QUADAS ≤ this are excluded from quantitative synthesis
    QUADAS_MAX: int = 19                               # Scale 0-19
    HETEROGENEITY_CRITICAL_THRESHOLD: float = 90.0     # I² ≥ → RE model + forest plot + moderator analysis
    HETEROGENEITY_HIGH_THRESHOLD: float = 75.0
    HETEROGENEITY_MODERATE_THRESHOLD: float = 50.0
    HETEROGENEITY_LOW_THRESHOLD: float = 25.0
    MIN_STUDIES_PUBLICATION_BIAS: int = 10             # Egger's test / trim-and-fill require ≥ this many studies
    LEAVE_ONE_OUT_INFLUENCE_THRESHOLD: float = 0.10    # 10% change in pooled estimate = influential paper
    EFFECT_SIZE_NEGLIGIBLE: float = 0.10               # r < this = negligible (Cohen)
    EFFECT_SIZE_WEAK: float = 0.29                     # r in [0.10, 0.29] = weak
    EFFECT_SIZE_MODERATE: float = 0.49                 # r in [0.30, 0.49] = moderate
    # r ≥ 0.50 = strong (implicit)

    # Master Improvement Spec v3.0 — Priority 2.1: two-step extraction + arbiter.
    # When true, every paper is extracted by two independent Sonnet reviewers
    # (temp 0.1 and 0.3) and reconciled by an arbiter (temp 0.0). Triples the
    # Sonnet cost per deep-extracted paper but eliminates anchoring bias.
    ARBITER_ENABLED: bool = True

    # Master Improvement Spec v3.0 — Priority 2.2: UMLS / MeSH normalisation.
    # When true, every deep extraction is followed by one Haiku tool-call that
    # maps the extracted phenotypes / mechanisms to UMLS CUIs and MeSH headings.
    UMLS_NORMALIZATION_ENABLED: bool = True

    # ── UPGRADE v3.2 — Methodological Hardening & Provenance Integrity ──────
    # WP-1: on a JSON parse failure, hand the malformed string to a cheap model
    # (Haiku) once, asking for valid JSON conforming to the schema. Accepted
    # only if it validates. Eliminates the silent technical filter on data-rich
    # papers.
    EXTRACTION_REPAIR_ENABLED: bool = True
    # WP-1: ceiling for deep-extraction output, sized to the largest observed
    # payload + headroom so data-rich papers no longer truncate.
    DEEP_EXTRACTION_MAX_TOKENS: int = 16384
    # WP-5/6: which per-condition outcome dictionary to normalise against.
    OUTCOME_DICTIONARY_CONDITION: str = "long_covid"
    # WP-0/§1: fail the report build if a template affirmatively self-describes
    # as a "systematic review" (EMCU framing).
    EMCU_LINT_ENABLED: bool = True
    # WP-9: fail the report build if narrative certainty language exceeds the
    # calibrated ceiling (prose/calibrated mismatch). The calibrated layer is
    # authoritative; this enforces it.
    RECONCILIATION_STRICT: bool = True


settings = Settings()
