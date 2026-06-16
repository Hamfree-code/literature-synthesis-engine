"""WP-2 — GRADE applied per outcome, not per paper.

DEFECT: GRADE was attached to individual papers. GRADE rates the certainty of a
*body of evidence for a specific outcome* — per-paper GRADE is a category error.
Compounding it: observational evidence *starts* at Low by design, so "all papers
Low/Very Low" is largely tautological.

FIX: an outcome-level ``EvidenceBody`` entity carries the GRADE assessment. The
starting point is deterministic ([CALC]): High if RCT-dominated, Low if
observational-dominated. Each of the five downgrade and three upgrade domains is
an [LLM] judgement (0 / -1 / -2 and 0 / +1 / +2), but the arithmetic that
combines them into the final level is [CALC]. Final certainty is capped at what
the design + domain math produce. GRADE is never reported per paper.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field

from methodology.rob_tools import StudyDesign, classify_design


class Certainty(enum.IntEnum):
    VERY_LOW = 1
    LOW = 2
    MODERATE = 3
    HIGH = 4

    @property
    def label(self) -> str:
        return {1: "very_low", 2: "low", 3: "moderate", 4: "high"}[int(self)]

    @classmethod
    def from_label(cls, s: str) -> "Certainty":
        return {"very_low": cls.VERY_LOW, "low": cls.LOW, "moderate": cls.MODERATE, "high": cls.HIGH}[s.lower()]


DOWNGRADE_DOMAINS = ("risk_of_bias", "inconsistency", "indirectness", "imprecision", "publication_bias")
UPGRADE_DOMAINS = ("large_effect", "dose_response", "plausible_confounding")

# Designs that justify a HIGH starting point.
_RCT_LIKE = {StudyDesign.RCT}


def is_rct_dominated(study_designs: list[str]) -> bool:
    """RCT-dominated iff RCTs are a strict majority of contributing designs."""
    if not study_designs:
        return False
    rct = sum(1 for d in study_designs if classify_design(d) in _RCT_LIKE)
    return rct * 2 > len(study_designs)


def starting_certainty(study_designs: list[str]) -> Certainty:
    """[CALC] High if RCT-dominated, Low if observational-dominated."""
    return Certainty.HIGH if is_rct_dominated(study_designs) else Certainty.LOW


def _clean_domain(value, *, lo: int, hi: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 0
    return max(lo, min(hi, v))


@dataclass
class EvidenceBody:
    """An outcome-level body of evidence (the entity GRADE attaches to)."""

    outcome: str
    comparison: str = "Long COVID vs control"
    contributing_papers: list[str] = field(default_factory=list)
    study_designs: list[str] = field(default_factory=list)
    downgrades: dict[str, int] = field(default_factory=dict)   # 0/-1/-2 each
    upgrades: dict[str, int] = field(default_factory=dict)     # 0/+1/+2 each
    starting_certainty_: Certainty | None = None
    final_grade_: Certainty | None = None
    rationale: str = ""

    @property
    def starting_certainty(self) -> Certainty:
        return self.starting_certainty_ or starting_certainty(self.study_designs)

    @property
    def final_grade(self) -> Certainty:
        return self.final_grade_ if self.final_grade_ is not None else compute_final_grade(self)

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome,
            "comparison": self.comparison,
            "contributing_papers": list(self.contributing_papers),
            "study_designs": list(self.study_designs),
            "starting_certainty": self.starting_certainty.label,
            "downgrades": {k: self.downgrades.get(k, 0) for k in DOWNGRADE_DOMAINS},
            "upgrades": {k: self.upgrades.get(k, 0) for k in UPGRADE_DOMAINS},
            "final_grade": self.final_grade.label,
            "rationale": self.rationale or build_rationale(self),
        }


def total_downgrade(body: EvidenceBody) -> int:
    return sum(_clean_domain(body.downgrades.get(d, 0), lo=-2, hi=0) for d in DOWNGRADE_DOMAINS)


def total_upgrade(body: EvidenceBody) -> int:
    return sum(_clean_domain(body.upgrades.get(d, 0), lo=0, hi=2) for d in UPGRADE_DOMAINS)


def any_upgrade_fired(body: EvidenceBody) -> bool:
    return total_upgrade(body) > 0


def compute_final_grade(body: EvidenceBody) -> Certainty:
    """[CALC] Combine starting certainty + downgrade/upgrade domains, clamped to
    the [very_low, high] band. Upgrades only apply to observational bodies
    (GRADE convention) — an RCT-dominated body cannot be upgraded above High and
    does not gain from upgrade domains."""
    start = int(body.starting_certainty)
    down = total_downgrade(body)
    up = total_upgrade(body) if body.starting_certainty == Certainty.LOW else 0
    level = start + down + up
    level = max(int(Certainty.VERY_LOW), min(int(Certainty.HIGH), level))
    return Certainty(level)


def build_rationale(body: EvidenceBody) -> str:
    """Auto-generate an auditable [CALC]+[LLM] rationale enumerating the firing
    domains. Guarantees any fired upgrade is justified in the rationale (§3.2)."""
    parts = [
        f"Starting certainty {body.starting_certainty.label} "
        f"({'RCT-dominated' if body.starting_certainty == Certainty.HIGH else 'observational-dominated'}) [CALC]."
    ]
    fired_down = [(d, body.downgrades.get(d, 0)) for d in DOWNGRADE_DOMAINS if body.downgrades.get(d, 0)]
    fired_up = [(u, body.upgrades.get(u, 0)) for u in UPGRADE_DOMAINS if body.upgrades.get(u, 0)]
    if fired_down:
        parts.append("Downgraded for " + ", ".join(f"{d} ({v})" for d, v in fired_down) + " [LLM].")
    if fired_up and body.starting_certainty == Certainty.LOW:
        parts.append("Upgraded for " + ", ".join(f"{u} (+{v})" for u, v in fired_up) + " [LLM].")
    parts.append(f"Final certainty {compute_final_grade(body).label} [CALC].")
    return " ".join(parts)


def build_evidence_body(
    outcome: str,
    contributing_papers: list[str],
    study_designs: list[str],
    *,
    comparison: str = "Long COVID vs control",
    downgrades: dict[str, int] | None = None,
    upgrades: dict[str, int] | None = None,
) -> EvidenceBody:
    """Construct a fully-resolved evidence body with deterministic final grade
    and an auditable rationale."""
    body = EvidenceBody(
        outcome=outcome,
        comparison=comparison,
        contributing_papers=list(contributing_papers),
        study_designs=list(study_designs),
        downgrades=dict(downgrades or {}),
        upgrades=dict(upgrades or {}),
    )
    body.starting_certainty_ = starting_certainty(study_designs)
    body.final_grade_ = compute_final_grade(body)
    body.rationale = build_rationale(body)
    return body
