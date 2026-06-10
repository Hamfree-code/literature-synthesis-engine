"""P5 — run registry row shaping + Kappa panel."""

from __future__ import annotations

from utils.run_registry import build_run_row
from utils.validation_engine import kappa_panel


def test_build_run_row_maps_fields():
    manifest = {
        "topic": "fibromyalgia",
        "mesh_terms": None,
        "run_id": "abc",
        "engine_version": "3.1.0",
        "phase_counts": {"screened": 120, "included_deep": 50},
    }
    qa = {
        "sources_breakdown": {"pmc": 100, "openalex": 20},
        "deep_success_rate": 99.0,
        "cui_verified_pct": 82.0,
        "fulltext_coverage_pct": 95.0,
        "n_retracted_excluded": 1,
        "reconciliations": 3,
        "api_cost_usd": 22.5,
        "runtime_seconds": 1800,
    }
    row = build_run_row(manifest, qa)
    assert row["topic"] == "fibromyalgia"
    assert row["manifest_sha256"] == "abc"
    assert row["deep_success_rate"] == 99.0
    assert set(row["sources"]) == {"pmc", "openalex"}
    assert row["n_papers_deep"] == 50


def test_kappa_panel_per_variable():
    human = [
        {"paper_id": "p1", "field_name": "grade_certainty", "rating_value": "High"},
        {"paper_id": "p2", "field_name": "grade_certainty", "rating_value": "Low"},
        {"paper_id": "p1", "field_name": "nos_score", "rating_value": "7"},
        {"paper_id": "p2", "field_name": "nos_score", "rating_value": "6"},
    ]
    ai = [
        {"paper_id": "p1", "field_name": "grade_certainty", "value": "High"},
        {"paper_id": "p2", "field_name": "grade_certainty", "value": "Low"},
        {"paper_id": "p1", "field_name": "nos_score", "value": "7"},
        {"paper_id": "p2", "field_name": "nos_score", "value": "6"},
    ]
    panel = kappa_panel(human, ai)
    assert panel["grade_certainty"]["statistic_name"] == "Cohen's Kappa"
    assert panel["grade_certainty"]["interpretation"] == "almost perfect"
    assert panel["nos_score"]["statistic_name"] == "RMSE"
    assert panel["nos_score"]["statistic"] == 0.0
    assert panel["_summary"]["papers_rated"] == 2
