"""WP-1 — extraction integrity: oversized payload survives or is logged;
conservation invariant; substitution recorded.

Acceptance (spec §2):
  * A deliberately oversized paper extracts successfully or, if it fails,
    appears in the flow record with failure_reason populated — never vanishes.
  * n_failed is explicitly reported.
  * Unit test asserts n_intended == n_extracted + n_failed_without_substitution.
"""
from __future__ import annotations

import json

import pytest

from methodology import extraction_schema as es
from methodology import flow_record as fr
from methodology.extraction_schema import FailureReason


def _good_extraction() -> dict:
    return {
        "study_metadata": {"design": "cross_sectional", "sample_size": 120},
        "factual_extraction": {"key_findings": ["x"]},
        "methodology_appraisal": {"grade_certainty": "Low"},
        "calibration": {"calibrated_certainty": "possible"},
        "provenance": [{"field": "x", "quote": "y"}],
    }


# ---- schema validation -----------------------------------------------------

def test_valid_extraction_passes():
    outcome = es.validate_extraction(_good_extraction())
    assert outcome.ok
    assert outcome.reason is None


def test_truncated_object_classified_as_truncation():
    raw = json.dumps(_good_extraction())[: -40]  # chop the tail off
    assert es.looks_truncated(raw)
    assert es.classify_parse_failure(raw) is FailureReason.TRUNCATION


def test_missing_calibration_block_is_truncation():
    obj = _good_extraction()
    obj.pop("calibration")
    outcome = es.validate_extraction(obj)
    assert not outcome.ok
    assert outcome.reason is FailureReason.TRUNCATION
    assert any("calibration" in e for e in outcome.errors)


def test_valid_json_wrong_shape_is_schema_violation():
    raw = json.dumps({"hello": "world"})
    assert es.classify_parse_failure(raw) is FailureReason.SCHEMA_VIOLATION


# ---- repair contract -------------------------------------------------------

def test_parse_or_repair_succeeds_without_repair():
    obj, reason = es.parse_or_repair(json.dumps(_good_extraction()))
    assert reason is None
    assert obj["study_metadata"]["design"] == "cross_sectional"


def test_parse_or_repair_uses_injected_repair_fn():
    broken = json.dumps(_good_extraction())[:-30]  # truncated

    def fake_repair(_raw: str) -> str:
        # a cheap model "returns only valid JSON conforming to the schema"
        return json.dumps(_good_extraction())

    obj, reason = es.parse_or_repair(broken, repair_fn=fake_repair)
    assert reason is None
    assert obj is not None


def test_parse_or_repair_rejects_invalid_repair():
    broken = json.dumps(_good_extraction())[:-30]

    def bad_repair(_raw: str) -> str:
        return json.dumps({"still": "wrong"})  # validates? no.

    obj, reason = es.parse_or_repair(broken, repair_fn=bad_repair)
    assert obj is None
    assert reason is FailureReason.SCHEMA_VIOLATION


# ---- bounded sub-extractions (a truncation in one no longer kills the paper)

def test_assemble_subextractions_survives_one_failed_family():
    parts = {
        "study_characteristics": {"study_metadata": {"design": "rct"}},
        "outcomes": {"factual_extraction": {"key_findings": ["a"]}},
        "bias_audit": None,  # this sub-object truncated / failed
        "mechanism": {"phenotype_mapping": {"primary_mechanism": "autoimmunity"}},
    }
    assembled = es.assemble_subextractions(parts)
    assert assembled["study_metadata"]["design"] == "rct"
    assert assembled["factual_extraction"]["key_findings"] == ["a"]
    assert assembled["_subextraction_failures"] == ["bias_audit"]


# ---- oversized payload survives -------------------------------------------

def test_oversized_payload_validates_when_complete():
    """A synthetic 40k-token-ish full text produces a large but COMPLETE
    object — it must validate, not be dropped (acceptance §2)."""
    obj = _good_extraction()
    obj["factual_extraction"]["key_findings"] = ["finding " + "x" * 50 for _ in range(800)]
    raw = json.dumps(obj)
    assert len(raw) > 40_000
    parsed, reason = es.parse_or_repair(raw)
    assert reason is None and parsed is not None


def test_oversized_payload_failure_is_logged_not_dropped():
    """If the oversized payload truncates and cannot be repaired, it surfaces
    with a populated failure_reason (it never vanishes)."""
    obj = _good_extraction()
    obj["factual_extraction"]["key_findings"] = ["x" * 100 for _ in range(800)]
    truncated = json.dumps(obj)[:-200]
    parsed, reason = es.parse_or_repair(truncated, repair_fn=None)
    assert parsed is None
    assert reason is FailureReason.TRUNCATION  # populated, never None/silent


# ---- conservation invariant + substitution --------------------------------

def test_conservation_no_failures():
    intended = ["p1", "p2", "p3"]
    outcome = fr.reconcile_selection(intended, succeeded_ids=set(intended))
    outcome.assert_conservation()
    assert outcome.n_intended == 3
    assert outcome.n_extracted == 3
    assert outcome.n_failed == 0
    assert outcome.n_substituted == 0


def test_substitution_is_recorded():
    intended = ["p1", "p2", "p3"]
    # p2 failed; p9 is the next-ranked replacement and it extracts fine.
    succeeded = {"p1", "p3", "p9"}
    outcome = fr.reconcile_selection(
        intended,
        succeeded_ids=succeeded,
        replacement_pool=["p9", "p10"],
        failure_reasons={"p2": FailureReason.TRUNCATION.value},
    )
    outcome.assert_conservation()
    assert outcome.n_failed == 1
    assert outcome.n_substituted == 1
    assert outcome.n_extracted == 3
    assert outcome.final_corpus_ids == ["p1", "p9", "p3"]
    assert outcome.substitutions[0].failed_id == "p2"
    assert outcome.substitutions[0].replacement_id == "p9"
    assert outcome.substitutions[0].failed_reason == "truncation"


def test_failure_without_substitution_conserves():
    intended = ["p1", "p2", "p3"]
    # p2 and p3 fail; only one replacement available.
    succeeded = {"p1", "p9"}
    outcome = fr.reconcile_selection(
        intended,
        succeeded_ids=succeeded,
        replacement_pool=["p9"],  # only one good replacement for two failures
        failure_reasons={"p2": "truncation", "p3": "api_error"},
    )
    outcome.assert_conservation()
    assert outcome.n_failed == 2
    assert outcome.n_substituted == 1
    assert outcome.n_failed_without_substitution == 1
    # the explicit invariant from the spec
    assert outcome.n_intended == outcome.n_extracted + outcome.n_failed_without_substitution


def test_flow_record_diagram_and_counts():
    intended = ["p1", "p2", "p3", "p4"]
    succeeded = {"p1", "p2", "p3"}  # p4 failed, no replacement
    sel = fr.reconcile_selection(
        intended, succeeded_ids=succeeded, replacement_pool=[],
        failure_reasons={"p4": "timeout"},
    )
    record = fr.build_flow_record(
        identified=5000, triaged=4800, eligible=300, selection=sel,
        failures_by_reason={"timeout": 1},
    )
    assert record.selected_deep == 4
    assert record.extraction_succeeded == 3
    assert record.extraction_failed == 1
    assert record.final_corpus == record.extraction_succeeded
    text = record.as_text_diagram()
    assert "final synthesised corpus" in text
    assert "timeout:1" in text
