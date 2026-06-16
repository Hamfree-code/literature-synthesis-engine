"""v3.2 integration glue — pure transforms from pipeline artifacts to engine
inputs. Kept here (and unit-tested) so the phase modules only need thin calls
and no untested logic lives in the pipeline.

Functions:
  * classify_extraction_failure   — typed FailureReason from a batch result
  * validate_or_fail              — parse+repair+validate a raw response
  * normalisation_review          — canonicalise every reported outcome label
  * rob_assignments               — design-matched RoB instrument per paper
  * build_evidence_bodies         — outcome-level GRADE bodies from deep data
  * max_evidence_tier             — strongest calibrated tier (for WP-10 ceiling)
"""
from __future__ import annotations

from collections import defaultdict

from methodology import grade_engine as ge
from methodology import synthesis_gating as sg
from methodology.case_definition import canonicalize_case_definition
from methodology.extraction_schema import FailureReason, parse_or_repair, validate_extraction
from methodology.outcome_dictionary import load_dictionary, normalize_outcomes
from methodology.rob_tools import StudyDesign, classify_design, rob_assignment

# Bias-audit boolean flags emitted by the extraction prompt (WP-3 cross-cutting
# descriptive layer) — used to derive a deterministic risk_of_bias downgrade.
_BIAS_FLAGS = (
    "surveillance_bias", "baseline_absence", "self_report_bias",
    "variant_vaccine_confounding", "healthy_vaccinee_bias",
    "loss_to_followup_bias", "selection_bias", "circular_case_definition",
)


def classify_extraction_failure(result_type: str, raw_text: str | None) -> FailureReason:
    """Map a batch result into a typed failure reason (WP-1.2)."""
    if result_type == "timeout":
        return FailureReason.TIMEOUT
    if result_type != "succeeded":
        return FailureReason.API_ERROR
    from methodology.extraction_schema import classify_parse_failure
    return classify_parse_failure(raw_text or "")


def validate_or_fail(raw_text: str, repair_fn=None) -> tuple[dict | None, str | None]:
    """Parse → repair → validate a raw Sonnet response.

    Returns ``(obj, None)`` on success or ``(None, failure_reason_value)``.
    """
    obj, reason = parse_or_repair(raw_text, repair_fn=repair_fn)
    return obj, (reason.value if reason else None)


def _reported_outcome_labels(extraction: dict) -> list[str]:
    """Every outcome/symptom label a deep extraction reports."""
    fx = extraction.get("factual_extraction") or {}
    labels: list[str] = []
    labels.extend((fx.get("symptoms_prevalence") or {}).keys())
    labels.extend(fx.get("primary_outcomes") or [])
    labels.extend(fx.get("secondary_outcomes") or [])
    return [str(x) for x in labels if x]


def normalisation_review(deep_extractions: list[dict], condition: str = "long_covid") -> dict:
    """Normalise every reported label; return the mapping + the review log of
    unmapped labels (never discarded)."""
    dictionary = load_dictionary(condition)
    all_labels: list[str] = []
    for ext in deep_extractions:
        all_labels.extend(_reported_outcome_labels(ext))
    res = normalize_outcomes(all_labels, dictionary)
    return {
        "dictionary_version": dictionary.version,
        "condition": dictionary.condition,
        "mapping": res.mapping,
        "by_canonical": res.by_canonical,
        "normalisation_review": res.unmapped,
    }


def rob_assignments(deep_extractions: list[dict]) -> dict[str, dict]:
    """One design-matched primary RoB instrument per paper (WP-3)."""
    out: dict[str, dict] = {}
    for ext in deep_extractions:
        pid = ext.get("paper_id")
        if not pid:
            continue
        design = (ext.get("study_metadata") or {}).get("design")
        out[pid] = rob_assignment(design or "other")
    return out


def _risk_of_bias_downgrade(extractions: list[dict]) -> int:
    """Derive a deterministic risk_of_bias downgrade from the [LLM]-extracted
    bias-audit flags across the body. The chain is bias_audit [LLM] → mapping
    [CALC]; documented in the GRADE rationale."""
    max_flags = 0
    for ext in extractions:
        ba = ext.get("bias_audit") or {}
        n = sum(1 for k in _BIAS_FLAGS if ba.get(k) is True)
        max_flags = max(max_flags, n)
    if max_flags >= 3:
        return -2
    if max_flags >= 1:
        return -1
    return 0


def _imprecision_downgrade(extractions: list[dict]) -> int:
    """Single contributing study or tiny total N → imprecision downgrade."""
    if len(extractions) < 2:
        return -1
    total_n = 0
    for ext in extractions:
        n = (ext.get("study_metadata") or {}).get("sample_size") or 0
        try:
            total_n += int(n)
        except (TypeError, ValueError):
            pass
    return -1 if total_n and total_n < 400 else 0


