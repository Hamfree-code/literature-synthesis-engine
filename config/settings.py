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
    ANTHROPIC_OPUS_MODEL: str = "claude-opus-4-8"   # arbiter (reconciles A+B)

    # Gemini (multi-provider extraction engine). Triage filter + deep Reviewer B
    # run on Gemini; Reviewer A stays on Claude Sonnet, arbiter on Claude Opus.
    GEMINI_API_KEY: str = ""
    GEMINI_FLASH_MODEL: str = "gemini-3.5-flash"    # triage filter
    GEMINI_PRO_MODEL: str = "gemini-3.1-pro"        # deep Reviewer B
    GEMINI_CONCURRENCY: int = 8                      # max in-flight async Gemini calls

    # NCBI / PubMed
    NCBI_API_KEY: str = ""
    NCBI_EMAIL: str = ""

    # OpenAlex (free key required since 2026-02-13; 100 credits/day without one)
    OPENALEX_API_KEY: str = ""
    OPENALEX_MAILTO: str = ""
    OPENALEX_ENABLED: bool = True

    # Supabase
    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = ""

    # Pipeline limits
    MAX_PAPERS: int = 5000
    MAX_DEEP_ANALYSIS: int = 500
    BATCH_SIZE: int = 100
    LOG_LEVEL: str = "INFO"

    # Cost guardrails. The pipeline aborts before submitting any paid batch if
    # the up-front estimate exceeds MAX_SPEND_USD. Set to 0 to disable the cap.
    MAX_SPEND_USD: float = 25.0
    # Maximum hours to poll a single Batch API job before giving up (the batch
    # id is persisted, so a later run can resume rather than resubmit).
    BATCH_MAX_POLL_HOURS: float = 26.0  # Anthropic batches expire at 24h

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

    @property
    def supabase_enabled(self) -> bool:
        """True only when both Supabase credentials are present. Phases that
        persist to Supabase no-op (rather than crash) when this is False."""
        return bool(self.SUPABASE_URL.strip()) and bool(self.SUPABASE_KEY.strip())


settings = Settings()
