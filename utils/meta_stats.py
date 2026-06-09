"""Reference meta-analysis estimators (UPGRADE v3.1 — P3).

Replaces the pure-numpy approximations in phase5 with library-backed
implementations while keeping the EXACT output shape the rest of the pipeline
consumes (so forest plots, report templates and aggregates are untouched):

  - DerSimonian–Laird τ² via PyMARE (then I²/Q/CI derived with the standard
    closed-form, identical formulas the consumers already expect).
  - Egger's regression via statsmodels OLS (proper t-distribution p-value).
  - Trim-and-fill stays the in-house implementation (no maintained Python
    library); it is validated by tests and declared as such in the report.

Every function degrades gracefully: if a library is missing or the fit fails,
it returns ``None`` so the caller can fall back to the legacy estimator.
"""

from __future__ import annotations

import numpy as np


def dl_tau2_reference(effects: list[float], variances: list[float]) -> float | None:
    """DerSimonian–Laird τ² from PyMARE. Returns None if unavailable."""
    if len(effects) < 2:
        return 0.0
    try:
        from pymare import Dataset
        from pymare.estimators import DerSimonianLaird
    except Exception:
        return None
    try:
        y = np.asarray(effects, dtype=float).reshape(-1, 1)
        v = np.asarray(variances, dtype=float).reshape(-1, 1)
        v = np.where(v <= 0, 1e-6, v)
        ds = Dataset(y=y, v=v)
        est = DerSimonianLaird().fit_dataset(ds)
        tau2 = float(np.asarray(est.params_["tau2"]).ravel()[0])
        return max(0.0, tau2)
    except Exception:
        return None


def pool_random_effects_reference(effects: list[float], variances: list[float]) -> dict | None:
    """Random-effects pooling using a PyMARE-derived τ². Output shape matches
    phase5._pool_random_effects exactly. Returns None when PyMARE is unavailable
    so the caller can fall back to the legacy estimator."""
    if not effects:
        return None
    tau2 = dl_tau2_reference(effects, variances)
    if tau2 is None:
        return None
    eff = np.asarray(effects, dtype=float)
    var = np.asarray(variances, dtype=float)
    var = np.where(var <= 0, 1e-6, var)

    w_fe = 1.0 / var
    pooled_fe = float(np.sum(w_fe * eff) / np.sum(w_fe))
    q = float(np.sum(w_fe * (eff - pooled_fe) ** 2))
    df = len(eff) - 1
    i2 = max(0.0, (q - df) / q) * 100.0 if (q > 0 and df >= 1) else 0.0

    w_re = 1.0 / (var + tau2)
    pooled = float(np.sum(w_re * eff) / np.sum(w_re))
    se = float(np.sqrt(1.0 / np.sum(w_re)))
    return {
        "pooled": pooled,
        "se": se,
        "ci_low": pooled - 1.96 * se,
        "ci_high": pooled + 1.96 * se,
        "i_squared": float(i2),
        "q": q,
        "tau_squared": float(tau2),
        "n_studies": len(eff),
        "weights_re": w_re.tolist(),
        "estimator": "pymare_dersimonian_laird",
    }


def egger_test_reference(effects: list[float], variances: list[float]) -> dict | None:
    """Egger's regression test via statsmodels OLS of the standardised effect on
    precision (1/SE). Returns {egger_p, egger_intercept} or None if unavailable."""
    n = len(effects)
    if n < 3:
        return None
    try:
        import statsmodels.api as sm
    except Exception:
        return None
    try:
        eff = np.asarray(effects, dtype=float)
        var = np.asarray(variances, dtype=float)
        se = np.sqrt(np.where(var <= 0, 1e-6, var))
        precision = 1.0 / se
        snd = eff / se  # standard normal deviate
        X = sm.add_constant(precision)
        model = sm.OLS(snd, X).fit()
        intercept = float(model.params[0])
        p_value = float(model.pvalues[0])
        return {"egger_p": p_value, "egger_intercept": intercept}
    except Exception:
        return None
