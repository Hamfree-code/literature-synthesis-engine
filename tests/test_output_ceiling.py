"""WP-10 — speculative-max corpus blocks Phase II skeleton & sample size.

Acceptance (spec §11):
  * The Long COVID due-diligence brief, re-run, contains no Phase II skeleton,
    no N=400, no named drug list.
  * A corpus whose max tier is speculative causes the Phase II generator to emit
    the gaps-only template and raises no protocol parameters.
"""
from __future__ import annotations

from methodology import output_ceiling as oc
from methodology.output_ceiling import EvidenceTier, OutputCeiling


def test_ceiling_by_tier():
    assert oc.permitted_ceiling("speculative") is OutputCeiling.LANDSCAPE_GAPS
    assert oc.permitted_ceiling("possible") is OutputCeiling.HYPOTHESES_GAPS
    assert oc.permitted_ceiling("probable") is OutputCeiling.DESIGN_DIRECTIONS
    assert oc.permitted_ceiling("established") is OutputCeiling.SPECIFIC_PROTOCOL


def test_speculative_blocks_phase_ii_and_sample_size_and_drugs():
    assert not oc.phase_ii_skeleton_allowed(OutputCeiling.LANDSCAPE_GAPS)
    assert not oc.point_sample_size_allowed(OutputCeiling.LANDSCAPE_GAPS)
    assert not oc.named_drugs_allowed(OutputCeiling.LANDSCAPE_GAPS)


def test_sample_size_not_computable_without_calc_estimate():
    txt = oc.sample_size_text(OutputCeiling.SPECIFIC_PROTOCOL, has_calc_effect_size=False)
    assert txt == oc.SAMPLE_SIZE_NOT_COMPUTABLE


def test_gate_due_diligence_strips_everything_when_speculative():
    dd = {
        "recommended_target_phenotype": {
            "phenotype": "autonomic",
            "confidence_in_recommendation": 38,
            "phase_ii_design_skeleton": {
                "inclusion_criteria": ["x"],
                "primary_endpoint": "fatigue",
                "estimated_sample_size_basis": "N=400 powered at 80%",
            },
            "estimated_sample_size_basis": "N=400",
        },
        "target_trial_emulation_inventory": {
            "studies": [{"doi": "PMC1", "drug_class": "metformin"}],
        },
    }
    gated = oc.gate_due_diligence(dd, "speculative")
    rtp = gated["recommended_target_phenotype"]
    # Phase II skeleton removed
    assert rtp["phase_ii_design_skeleton"] is None
    # sample size replaced with the not-computable statement (no N=400)
    assert rtp["estimated_sample_size_basis"] == oc.SAMPLE_SIZE_NOT_COMPUTABLE
    # named drug candidates withheld
    assert "metformin" not in str(gated["target_trial_emulation_inventory"])
    # gaps-only structure emitted
    assert "gaps_only" in gated
    assert gated["output_ceiling"] == OutputCeiling.LANDSCAPE_GAPS.value
    assert set(gated["output_gating"]["removed"]) >= {"phase_ii_design_skeleton", "sample_size", "named_drug_candidates"}


def test_low_confidence_reframes_as_hypothesis():
    framing = oc.confidence_coherent_framing(38)
    assert framing["lead_with_limitation"] is True
    assert framing["noun"] == "hypothesis for expert evaluation"


def test_high_confidence_keeps_recommendation_noun():
    framing = oc.confidence_coherent_framing(80)
    assert framing["noun"] == "recommendation"


def test_established_corpus_permits_protocol_and_sample_size():
    ceiling = oc.permitted_ceiling("established")
    assert oc.phase_ii_skeleton_allowed(ceiling)
    assert oc.sample_size_text(ceiling, has_calc_effect_size=True) == "computable"
