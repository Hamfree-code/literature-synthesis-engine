"""Tests for the v3.2 integration glue (methodology.integration).

These prove the transforms from pipeline artifacts → engine inputs, so the
phase wiring carries no untested logic.
"""
from __future__ import annotations

from methodology import integration as it
from methodology.extraction_schema import FailureReason


def _ext(pid, design, symptoms, bias_flags=(), n=100):
    return {
        "paper_id": pid,
        "study_metadata": {"design": design, "sample_size": n},
        "factual_extraction": {
            "symptoms_prevalence": {s: "0.3" for s in symptoms},
            "long_covid_definition_weeks": 12,
            "definition_source": "WHO_2021",
        },
        "bias_audit": {f: True for f in bias_flags},
        "calibration": {"calibrated_certainty": "speculative"},
        "methodology_appraisal": {},
    }


def test_classify_failure_types():
    assert it.classify_extraction_failure("timeout", None) is FailureReason.TIMEOUT
    assert it.classify_extraction_failure("errored", None) is FailureReason.API_ERROR
    assert it.classify_extraction_failure("succeeded", '{"a":1') is FailureReason.TRUNCATION


def test_normalisation_review_collapses_and_logs():
    deep = [
        _ext("PMC1", "cross_sectional", ["brain fog", "fatigue"]),
        _ext("PMC2", "cohort", ["cognitive impairment", "left toe tingling"]),
    ]
    review = it.normalisation_review(deep)
    assert review["by_canonical"]["cognitive_function"]  # collapsed
    assert "left toe tingling" in review["normalisation_review"]
    assert review["dictionary_version"]


def test_rob_assignments_are_design_matched():
    deep = [_ext("PMC1", "ml_prediction", ["fatigue"]), _ext("PMC2", "cross_sectional", ["fatigue"])]
    a = it.rob_assignments(deep)
    assert a["PMC1"]["primary_instrument"] == "PROBAST"
    assert a["PMC2"]["primary_instrument"] == "JBI Prevalence checklist"
    assert a["PMC2"]["quadas_applicable"] is False


def test_build_evidence_bodies_per_outcome_with_serious_rob():
    # three cross-sectional papers all reporting cognitive labels + many biases
    deep = [
        _ext("PMC1", "cross_sectional", ["brain fog"], bias_flags=("surveillance_bias", "self_report_bias", "selection_bias")),
        _ext("PMC2", "cross_sectional", ["cognitive dysfunction"], bias_flags=("self_report_bias", "selection_bias", "baseline_absence")),
        _ext("PMC3", "cross_sectional", ["cognitive impairment"], bias_flags=("self_report_bias",)),
    ]
    bodies = it.build_evidence_bodies(deep)
    cog = [b for b in bodies if b["outcome"] == "cognitive_function"]
    assert len(cog) == 1                          # three labels → ONE outcome body
    assert len(cog[0]["contributing_papers"]) == 3
    assert cog[0]["final_grade"] == "very_low"    # observational + serious RoB
    assert "paper_id" not in cog[0]               # GRADE is per-outcome, not per-paper


def test_max_evidence_tier_speculative_when_all_speculative():
    cc = {"fatigue": {"consensus_certainty": "speculative"}, "pain": {"consensus_certainty": "speculative"}}
    assert it.max_evidence_tier(cc) == "speculative"


def test_max_evidence_tier_picks_strongest():
    cc = {"fatigue": {"consensus_certainty": "speculative"}, "pain": {"consensus_certainty": "possible"}}
    assert it.max_evidence_tier(cc) == "possible"
