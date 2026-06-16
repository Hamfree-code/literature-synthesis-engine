"""Methodological hardening engines — UPGRADE v3.2.

This package contains the deterministic engines introduced by the v3.2
"Methodological Hardening & Provenance Integrity" upgrade. Each module is a
self-contained work package (WP) with a deterministic, unit-testable core so
that the pipeline never presents a number more precise than its provenance
supports.

Global rule (v3.2): every quantitative cell in a report must resolve to one of
three provenance tags — ``[LLM]`` (model inference), ``[CALC]`` (deterministic
computation on extracted data), ``[CONSENSUS]`` (arbiter-reconciled) — and the
tag must be verifiable, not decorative.

Work packages:
    emcu                 — WP-0  product identity (EMCU) + self-reference lint
    extraction_schema    — WP-1  Pydantic extraction contract + repair
    flow_record          — WP-1  PRISMA-style flow + conservation invariant
    provenance_registry  — WP-9  canonical IDs, shared numbering, bleed/quote-drift
    outcome_dictionary   — WP-5/6 controlled outcome vocabulary + normalisation
    rob_tools            — WP-3  risk-of-bias instrument routing by design
    case_definition      — WP-7  case-definition canonicalisation + gating
    grade_engine         — WP-2  outcome-level evidence bodies + GRADE arithmetic
    synthesis_gating     — WP-6  statistical-method preconditions
    prevalence           — WP-4  mention-frequency vs patient-prevalence split
    reconciliation       — WP-9  narrative vs calibrated-certainty gate
    output_ceiling       — WP-10 evidence-gated output specificity
"""

PRODUCT_IDENTITY = "Evidence Mapping with Calibrated Uncertainty (EMCU)"
UPGRADE_VERSION = "3.2"

# The three — and only three — provenance tags a quantitative cell may carry.
PROVENANCE_TAGS = ("[LLM]", "[CALC]", "[CONSENSUS]")
