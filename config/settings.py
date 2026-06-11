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
    # Arbiter model: reconciles the two reviewers. Opus is the strongest neutral
    # adjudicator and stays inside the Anthropic Batch stack (no new provider
    # integration). Reviewer A stays on Sonnet so its temperature lever keeps
    # working — Opus 4.7/4.8 reject sampling params.
    ANTHROPIC_ARBITER_MODEL: str = "claude-opus-4-8"

    # Reviewer B provider — "anthropic" (Sonnet, same as v3.1) or "gemini"
    # (Gemini Flash via its Batch API). Cross-model diversity at the reviewer
    # layer decorrelates extraction errors that two same-family reviewers share.
    # Fail-secure: "gemini" with no GEMINI_API_KEY (or the SDK absent) silently
    # falls back to the Anthropic reviewer; a degraded Gemini batch trips a
    # circuit breaker and surfaces in degraded_services.
    REVIEWER_B_PROVIDER: str = "anthropic"

    # Google Gemini (Reviewer B). Free key at https://aistudio.google.com/apikey
    GEMINI_API_KEY: str = ""
    GEMINI_FLASH_MODEL: str = "gemini-2.5-flash"  # gemini-2.5-flash-lite is cheaper

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
    QUADAS_CUTOFF: int = 13  # Papers with QUADAS ≤ this are excluded from quantitative synthesis
    QUADAS_MAX: int = 19  # Scale 0-19
    HETEROGENEITY_CRITICAL_THRESHOLD: float = 90.0  # I² ≥ → RE model + forest plot + moderator analysis
    HETEROGENEITY_HIGH_THRESHOLD: float = 75.0
    HETEROGENEITY_MODERATE_THRESHOLD: float = 50.0
    HETEROGENEITY_LOW_THRESHOLD: float = 25.0
    MIN_STUDIES_PUBLICATION_BIAS: int = 10  # Egger's test / trim-and-fill require ≥ this many studies
    # v3.1: a pooled estimate is only reported when a factor has ≥ this many
    # studies. DerSimonian–Laird τ² is unstable on 2 studies; below this we list
    # the individual effects instead of a fragile pooled point estimate.
    MIN_STUDIES_POOLING: int = 3
    LEAVE_ONE_OUT_INFLUENCE_THRESHOLD: float = 0.10  # 10% change in pooled estimate = influential paper
    EFFECT_SIZE_NEGLIGIBLE: float = 0.10  # r < this = negligible (Cohen)
    EFFECT_SIZE_WEAK: float = 0.29  # r in [0.10, 0.29] = weak
    EFFECT_SIZE_MODERATE: float = 0.49  # r in [0.30, 0.49] = moderate
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

    # ── v3.1 ────────────────────────────────────────────────────────────────
    # P1 — yield: deep extraction via forced tool-use + oversized defences.
    EXTRACTION_TOOL_USE: bool = True
    DEEP_MAX_TOKENS: int = 12000  # p99 of Long COVID extraction size + headroom
    DEEP_MAX_RETRIES: int = 2  # compression retries on stop_reason=max_tokens
    REPAIR_PASS_ENABLED: bool = True  # last-resort Haiku JSON repair

    # P2 — credibility: UMLS REST verification + retraction screening.
    UMLS_API_KEY: str = ""  # UTS key; empty → offline fallback (cui_verified=false)
    UMLS_VERIFY_ENABLED: bool = True  # only acts when UMLS_API_KEY is set
    UMLS_FUZZ_THRESHOLD: int = 70  # rapidfuzz ratio to accept a CUI ↔ verbatim match
    UMLS_CONCURRENCY: int = 5
    INCLUDE_RETRACTED: bool = False  # False → esearch excludes Retracted Publication[pt]
    RETRACTION_CHECK_ENABLED: bool = True  # Crossref retraction screen in Phase 4

    # P3 — statistics: reference implementations (PyMARE / statsmodels).
    STATS_REFERENCE_IMPL: bool = True  # False → legacy pure-numpy estimators
    STATS_DUAL_RUN: bool = False  # log old-vs-new diff to docs/V31_STATS_DIFF.md

    # P4 — coverage: OpenAlex discovery + Unpaywall full-text fallback.
    OPENALEX_ENABLED: bool = True
    OPENALEX_MAILTO: str = ""  # polite pool; falls back to NCBI_EMAIL
    MEDRXIV_LEGACY: bool = False  # True → keep client-side medRxiv scan
    UNPAYWALL_ENABLED: bool = True
    UNPAYWALL_EMAIL: str = ""  # required by Unpaywall; falls back to NCBI_EMAIL

    # P5/P6 — product: run registry + reproducibility manifest.
    ENGINE_VERSION: str = "3.1.0"
    RUN_REGISTRY_ENABLED: bool = True


settings = Settings()
