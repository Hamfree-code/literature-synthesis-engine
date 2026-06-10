"""P0/P3 — meta-analysis estimators against synthetic data with known effects.

The reference (PyMARE/statsmodels) and legacy (numpy) estimators must both
recover a planted effect within tolerance and agree with each other to <1%.
"""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.phase5_analyze import (
    _pool_random_effects,
    _pool_random_effects_legacy,
    assess_publication_bias,
    leave_one_out_analysis,
)
from utils.meta_stats import (
    egger_test_reference,
    pool_random_effects_reference,
)


def _synthetic(true_effect=0.3, k=20, tau=0.0, seed=0):
    rng = np.random.default_rng(seed)
    variances = rng.uniform(0.01, 0.05, size=k)
    study_means = rng.normal(true_effect, tau, size=k)
    effects = [float(rng.normal(m, np.sqrt(v))) for m, v in zip(study_means, variances)]
    return effects, list(variances)


def test_legacy_recovers_known_effect():
    effects, variances = _synthetic(true_effect=0.3, k=40, seed=1)
    out = _pool_random_effects_legacy(effects, variances)
    assert out["pooled"] == pytest.approx(0.3, abs=0.05)


def test_reference_recovers_known_effect():
    effects, variances = _synthetic(true_effect=0.3, k=40, seed=1)
    ref = pool_random_effects_reference(effects, variances)
    assert ref is not None
    assert ref["pooled"] == pytest.approx(0.3, abs=0.05)
    assert ref["estimator"] == "pymare_dersimonian_laird"


def test_reference_and_legacy_agree_within_1pct():
    effects, variances = _synthetic(true_effect=0.4, k=30, tau=0.1, seed=2)
    legacy = _pool_random_effects_legacy(effects, variances)
    ref = pool_random_effects_reference(effects, variances)
    assert ref is not None
    rel = abs(ref["pooled"] - legacy["pooled"]) / max(abs(legacy["pooled"]), 1e-6)
    assert rel < 0.01


def test_heterogeneity_detected_with_high_tau():
    effects, variances = _synthetic(true_effect=0.3, k=30, tau=0.4, seed=3)
    out = _pool_random_effects(effects, variances)
    assert out["i_squared"] > 50.0
    assert out["tau_squared"] > 0.0


def test_homogeneous_data_low_i_squared():
    # identical effects → no heterogeneity
    effects = [0.3] * 10
    variances = [0.02] * 10
    out = _pool_random_effects(effects, variances)
    assert out["i_squared"] == pytest.approx(0.0, abs=1.0)


def test_egger_symmetric_data_high_p():
    effects, variances = _synthetic(true_effect=0.3, k=30, seed=4)
    ref = egger_test_reference(effects, variances)
    assert ref is not None
    assert ref["egger_p"] > 0.05  # no small-study effect planted


def test_egger_asymmetric_data_low_p():
    # plant a precision-correlated bias: small studies (high variance) inflated
    rng = np.random.default_rng(5)
    variances = list(rng.uniform(0.01, 0.2, size=30))
    effects = [0.3 + 2.0 * v + float(rng.normal(0, 0.02)) for v in variances]
    ref = egger_test_reference(effects, variances)
    assert ref is not None
    assert ref["egger_p"] < 0.05


def test_leave_one_out_flags_influential_study():
    effects = [0.30, 0.31, 0.29, 0.30, 0.95]  # last one is an outlier
    variances = [0.01] * 5
    out = leave_one_out_analysis(effects, variances, ["a", "b", "c", "d", "OUT"])
    assert "OUT" in out["influential_papers"]


def test_publication_bias_insufficient_data():
    out = assess_publication_bias([0.3, 0.4], [0.02, 0.03])
    assert out["publication_bias_risk"] == "insufficient_data"


def test_pooling_skipped_below_threshold(monkeypatch):
    from pipeline.phase5_analyze import meta_analyze_by_factor, settings

    monkeypatch.setattr(settings, "MIN_STUDIES_POOLING", 3, raising=False)
    rows = [
        {"paper_id": "a", "factor": "x", "r": 0.3, "variance": 0.01, "n": 100},
        {"paper_id": "b", "factor": "x", "r": 0.4, "variance": 0.01, "n": 100},
    ]
    out = meta_analyze_by_factor(rows)
    assert out["x"]["pooled"] is None
    assert out["x"]["pooled_skipped"] is True
    assert out["x"]["n_studies"] == 2  # surfaced, not silently dropped


def test_pooling_runs_at_threshold(monkeypatch):
    from pipeline.phase5_analyze import meta_analyze_by_factor, settings

    monkeypatch.setattr(settings, "MIN_STUDIES_POOLING", 3, raising=False)
    rows = [{"paper_id": p, "factor": "x", "r": 0.3, "variance": 0.01, "n": 100} for p in ("a", "b", "c")]
    out = meta_analyze_by_factor(rows)
    assert out["x"]["pooled"] is not None
    assert out["x"]["pooled"]["n_studies"] == 3
