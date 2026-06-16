"""WP-3 — RoB tool routing.

Acceptance (spec §4):
  * QUADAS appears for 0 papers when none are diagnostic-accuracy; PROBAST for
    the ML prediction paper; JBI prevalence checklist for prevalence/admin; NOS
    for cohorts.
  * Feeding a cross-sectional prevalence study and requesting QUADAS raises a
    ToolDesignMismatch error.
"""
from __future__ import annotations

import pytest

from methodology import rob_tools as rt
from methodology.rob_tools import StudyDesign, ToolDesignMismatch


@pytest.mark.parametrize("raw,expected", [
    ("rct", StudyDesign.RCT),
    ("prospective_cohort", StudyDesign.COHORT),
    ("retrospective_cohort", StudyDesign.COHORT),
    ("cross_sectional", StudyDesign.CROSS_SECTIONAL),
    ("ml_prediction", StudyDesign.ML_PREDICTION),
    ("administrative", StudyDesign.ADMINISTRATIVE),
    ("meta_analysis", StudyDesign.SYSTEMATIC_REVIEW),
    ("diagnostic_accuracy", StudyDesign.DIAGNOSTIC_ACCURACY),
    ("something weird", StudyDesign.OTHER),
])
def test_classify_design(raw, expected):
    assert rt.classify_design(raw) == expected


@pytest.mark.parametrize("design,instrument", [
    ("rct", "Cochrane RoB 2"),
    ("prospective_cohort", "Newcastle-Ottawa (NOS)"),
    ("case_control", "Newcastle-Ottawa (NOS)"),
    ("non_randomised_intervention", "ROBINS-I"),
    ("diagnostic_accuracy", "QUADAS-2"),
    ("cross_sectional", "JBI Prevalence checklist"),
    ("prevalence", "JBI Prevalence checklist"),
    ("administrative", "JBI Prevalence checklist"),
    ("ml_prediction", "PROBAST"),
    ("systematic_review", "AMSTAR-2 / ROBIS"),
])
def test_instrument_routing(design, instrument):
    assert rt.select_rob_instrument(design) == instrument


def test_quadas_only_for_diagnostic_accuracy():
    assert rt.quadas_available("diagnostic_accuracy")
    assert not rt.quadas_available("cross_sectional")
    assert not rt.quadas_available("cohort")


def test_requesting_quadas_on_prevalence_raises():
    with pytest.raises(ToolDesignMismatch):
        rt.assert_quadas_applicable("cross_sectional")
    with pytest.raises(ToolDesignMismatch):
        rt.run_quadas("prevalence")


def test_quadas_runs_for_diagnostic_accuracy():
    out = rt.run_quadas("diagnostic_accuracy")
    assert out["applicable"] is True


def test_long_covid_corpus_uses_zero_quadas():
    """The Long COVID corpus has no diagnostic-accuracy studies → QUADAS for 0
    papers; ML paper → PROBAST; prevalence/admin → JBI; cohort → NOS."""
    corpus = [
        "prospective_cohort", "cross_sectional", "ml_prediction",
        "administrative", "retrospective_cohort", "case_control",
    ]
    instruments = [rt.select_rob_instrument(d) for d in corpus]
    assert instruments.count("QUADAS-2") == 0
    assert "PROBAST" in instruments
    assert "JBI Prevalence checklist" in instruments
    assert "Newcastle-Ottawa (NOS)" in instruments


def test_rob_assignment_separates_bias_audit_layer():
    a = rt.rob_assignment("cross_sectional")
    assert a["primary_instrument"] == "JBI Prevalence checklist"
    assert "descriptive" in a["bias_audit_layer"].lower()
    assert a["quadas_applicable"] is False
