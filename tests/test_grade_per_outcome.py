"""WP-2 — GRADE per outcome, not per paper.

Acceptance (spec §3):
  * No template renders a GRADE value attached to a single paper (enforced in
    the template tests / lint).
  * GRADE appears once per outcome with an auditable downgrade/upgrade trail.
  * An evidence body composed entirely of cross-sectional studies with serious
    RoB cannot exceed very_low unless an explicit upgrade domain fires, and the
    upgrade must be justified in the rationale.
"""
from __future__ import annotations

from methodology import grade_engine as ge
from methodology.grade_engine import Certainty, EvidenceBody, build_evidence_body


def test_starting_certainty_rct_vs_observational():
    assert ge.starting_certainty(["rct", "rct", "cohort"]) == Certainty.HIGH
    assert ge.starting_certainty(["cross_sectional", "cohort"]) == Certainty.LOW
    assert ge.starting_certainty([]) == Certainty.LOW


def test_observational_serious_rob_is_very_low():
    body = build_evidence_body(
        "cognitive_function",
        contributing_papers=["PMC1", "PMC2", "PMC3"],
        study_designs=["cross_sectional", "cross_sectional", "cross_sectional"],
        downgrades={"risk_of_bias": -2},
    )
    assert body.final_grade == Certainty.VERY_LOW
    assert body.final_grade.label == "very_low"


def test_cannot_exceed_very_low_without_upgrade():
    # all cross-sectional + serious RoB, several downgrades, no upgrade
    body = build_evidence_body(
        "fatigue",
        ["PMC1", "PMC2"],
        ["cross_sectional", "cross_sectional"],
        downgrades={"risk_of_bias": -1, "imprecision": -1, "inconsistency": -1},
    )
    assert body.final_grade == Certainty.VERY_LOW


def test_explicit_upgrade_can_raise_above_very_low_and_is_justified():
    body = build_evidence_body(
        "fatigue",
        ["PMC1", "PMC2"],
        ["cross_sectional", "cross_sectional"],
        downgrades={"risk_of_bias": -1},
        upgrades={"large_effect": 1},
    )
    # start LOW(2) - 1 + 1 = LOW(2); strictly above very_low
    assert body.final_grade > Certainty.VERY_LOW
    assert body.final_grade == Certainty.LOW
    # the firing upgrade must be justified in the rationale
    assert "large_effect" in body.rationale
    assert "[CALC]" in body.rationale and "[LLM]" in body.rationale


def test_rct_body_does_not_benefit_from_upgrade_domains():
    body = build_evidence_body(
        "serious_adverse_events",
        ["PMC1", "PMC2"],
        ["rct", "rct"],
        upgrades={"large_effect": 2},  # upgrades ignored for RCT-dominated
    )
    assert body.final_grade == Certainty.HIGH  # capped, not pushed past High


def test_evidence_body_to_dict_is_outcome_indexed():
    body = build_evidence_body(
        "dyspnea", ["PMC1"], ["cohort"], downgrades={"imprecision": -1}
    )
    d = body.to_dict()
    assert d["outcome"] == "dyspnea"
    assert set(d["downgrades"]) == set(ge.DOWNGRADE_DOMAINS)
    assert set(d["upgrades"]) == set(ge.UPGRADE_DOMAINS)
    assert d["final_grade"] in {"very_low", "low", "moderate", "high"}
    # there is no "paper" key — GRADE is attached to the outcome body, not a paper
    assert "paper_id" not in d


def test_downgrade_cannot_push_below_very_low():
    body = build_evidence_body(
        "pain", ["PMC1"], ["cross_sectional"],
        downgrades={"risk_of_bias": -2, "imprecision": -2, "inconsistency": -2},
    )
    assert body.final_grade == Certainty.VERY_LOW  # floored
