"""Single source of truth for the deep-extraction JSON schema (UPGRADE v3.1 — P1).

Used three ways:
  1. As the ``input_schema`` of a forced Anthropic tool (``submit_extraction``)
     so Reviewer A, Reviewer B and the Arbiter cannot emit malformed JSON.
  2. As a light structural validator before Supabase upsert.
  3. As the contract the integration tests assert against.

Field SEMANTICS are unchanged from v3.0 (spec rule: P1 only changes the delivery
mechanism, never the meaning of a field). The legacy COVID-named keys
(``long_covid_definition_weeks`` …) are preserved here for one version; P7 adds
the topic-neutral aliases.
"""

from __future__ import annotations

# A value that may be a real boolean or the literal sentinel "Not Reported".
_TRISTATE = {"type": ["boolean", "string"]}
_STR = {"type": "string"}
_STR_OR_NULL = {"type": ["string", "null"]}
_INT_OR_NULL = {"type": ["integer", "null"]}
_NUM_OR_NULL = {"type": ["number", "null"]}
_STR_ARRAY = {"type": "array", "items": {"type": "string"}}

EXTRACTION_INPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "study_metadata": {
            "type": "object",
            "properties": {
                "title": _STR,
                "authors": _STR_ARRAY,
                "year": _INT_OR_NULL,
                "journal": _STR_OR_NULL,
                "doi": _STR_OR_NULL,
                "design": _STR,
                "sample_size": _INT_OR_NULL,
                "sample_size_followup": _INT_OR_NULL,
                "population": _STR_OR_NULL,
                "population_description": _STR_OR_NULL,
                "follow_up_weeks": _INT_OR_NULL,
                "country": _STR_OR_NULL,
                "pandemic_era": _STR_OR_NULL,
            },
        },
        "factual_extraction": {
            "type": "object",
            "properties": {
                "long_covid_definition": _STR_OR_NULL,
                "long_covid_definition_weeks": _INT_OR_NULL,
                "definition_source": _STR_OR_NULL,
                "control_group_present": _TRISTATE,
                "control_group_description": _STR_OR_NULL,
                "primary_outcomes": _STR_ARRAY,
                "secondary_outcomes": _STR_ARRAY,
                "statistical_methods": _STR_ARRAY,
                "symptoms_prevalence": {"type": "object", "additionalProperties": True},
                "biomarker_findings": {"type": "object", "additionalProperties": True},
                "risk_factors_quantified": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "factor": _STR,
                            "metric": _STR,
                            "value": _NUM_OR_NULL,
                            "ci_low": _NUM_OR_NULL,
                            "ci_high": _NUM_OR_NULL,
                        },
                        "required": ["factor"],
                    },
                },
                "key_findings": _STR_ARRAY,
                "vaccination_adjusted": _TRISTATE,
                "vaccination_data": _STR_OR_NULL,
                "baseline_measurements": _TRISTATE,
                "funding_source": _STR_OR_NULL,
                "conflict_of_interest": _STR_OR_NULL,
            },
        },
        "methodology_appraisal": {
            "type": "object",
            "properties": {
                "nos_score": _INT_OR_NULL,
                "nos_rationale": _STR_OR_NULL,
                "grade_certainty": _STR_OR_NULL,
                "grade_rationale": _STR_OR_NULL,
                "mcid_assessed": _TRISTATE,
                "mcid_notes": _STR_OR_NULL,
                "limitations_self_reported": _STR_ARRAY,
                "limitations_inferred": _STR_ARRAY,
            },
        },
        "bias_audit": {"type": "object", "additionalProperties": True},
        "phenotype_mapping": {
            "type": "object",
            "properties": {
                "primary_mechanism": _STR_OR_NULL,
                "secondary_mechanisms": _STR_ARRAY,
                "phenotype_confidence": _NUM_OR_NULL,
            },
        },
        "calibration": {
            "type": "object",
            "properties": {
                "extraction_confidence": _NUM_OR_NULL,
                "confidence_flags": _STR_ARRAY,
                "calibrated_certainty": _STR,
                "calibrated_certainty_rationale": _STR_OR_NULL,
                "uncertainty_sources": _STR_ARRAY,
                "probabilistic_summary": _STR_OR_NULL,
            },
        },
        "provenance": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": _STR,
                    "claim": _STR,
                    "quote": _STR,
                    "section": _STR_OR_NULL,
                    "page": _INT_OR_NULL,
                    "confidence": _NUM_OR_NULL,
                },
                "required": ["field", "quote"],
            },
        },
        "quality_assessment": {"type": "object", "additionalProperties": True},
        "effect_sizes_classified": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "factor": _STR,
                    "metric_reported": _STR,
                    "value_reported": _NUM_OR_NULL,
                    "r_equivalent": _NUM_OR_NULL,
                    "conversion_note": _STR_OR_NULL,
                    "magnitude": _STR_OR_NULL,
                },
                "required": ["factor"],
            },
        },
        "moderators": {"type": "object", "additionalProperties": True},
        "estimated_from_figure": {"type": "boolean"},
        "abstract_vs_results_conflict": {"type": "boolean"},
        "critical_notes": _STR_OR_NULL,
    },
    "required": ["study_metadata", "factual_extraction", "methodology_appraisal", "provenance"],
}

EXTRACTION_TOOL: dict = {
    "name": "submit_extraction",
    "description": (
        "Submit the complete structured methodological extraction for one paper. "
        "Every field follows the contract exactly; unknown values use null or the "
        "literal string 'Not Reported' as specified, never a fabricated value."
    ),
    "input_schema": EXTRACTION_INPUT_SCHEMA,
}

# The arbiter returns the same shape plus two reconciliation fields.
ARBITER_INPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        **EXTRACTION_INPUT_SCHEMA["properties"],
        "reconciliation_triggered": {"type": "boolean"},
        "arbiter_notes": _STR_OR_NULL,
        "llm_judgment_flags": {"type": "object", "additionalProperties": True},
    },
    "required": EXTRACTION_INPUT_SCHEMA["required"],
}

ARBITER_TOOL: dict = {
    "name": "submit_reconciled_extraction",
    "description": (
        "Submit the reconciled extraction after comparing Reviewer A and Reviewer B. "
        "Set reconciliation_triggered=true when the reviewers materially disagreed and "
        "you had to adjudicate; record the adjudication in arbiter_notes."
    ),
    "input_schema": ARBITER_INPUT_SCHEMA,
}


def validate_extraction(obj: dict) -> list[str]:
    """Cheap structural check used before persistence and in tests.

    Returns a list of human-readable problems (empty list == valid). Deliberately
    permissive about field *values* — the contract allows nulls / 'Not Reported'
    almost everywhere — but strict about the presence of the load-bearing blocks.
    """
    problems: list[str] = []
    if not isinstance(obj, dict):
        return ["extraction is not an object"]
    for key in EXTRACTION_INPUT_SCHEMA["required"]:
        if key not in obj:
            problems.append(f"missing required block: {key}")
    prov = obj.get("provenance")
    if prov is not None and not isinstance(prov, list):
        problems.append("provenance must be a list")
    return problems
