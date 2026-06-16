"""WP-10 — Due-diligence overreach guard.

DEFECT: the investment brief generated a full Phase II skeleton (inclusion /
exclusion, co-primary endpoints, N=400, named drugs) from a corpus where
confidence was 38/100 and 0 findings exceeded speculative. Output specificity
was decoupled from evidence strength.

FIX: tie the level of prescriptive detail the brief may emit to the strength of
the evidence body, deterministically. Sample-size math may only render when a
[CALC] effect-size estimate exists from a qualifying pooled analysis. If
``confidence_in_recommendation < 50`` the document leads with the limitation and
"recommendation" becomes "hypothesis for expert evaluation".
"""
from __future__ import annotations

import enum


class EvidenceTier(enum.IntEnum):
    SPECULATIVE = 1
    POSSIBLE = 2
    PROBABLE = 3
    ESTABLISHED = 4

    @classmethod
    def from_label(cls, s: str) -> "EvidenceTier":
        return {
            "speculative": cls.SPECULATIVE,
            "possible": cls.POSSIBLE,
            "probable": cls.PROBABLE,
            "established": cls.ESTABLISHED,
        }[(s or "speculative").strip().lower()]


class OutputCeiling(enum.Enum):
    SPECIFIC_PROTOCOL = "specific_protocol_parameters"
    DESIGN_DIRECTIONS = "design_directions_and_ranges"
    HYPOTHESES_GAPS = "hypotheses_and_research_gaps"
    LANDSCAPE_GAPS = "landscape_and_gaps_only"


_CEILING_BY_TIER = {
    EvidenceTier.ESTABLISHED: OutputCeiling.SPECIFIC_PROTOCOL,
    EvidenceTier.PROBABLE: OutputCeiling.DESIGN_DIRECTIONS,
    EvidenceTier.POSSIBLE: OutputCeiling.HYPOTHESES_GAPS,
    EvidenceTier.SPECULATIVE: OutputCeiling.LANDSCAPE_GAPS,
}

SAMPLE_SIZE_NOT_COMPUTABLE = (
    "no defensible effect-size estimate exists in this corpus; sizing is not computable"
)


def permitted_ceiling(max_tier: str | EvidenceTier) -> OutputCeiling:
    if isinstance(max_tier, str):
        max_tier = EvidenceTier.from_label(max_tier)
    return _CEILING_BY_TIER[max_tier]


def phase_ii_skeleton_allowed(ceiling: OutputCeiling) -> bool:
    return ceiling is OutputCeiling.SPECIFIC_PROTOCOL


def point_sample_size_allowed(ceiling: OutputCeiling) -> bool:
    return ceiling is OutputCeiling.SPECIFIC_PROTOCOL


def named_drugs_allowed(ceiling: OutputCeiling) -> bool:
    # Named drug candidates require at least design-direction strength.
    return ceiling in (OutputCeiling.SPECIFIC_PROTOCOL, OutputCeiling.DESIGN_DIRECTIONS)


def sample_size_text(ceiling: OutputCeiling, has_calc_effect_size: bool) -> str:
    """Sample-size math only renders with a [CALC] effect-size estimate from a
    qualifying pooled analysis (§11.2)."""
    if point_sample_size_allowed(ceiling) and has_calc_effect_size:
        return "computable"  # caller supplies the actual [CALC] figure
    return SAMPLE_SIZE_NOT_COMPUTABLE


def confidence_coherent_framing(confidence_in_recommendation: int | None) -> dict:
    """If confidence < 50, lead with the limitation and rename 'recommendation'
    to 'hypothesis for expert evaluation' (§11.3)."""
    conf = confidence_in_recommendation or 0
    if conf < 50:
        return {
            "lead_with_limitation": True,
            "noun": "hypothesis for expert evaluation",
            "title_prefix": "Hypothesis for expert evaluation (low confidence): ",
        }
    return {"lead_with_limitation": False, "noun": "recommendation", "title_prefix": ""}


def gaps_only_template() -> dict:
    """What a speculative-max brief MAY emit: where signal clusters, what would
    need to be true to act, and what evidence is missing."""
    return {
        "where_signal_clusters": [],
        "what_would_need_to_be_true_to_act": [],
        "what_evidence_is_missing": [],
    }


def gate_due_diligence(dd: dict, max_tier: str | EvidenceTier, *, has_calc_effect_size: bool = False) -> dict:
    """Apply the evidence-gated output ceiling to a due-diligence payload.

    For a speculative-max corpus, strips the Phase II skeleton, the sample-size
    calculation, and named drug candidates, and replaces them with a gaps-only
    structure. Returns a new dict; records what was removed under
    ``output_gating``.
    """
    ceiling = permitted_ceiling(max_tier)
    gated = dict(dd)
    removed: list[str] = []

    gated["output_ceiling"] = ceiling.value

    if not phase_ii_skeleton_allowed(ceiling):
        rtp = dict(gated.get("recommended_target_phenotype") or {})
        if rtp.get("phase_ii_design_skeleton"):
            rtp["phase_ii_design_skeleton"] = None
            removed.append("phase_ii_design_skeleton")
        # sample-size basis becomes the not-computable statement
        if not (point_sample_size_allowed(ceiling) and has_calc_effect_size):
            rtp["estimated_sample_size_basis"] = SAMPLE_SIZE_NOT_COMPUTABLE
            removed.append("sample_size")
        if rtp:
            gated["recommended_target_phenotype"] = rtp

    if not named_drugs_allowed(ceiling):
        tte = gated.get("target_trial_emulation_inventory")
        if isinstance(tte, dict) and tte.get("studies"):
            for s in tte["studies"]:
                if isinstance(s, dict) and "drug_class" in s:
                    s["drug_class"] = "[withheld — evidence below threshold for naming candidates]"
            removed.append("named_drug_candidates")

    if ceiling is OutputCeiling.LANDSCAPE_GAPS:
        gated.setdefault("gaps_only", gaps_only_template())

    framing = confidence_coherent_framing(
        (gated.get("recommended_target_phenotype") or {}).get("confidence_in_recommendation")
    )
    gated["confidence_framing"] = framing
    gated["output_gating"] = {"ceiling": ceiling.value, "removed": removed, "max_tier": EvidenceTier.from_label(max_tier).name.lower() if isinstance(max_tier, str) else max_tier.name.lower()}
    return gated
