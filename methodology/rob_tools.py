"""WP-3 — Risk-of-bias tooling matched to study design.

DEFECT: a single generic instrument ("QUADAS-adapted") was applied to a corpus
of prevalence / cohort / ML studies. QUADAS-2 is for *diagnostic test accuracy*
studies — almost none of the corpus qualifies. NOS was also applied, creating a
mismatched, partly redundant battery.

FIX: a design classifier routes each paper to exactly ONE design-matched
primary instrument. QUADAS may only be invoked when the design is
diagnostic-accuracy; otherwise it raises ``ToolDesignMismatch``. The 8-axis
bias audit is retained as a *cross-cutting descriptive layer*, explicitly
separate from the formal RoB instrument.
"""
from __future__ import annotations

import enum


class StudyDesign(str, enum.Enum):
    RCT = "rct"
    COHORT = "cohort"
    CASE_CONTROL = "case_control"
    NON_RANDOMISED_INTERVENTION = "non_randomised_intervention"
    DIAGNOSTIC_ACCURACY = "diagnostic_accuracy"
    CROSS_SECTIONAL = "cross_sectional"
    PREVALENCE = "prevalence"
    ADMINISTRATIVE = "administrative"
    ML_PREDICTION = "ml_prediction"
    SYSTEMATIC_REVIEW = "systematic_review"
    CASE_REPORT = "case_report"
    OTHER = "other"


# Design → primary RoB instrument (spec §4.1 table).
INSTRUMENT_BY_DESIGN: dict[StudyDesign, str] = {
    StudyDesign.RCT: "Cochrane RoB 2",
    StudyDesign.COHORT: "Newcastle-Ottawa (NOS)",
    StudyDesign.CASE_CONTROL: "Newcastle-Ottawa (NOS)",
    StudyDesign.NON_RANDOMISED_INTERVENTION: "ROBINS-I",
    StudyDesign.DIAGNOSTIC_ACCURACY: "QUADAS-2",
    StudyDesign.CROSS_SECTIONAL: "JBI Prevalence checklist",
    StudyDesign.PREVALENCE: "JBI Prevalence checklist",
    StudyDesign.ADMINISTRATIVE: "JBI Prevalence checklist",
    StudyDesign.ML_PREDICTION: "PROBAST",
    StudyDesign.SYSTEMATIC_REVIEW: "AMSTAR-2 / ROBIS",
    StudyDesign.CASE_REPORT: "Narrative appraisal (no validated RoB instrument)",
    StudyDesign.OTHER: "Narrative appraisal (no validated RoB instrument)",
}

# Raw design strings (from triage + deep extraction prompts + spec additions)
# mapped to the canonical StudyDesign.
_DESIGN_ALIASES: dict[str, StudyDesign] = {
    "rct": StudyDesign.RCT,
    "randomized_controlled_trial": StudyDesign.RCT,
    "randomised_controlled_trial": StudyDesign.RCT,
    "cohort": StudyDesign.COHORT,
    "prospective_cohort": StudyDesign.COHORT,
    "retrospective_cohort": StudyDesign.COHORT,
    "case_control": StudyDesign.CASE_CONTROL,
    "non_randomised_intervention": StudyDesign.NON_RANDOMISED_INTERVENTION,
    "non_randomized_intervention": StudyDesign.NON_RANDOMISED_INTERVENTION,
    "quasi_experimental": StudyDesign.NON_RANDOMISED_INTERVENTION,
    "diagnostic_accuracy": StudyDesign.DIAGNOSTIC_ACCURACY,
    "diagnostic_test_accuracy": StudyDesign.DIAGNOSTIC_ACCURACY,
    "cross_sectional": StudyDesign.CROSS_SECTIONAL,
    "prevalence": StudyDesign.PREVALENCE,
    "administrative": StudyDesign.ADMINISTRATIVE,
    "registry": StudyDesign.ADMINISTRATIVE,
    "claims": StudyDesign.ADMINISTRATIVE,
    "ml_prediction": StudyDesign.ML_PREDICTION,
    "machine_learning": StudyDesign.ML_PREDICTION,
    "prediction_model": StudyDesign.ML_PREDICTION,
    "review": StudyDesign.SYSTEMATIC_REVIEW,
    "systematic_review": StudyDesign.SYSTEMATIC_REVIEW,
    "meta_analysis": StudyDesign.SYSTEMATIC_REVIEW,
    "case_report": StudyDesign.CASE_REPORT,
    "case_series": StudyDesign.CASE_REPORT,
    "qualitative": StudyDesign.OTHER,
    "other": StudyDesign.OTHER,
}


class ToolDesignMismatch(ValueError):
    """Raised when a RoB instrument is invoked on an inappropriate design."""


def classify_design(raw: str | None) -> StudyDesign:
    """Map a raw design string to the canonical :class:`StudyDesign`."""
    if not raw:
        return StudyDesign.OTHER
    key = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
    if key in _DESIGN_ALIASES:
        return _DESIGN_ALIASES[key]
    # substring fallback for noisy free-text designs
    for alias, design in _DESIGN_ALIASES.items():
        if alias in key:
            return design
    return StudyDesign.OTHER


def select_rob_instrument(design: StudyDesign | str) -> str:
    """Return the single design-matched primary RoB instrument."""
    if isinstance(design, str):
        design = classify_design(design)
    return INSTRUMENT_BY_DESIGN.get(design, INSTRUMENT_BY_DESIGN[StudyDesign.OTHER])


def quadas_available(design: StudyDesign | str) -> bool:
    """QUADAS-2 is available only for diagnostic-accuracy studies (§4.1)."""
    if isinstance(design, str):
        design = classify_design(design)
    return design is StudyDesign.DIAGNOSTIC_ACCURACY


def assert_quadas_applicable(design: StudyDesign | str) -> None:
    """Raise :class:`ToolDesignMismatch` unless the design is diagnostic-accuracy.

    Acceptance §4: feeding a cross-sectional prevalence study and requesting
    QUADAS raises ToolDesignMismatch.
    """
    if isinstance(design, str):
        design = classify_design(design)
    if not quadas_available(design):
        raise ToolDesignMismatch(
            f"QUADAS-2 is only valid for diagnostic-accuracy studies; "
            f"design '{design.value}' must use {select_rob_instrument(design)!r}."
        )


def run_quadas(design: StudyDesign | str, scorer=None):
    """Invoke QUADAS, gated by design. Raises ToolDesignMismatch for any
    non-diagnostic-accuracy design before any scoring happens."""
    assert_quadas_applicable(design)
    return scorer() if scorer else {"instrument": "QUADAS-2", "applicable": True}


def rob_assignment(design: StudyDesign | str) -> dict:
    """Full RoB assignment for a paper: exactly one primary instrument, an
    optional secondary descriptor (NOS for observational), and an explicit note
    that the 8-axis bias audit is a separate descriptive layer (§4.2)."""
    if isinstance(design, str):
        design = classify_design(design)
    primary = select_rob_instrument(design)
    secondary = None
    if design in (StudyDesign.CROSS_SECTIONAL, StudyDesign.PREVALENCE, StudyDesign.ADMINISTRATIVE):
        secondary = "Newcastle-Ottawa (NOS) — descriptive only"
    return {
        "design": design.value,
        "primary_instrument": primary,
        "secondary_descriptor": secondary,
        "bias_audit_layer": "8-axis bias audit (cross-cutting descriptive layer, not the formal RoB judgement)",
        "quadas_applicable": quadas_available(design),
    }
