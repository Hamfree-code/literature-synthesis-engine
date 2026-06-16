"""WP-9 — layer reconciliation: prose/calibrated certainty mismatch fails build.

Acceptance (spec §10):
  * Every certainty word in prose matches the calibrated tier; if all findings
    are speculative, the prose contains no "probable"/"possible" claims.
  * A prose finding tagged "probable" over a calibrated "speculative" finding
    fails the reconciliation gate.
"""
from __future__ import annotations

import pytest

from methodology import reconciliation as rc
from methodology.reconciliation import ReconciliationError


def test_consistent_layers_pass():
    calibrated = {"cognitive_function": "speculative", "fatigue": "speculative"}
    prose = [("cognitive_function", "speculative"), ("fatigue", "speculative")]
    report = rc.assert_consistent(prose, calibrated)
    assert report.consistent
    assert report.checked == 2


def test_probable_over_speculative_fails():
    calibrated = {"cognitive_function": "speculative"}
    prose = [("cognitive_function", "probable")]
    report = rc.reconcile_findings(prose, calibrated)
    assert not report.consistent
    assert report.mismatches[0].prose_tier == "probable"
    assert report.mismatches[0].calibrated_tier == "speculative"
    with pytest.raises(ReconciliationError):
        rc.assert_consistent(prose, calibrated)


def test_diff_report_is_human_readable():
    calibrated = {"hrqol": "speculative", "female_sex": "speculative"}
    prose = [("hrqol", "probable"), ("female_sex", "possible")]
    report = rc.reconcile_findings(prose, calibrated)
    diff = report.diff()
    assert "hrqol" in diff and "probable" in diff and "speculative" in diff


def test_all_speculative_prose_has_no_higher_tier_language():
    # The good case: prose stays speculative.
    rc.assert_prose_within_ceiling(
        "Preliminary findings hint at a speculative association.", max_tier="speculative"
    )


def test_prose_with_probable_above_speculative_ceiling_fails():
    with pytest.raises(ReconciliationError):
        rc.assert_prose_within_ceiling(
            "Cognitive impairment is a probable manifestation.", max_tier="speculative"
        )


def test_find_certainty_words():
    words = rc.find_certainty_words("This is probable; that is possible; the rest speculative.")
    assert set(words) == {"probable", "possible", "speculative"}