def build_evidence_bodies(
    deep_extractions: list[dict],
    condition: str = "long_covid",
) -> list[dict]:
    """Build outcome-level GRADE evidence bodies (WP-2).

    Papers are grouped by canonical outcome (WP-5/6). Downgrade domains are
    derived deterministically from the extracted bias-audit flags and study
    counts; the per-domain judgement provenance is [LLM] (the flags) combined by
    [CALC] arithmetic into the final grade.
    """
    dictionary = load_dictionary(condition)
    by_outcome: dict[str, list[dict]] = defaultdict(list)
    for ext in deep_extractions:
        seen: set[str] = set()
        for label in _reported_outcome_labels(ext):
            canonical = dictionary.normalize(label)
            if canonical and canonical not in seen:
                by_outcome[canonical].append(ext)
                seen.add(canonical)

    bodies: list[dict] = []
    for outcome, exts in sorted(by_outcome.items()):
        designs = [(e.get("study_metadata") or {}).get("design") or "other" for e in exts]
        papers = [e.get("paper_id") for e in exts if e.get("paper_id")]
        downgrades = {
            "risk_of_bias": _risk_of_bias_downgrade(exts),
            "imprecision": _imprecision_downgrade(exts),
        }
        body = ge.build_evidence_body(
            outcome=outcome,
            contributing_papers=papers,
            study_designs=designs,
            downgrades=downgrades,
        )
        bodies.append(body.to_dict())
    return bodies


def _group_by_outcome(deep_extractions: list[dict], condition: str) -> dict[str, list[dict]]:
    dictionary = load_dictionary(condition)
    by_outcome: dict[str, list[dict]] = defaultdict(list)
    for ext in deep_extractions:
        seen: set[str] = set()
        for label in _reported_outcome_labels(ext):
            canonical = dictionary.normalize(label)
            if canonical and canonical not in seen:
                by_outcome[canonical].append(ext)
                seen.add(canonical)
    return by_outcome


def rct_count(deep_extractions: list[dict]) -> int:
    """Number of RCTs in the corpus (WP-§1.3: methods must state the RCT count;
    zero RCTs → intervention-efficacy questions are out of scope)."""
    return sum(
        1 for e in deep_extractions
        if classify_design((e.get("study_metadata") or {}).get("design")) is StudyDesign.RCT
    )


def gated_synthesis_decisions(deep_extractions: list[dict], condition: str = "long_covid") -> dict:
    """Per-outcome quantitative-synthesis gate (WP-6): for each canonical
    outcome, record whether pooling / Egger preconditions hold and the honest
    "no pooling performed because …" note when they do not. Symptom outcomes are
    proportions; the gate therefore depends on case-definition commensurability."""
    by_outcome = _group_by_outcome(deep_extractions, condition)
    decisions: list[dict] = []
    any_qualified = False
    for outcome, exts in sorted(by_outcome.items()):
        case_defs = [canonicalize_case_definition(e) for e in exts]
        outcomes = [outcome] * len(exts)
        metrics = ["prevalence"] * len(exts)
        pool = sg.random_effects_pool(outcomes, metrics, case_defs)
        egger = sg.egger_test(len(exts))
        decisions.append({
            "outcome": outcome,
            "n_studies": len(exts),
            "pooling_performed": pool.performed,
            "pooling_note": pool.reason,
            "pooling_provenance_tag": pool.provenance_tag,
            "egger_performed": egger.performed,
            "egger_note": egger.reason,
        })
        any_qualified = any_qualified or pool.performed
    return {
        "decisions": decisions,
        "any_outcome_qualified": any_qualified,
        "siciliano_claim": sg.siciliano_claim(any_qualified),
    }


def max_evidence_tier(calibrated_consensus: dict | None, evidence_bodies: list[dict] | None = None) -> str:
    """The strongest tier present in the corpus, for the WP-10 output ceiling.

    Prefers the calibrated_consensus tally; falls back to evidence-body grades
    mapped onto the calibrated vocabulary (high→established … very_low→speculative).
    """
    order = ["speculative", "possible", "probable", "established"]
    best = "speculative"
    for data in (calibrated_consensus or {}).values():
        tier = (data or {}).get("consensus_certainty")
        if tier in order and order.index(tier) > order.index(best):
            best = tier
    grade_to_tier = {"very_low": "speculative", "low": "possible", "moderate": "probable", "high": "established"}
    for b in evidence_bodies or []:
        tier = grade_to_tier.get(b.get("final_grade", "very_low"), "speculative")
        if order.index(tier) > order.index(best):
            best = tier
    return best
