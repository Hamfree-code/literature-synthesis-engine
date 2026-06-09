"""P0 — Cohen's Kappa against Landis & Koch reference values."""

from __future__ import annotations

import pytest

from utils.validation_engine import (
    compute_cohens_kappa,
    compute_pearson,
    compute_rmse,
    interpret_kappa,
    validate_field,
)


def test_perfect_agreement():
    h = ["High", "Low", "Moderate", "High"]
    assert compute_cohens_kappa(h, list(h)) == pytest.approx(1.0)


def test_chance_level_agreement_is_near_zero():
    # Two raters with identical marginals but independent assignment.
    human = ["yes", "no"] * 25
    ai = (["yes"] * 25) + (["no"] * 25)
    k = compute_cohens_kappa(human, ai)
    assert -0.4 < k < 0.4


def test_known_kappa_2x2():
    # Classic 2x2 worked example: a=20,b=10,c=5,d=15 -> kappa ~0.40
    human = ["+"] * 30 + ["-"] * 20
    ai = ["+"] * 20 + ["-"] * 10 + ["+"] * 5 + ["-"] * 15
    k = compute_cohens_kappa(human, ai)
    assert 0.30 < k < 0.50


def test_landis_koch_bands():
    assert interpret_kappa(-0.1) == "worse than chance"
    assert interpret_kappa(0.1) == "slight"
    assert interpret_kappa(0.3) == "fair"
    assert interpret_kappa(0.5) == "moderate"
    assert interpret_kappa(0.7) == "substantial"
    assert interpret_kappa(0.9) == "almost perfect"


def test_rmse_and_pearson():
    assert compute_rmse([1, 2, 3], [1, 2, 3]) == pytest.approx(0.0)
    assert compute_rmse([1, 2, 3], [2, 3, 4]) == pytest.approx(1.0)
    assert compute_pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)


def test_validate_field_continuous_uses_rmse():
    human = [{"paper_id": "p1", "field_name": "nos", "rating_value": "7"}]
    ai = [{"paper_id": "p1", "field_name": "nos", "value": "7"}]
    out = validate_field(human, ai, "continuous")
    assert out["statistic_name"] == "RMSE"
    assert out["n"] == 1


def test_validate_field_discrete_uses_kappa():
    human = [
        {"paper_id": "p1", "field_name": "grade", "rating_value": "High"},
        {"paper_id": "p2", "field_name": "grade", "rating_value": "Low"},
    ]
    ai = [
        {"paper_id": "p1", "field_name": "grade", "value": "High"},
        {"paper_id": "p2", "field_name": "grade", "value": "Low"},
    ]
    out = validate_field(human, ai, "discrete")
    assert out["statistic_name"] == "Cohen's Kappa"
    assert out["interpretation"] == "almost perfect"
