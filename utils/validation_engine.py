"""Validation engine for human-vs-AI extraction agreement.

Master Improvement Spec v3.0 — Priority 2.3.

Computes:
  - Cohen's Kappa for categorical / discrete variables (GRADE, NOS, QUADAS
    sub-scores, boolean bias flags).
  - RMSE for continuous variables (sample size, effect size r, etc.).
  - Pearson correlation for continuous variables.

Reads human ratings from Supabase table `human_ratings` (schema v3) and AI
ratings from the `extractions` table for the same paper_id × field_name.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np


def compute_cohens_kappa(human: Iterable, ai: Iterable) -> float:
    """Compute Cohen's Kappa for two parallel lists of categorical ratings.

    Categories are auto-discovered from the union of values seen across both
    raters. Returns a value in [-1, 1]: 0 = chance agreement, 1 = perfect.
    """
    h_list = list(human)
    a_list = list(ai)
    if len(h_list) != len(a_list) or not h_list:
        return 0.0

    categories = sorted({*h_list, *a_list}, key=lambda x: str(x))
    if not categories:
        return 0.0
    cat_index = {c: i for i, c in enumerate(categories)}
    k = len(categories)
    n = len(h_list)
    confusion = np.zeros((k, k), dtype=float)
    for h, a in zip(h_list, a_list):
        confusion[cat_index[h]][cat_index[a]] += 1.0

    p_o = float(np.trace(confusion) / n)
    row_sums = np.sum(confusion, axis=1) / n
    col_sums = np.sum(confusion, axis=0) / n
    p_e = float(np.sum(row_sums * col_sums))
    if p_e == 1.0:
        return 1.0
    return float((p_o - p_e) / (1.0 - p_e))


def compute_rmse(human: Iterable[float], ai: Iterable[float]) -> float:
    h = np.array(list(human), dtype=float)
    a = np.array(list(ai), dtype=float)
    if h.size == 0 or h.size != a.size:
        return 0.0
    return float(np.sqrt(np.mean((h - a) ** 2)))


def compute_pearson(human: Iterable[float], ai: Iterable[float]) -> float:
    h = np.array(list(human), dtype=float)
    a = np.array(list(ai), dtype=float)
    if h.size < 2 or h.size != a.size:
        return 0.0
    h_std = np.std(h)
    a_std = np.std(a)
    if h_std == 0 or a_std == 0:
        return 0.0
    return float(np.corrcoef(h, a)[0, 1])


def interpret_kappa(k: float) -> str:
    """Landis & Koch 1977 benchmarks."""
    if k < 0:
        return "worse than chance"
    if k < 0.20:
        return "slight"
    if k < 0.40:
        return "fair"
    if k < 0.60:
        return "moderate"
    if k < 0.80:
        return "substantial"
    return "almost perfect"


def validate_field(human_ratings: list[dict], ai_ratings: list[dict], field_kind: str) -> dict:
    """Top-level helper: aligns human and AI ratings on (paper_id, field_name)
    and computes the appropriate statistic.

    Inputs:
      human_ratings: rows from the human_ratings table.
      ai_ratings:    dicts shaped like {paper_id, field_name, value}.
      field_kind:    'discrete' | 'continuous' | 'boolean'.

    Returns: {n, statistic, statistic_name, interpretation, missing}
    """
    ai_lookup = {(r["paper_id"], r["field_name"]): r["value"] for r in ai_ratings}
    paired_h: list = []
    paired_a: list = []
    missing = 0
    for hr in human_ratings:
        key = (hr["paper_id"], hr["field_name"])
        if key not in ai_lookup:
            missing += 1
            continue
        paired_h.append(hr["rating_value"])
        paired_a.append(ai_lookup[key])

    if field_kind == "continuous":
        try:
            stat = compute_rmse([float(x) for x in paired_h], [float(x) for x in paired_a])
            return {
                "n": len(paired_h),
                "statistic": stat,
                "statistic_name": "RMSE",
                "interpretation": "lower is better; depends on scale",
                "missing": missing,
            }
        except (TypeError, ValueError):
            pass  # fall through to kappa
    stat = compute_cohens_kappa(paired_h, paired_a)
    return {
        "n": len(paired_h),
        "statistic": stat,
        "statistic_name": "Cohen's Kappa",
        "interpretation": interpret_kappa(stat),
        "missing": missing,
    }


# Field → kind mapping for the UI Kappa panel (UPGRADE v3.1 — P5.2).
FIELD_KINDS = {
    "grade_certainty": "discrete",
    "nos_score": "continuous",
    "quadas_total": "continuous",
    "calibrated_certainty": "discrete",
    "surveillance_bias": "boolean",
    "selection_bias": "boolean",
    "self_report_bias": "boolean",
}


def kappa_panel(human_ratings: list[dict], ai_ratings: list[dict]) -> dict:
    """Per-variable agreement panel for the UI. Groups human/AI ratings by
    field_name and computes the appropriate statistic for each.

    human_ratings: rows from human_ratings (paper_id, field_name, rating_value).
    ai_ratings:    {paper_id, field_name, value} dicts derived from extractions.
    Returns {field_name: validate_field(...)} plus a coverage summary.
    """
    fields = {hr["field_name"] for hr in human_ratings}
    panel: dict[str, dict] = {}
    for field in sorted(fields):
        kind = FIELD_KINDS.get(field, "discrete")
        h = [hr for hr in human_ratings if hr["field_name"] == field]
        a = [ar for ar in ai_ratings if ar["field_name"] == field]
        panel[field] = validate_field(h, a, kind)
    panel["_summary"] = {
        "n_fields": len(fields),
        "n_human_ratings": len(human_ratings),
        "papers_rated": len({hr["paper_id"] for hr in human_ratings}),
    }
    return panel
