"""Tests for the pure statistical core of pipeline.phase5_analyze.

These functions are deterministic and offline (no API calls), so they can be
exercised directly. They are the heart of the meta-analysis — random-effects
pooling, leave-one-out influence, publication-bias estimation, and consensus
aggregation — and were previously untested.
"""
from __future__ import annotations

import json
import math

import pytest

from config.settings import settings
from pipeline import phase5_analyze as p5


# ── Fisher z round-trip ──────────────────────────────────────────────────

def test_fisher_z_inverse_round_trip():
    for r in (-0.8, -0.3, 0.0, 0.25, 0.75):
        assert p5._inverse_z(p5._fisher_z(r)) == pytest.approx(r, abs=1e-9)


def test_fisher_z_clamps_extremes():
    # r = 1.0 would be infinite without clamping; must stay finite.
    assert math.isfinite(p5._fisher_z(1.0))
    assert math.isfinite(p5._fisher_z(-1.0))


# ── Random-effects pooling ───────────────────────────────────────────────

def test_pool_empty_returns_zero_studies():
    res = p5._pool_random_effects([], [])
    assert res["n_studies"] == 0
    assert res["pooled"] is None


def test_pool_identical_effects_no_heterogeneity():
    res = p5._pool_random_effects([0.5, 0.5, 0.5], [0.01, 0.01, 0.01])
    assert res["pooled"] == pytest.approx(0.5, abs=1e-9)
    assert res["i_squared"] == pytest.approx(0.0, abs=1e-9)
    assert res["q"] == pytest.approx(0.0, abs=1e-9)
    assert res["n_studies"] == 3


def test_pool_single_study_has_zero_tau():
    res = p5._pool_random_effects([0.4], [0.02])
    assert res["pooled"] == pytest.approx(0.4, abs=1e-9)
    assert res["tau_squared"] == 0.0
    assert res["i_squared"] == 0.0


def test_pool_ci_brackets_pooled_estimate():
    res = p5._pool_random_effects([0.2, 0.4, 0.6], [0.02, 0.02, 0.02])
    assert res["ci_low"] < res["pooled"] < res["ci_high"]


def test_pool_heterogeneous_effects_raise_i_squared():
    homo = p5._pool_random_effects([0.3, 0.31, 0.29], [0.005, 0.005, 0.005])
    hetero = p5._pool_random_effects([0.05, 0.5, 0.9], [0.005, 0.005, 0.005])
    assert hetero["i_squared"] > homo["i_squared"]


# ── Model selection thresholds ───────────────────────────────────────────

@pytest.mark.parametrize("i2,expected", [
    (None, "insufficient_data"),
    (95.0, "random_effects_critical"),
    (80.0, "random_effects_mandatory"),
    (30.0, "random_effects_recommended"),
    (10.0, "fixed_effects"),
])
def test_select_model(i2, expected):
    assert p5.select_model(i2) == expected


# ── Leave-one-out influence ──────────────────────────────────────────────

def test_leave_one_out_stable_when_homogeneous():
    res = p5.leave_one_out_analysis([0.3, 0.3, 0.3], [0.01, 0.01, 0.01], ["a", "b", "c"])
    assert res["stable"] is True
    assert res["influential_papers"] == []


def test_leave_one_out_flags_outlier_as_influential():
    res = p5.leave_one_out_analysis(
        [0.1, 0.1, 0.9], [0.01, 0.01, 0.01], ["p1", "p2", "p3"]
    )
    assert res["stable"] is False
    assert "p3" in res["influential_papers"]


def test_leave_one_out_too_few_studies_is_stable():
    res = p5.leave_one_out_analysis([0.1, 0.9], [0.01, 0.01], ["p1", "p2"])
    assert res["stable"] is True
    assert res["influential_papers"] == []


# ── Publication bias ─────────────────────────────────────────────────────

def test_publication_bias_insufficient_data():
    res = p5.assess_publication_bias([0.3, 0.3, 0.3], [0.01, 0.01, 0.01])
    assert res["publication_bias_risk"] == "insufficient_data"
    assert res["egger_p"] is None


def test_publication_bias_symmetric_low_risk():
    n = max(10, settings.MIN_STUDIES_PUBLICATION_BIAS)
    res = p5.assess_publication_bias([0.3] * n, [0.01] * n)
    assert res["funnel_symmetry"] == "symmetric"
    assert res["publication_bias_risk"] == "low"
    assert res["n_studies"] == n


# ── JSONL collectors ─────────────────────────────────────────────────────

def _write_jsonl(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_collect_effect_sizes_variance_and_filtering(tmp_path):
    deep = tmp_path / "deep.jsonl"
    _write_jsonl(deep, [
        {
            "paper_id": "p1",
            "study_metadata": {"sample_size": 101},
            "effect_sizes_classified": [
                {"factor": "age", "r_equivalent": 0.5, "magnitude": "moderate"},
                {"factor": "missing_r", "magnitude": "weak"},          # dropped: no r
            ],
        },
        {
            "paper_id": "p2",
            "study_metadata": {"sample_size": 3},                       # dropped: n < 5
            "effect_sizes_classified": [{"factor": "age", "r_equivalent": 0.4}],
        },
    ])
    rows = p5.collect_effect_sizes(deep)
    assert len(rows) == 1
    row = rows[0]
    assert row["factor"] == "age"
    assert row["variance"] == pytest.approx(((1 - 0.5 ** 2) ** 2) / 100, rel=1e-9)


def test_meta_analyze_by_factor_groups_and_drops_singletons():
    rows = [
        {"paper_id": "p1", "factor": "age", "r": 0.3, "variance": 0.01, "n": 50},
        {"paper_id": "p2", "factor": "age", "r": 0.4, "variance": 0.01, "n": 60},
        {"paper_id": "p3", "factor": "bmi", "r": 0.2, "variance": 0.01, "n": 40},  # singleton
    ]
    res = p5.meta_analyze_by_factor(rows)
    assert "age" in res
    assert "bmi" not in res          # only one study → skipped
    assert res["age"]["pooled"]["n_studies"] == 2


def test_meta_analyze_respects_quadas_filter():
    rows = [
        {"paper_id": "p1", "factor": "age", "r": 0.3, "variance": 0.01, "n": 50},
        {"paper_id": "p2", "factor": "age", "r": 0.4, "variance": 0.01, "n": 60},
        {"paper_id": "p3", "factor": "age", "r": 0.5, "variance": 0.01, "n": 70},
    ]
    res = p5.meta_analyze_by_factor(rows, qaccept_ids={"p1", "p2"})
    assert res["age"]["pooled"]["n_studies"] == 2  # p3 excluded


def test_compute_symptom_consensus(tmp_path):
    triage = tmp_path / "triage.jsonl"
    _write_jsonl(triage, [
        {"main_symptoms": ["Fatigue", "Brain fog"]},
        {"main_symptoms": ["fatigue ", "Dyspnea"]},
        {"main_symptoms": []},
    ])
    res = p5.compute_symptom_consensus(triage)
    assert res["fatigue"]["count"] == 2          # case/space normalised
    assert res["fatigue"]["pct"] == pytest.approx(100.0)  # 2 of 2 symptom-bearing rows


def test_compute_definition_heterogeneity(tmp_path):
    triage = tmp_path / "triage.jsonl"
    _write_jsonl(triage, [
        {"long_covid_definition_weeks": 12},
        {"long_covid_definition_weeks": 12},
        {"long_covid_definition_weeks": 4},
        {"long_covid_definition_weeks": None},
    ])
    res = p5.compute_definition_heterogeneity(triage)
    assert res[12] == 2
    assert res[4] == 1


# ── Uncertainty propagation ──────────────────────────────────────────────

def _deep_with_symptom(symptom, certainty, conf=0.8):
    return {
        "calibration": {"calibrated_certainty": certainty, "extraction_confidence": conf},
        "factual_extraction": {"symptoms_prevalence": {symptom: 0.5}},
    }


def test_propagate_uncertainty_contradicted_overrides():
    extractions = [
        _deep_with_symptom("fatigue", "established"),
        _deep_with_symptom("fatigue", "contradicted"),
    ]
    res = p5.propagate_uncertainty(extractions)
    assert res["fatigue"]["consensus_certainty"] == "contradicted"


def test_propagate_uncertainty_established_consensus():
    extractions = [_deep_with_symptom("fatigue", "established") for _ in range(5)]
    res = p5.propagate_uncertainty(extractions)
    assert res["fatigue"]["consensus_certainty"] == "established"
    assert res["fatigue"]["n_papers"] == 5


def test_propagate_uncertainty_single_paper_is_speculative():
    res = p5.propagate_uncertainty([_deep_with_symptom("anosmia", "probable")])
    assert res["anosmia"]["consensus_certainty"] == "speculative"


def test_build_uncertainty_report_section_buckets_and_sorts():
    consensus = p5.propagate_uncertainty([
        _deep_with_symptom("fatigue", "established") for _ in range(5)
    ] + [_deep_with_symptom("rash", "contradicted")])
    section = p5.build_uncertainty_report_section(consensus)
    assert any(e["symptom"] == "fatigue" for e in section["established"])
    assert any(e["symptom"] == "rash" for e in section["contradicted"])


# ── CUI canonicalization ─────────────────────────────────────────────────

def test_build_verbatim_cui_map_majority_vote(tmp_path):
    norm = tmp_path / "normalized_entities.jsonl"
    _write_jsonl(norm, [
        {"paper_id": "p1", "entities": [
            {"verbatim_text": "Fatigue", "umls_cui": "C0015672", "mesh_heading": "Fatigue"},
            {"verbatim_text": "no cui here", "umls_cui": "", "mesh_heading": ""},  # skipped
        ]},
        {"paper_id": "p2", "entities": [
            {"verbatim_text": "fatigue", "umls_cui": "C0015672", "mesh_heading": "Fatigue"},
            {"verbatim_text": "fatigue", "umls_cui": "C9999999", "mesh_heading": "Wrong"},  # minority
        ]},
    ])
    m = p5.build_verbatim_cui_map(norm)
    assert m["fatigue"]["cui"] == "C0015672"   # 2 votes beat 1
    assert "no cui here" not in m


def test_build_verbatim_cui_map_missing_file(tmp_path):
    assert p5.build_verbatim_cui_map(tmp_path / "absent.jsonl") == {}


def test_canonicalize_merges_synonyms_onto_one_cui():
    consensus = {
        "fatigue": {"n_papers": 3, "mean_extraction_confidence": 0.8,
                    "certainty_distribution": {"established": 3, "probable": 0, "possible": 0,
                                               "speculative": 0, "contradicted": 0}},
        "tiredness": {"n_papers": 2, "mean_extraction_confidence": 0.6,
                      "certainty_distribution": {"established": 2, "probable": 0, "possible": 0,
                                                 "speculative": 0, "contradicted": 0}},
    }
    vmap = {
        "fatigue": {"cui": "C0015672", "mesh_heading": "Fatigue"},
        "tiredness": {"cui": "C0015672", "mesh_heading": "Fatigue"},
    }
    res = p5.canonicalize_consensus_by_cui(consensus, vmap)
    assert set(res.keys()) == {"C0015672"}
    entry = res["C0015672"]
    assert entry["canonical"] is True
    assert entry["n_papers"] == 5                       # 3 + 2 merged
    assert sorted(entry["members"]) == ["fatigue", "tiredness"]
    # weighted mean confidence: (0.8*3 + 0.6*2) / 5 = 0.72
    assert entry["mean_extraction_confidence"] == pytest.approx(0.72)
    assert entry["consensus_certainty"] == "established"  # 5/5 established


def test_canonicalize_keeps_unmapped_terms_flagged():
    consensus = {
        "weird_symptom": {"n_papers": 2, "mean_extraction_confidence": 0.5,
                          "certainty_distribution": {"established": 0, "probable": 0, "possible": 2,
                                                     "speculative": 0, "contradicted": 0}},
    }
    res = p5.canonicalize_consensus_by_cui(consensus, {})
    assert "verbatim:weird_symptom" in res
    entry = res["verbatim:weird_symptom"]
    assert entry["canonical"] is False
    assert entry["cui"] is None
    assert entry["consensus_certainty"] == "possible"


def test_canonicalize_contradicted_propagates_through_merge():
    consensus = {
        "fatigue": {"n_papers": 4, "mean_extraction_confidence": 0.8,
                    "certainty_distribution": {"established": 4, "probable": 0, "possible": 0,
                                               "speculative": 0, "contradicted": 0}},
        "exhaustion": {"n_papers": 1, "mean_extraction_confidence": 0.4,
                       "certainty_distribution": {"established": 0, "probable": 0, "possible": 0,
                                                  "speculative": 0, "contradicted": 1}},
    }
    vmap = {
        "fatigue": {"cui": "C0015672", "mesh_heading": "Fatigue"},
        "exhaustion": {"cui": "C0015672", "mesh_heading": "Fatigue"},
    }
    res = p5.canonicalize_consensus_by_cui(consensus, vmap)
    # one contradicted vote in the merged group overrides everything
    assert res["C0015672"]["consensus_certainty"] == "contradicted"


# ── Forest plot rendering (smoke) ────────────────────────────────────────

def test_forest_plot_text_renders():
    effects = [0.2, 0.5, 0.8]
    variances = [0.01, 0.01, 0.01]
    paper_ids = ["p1", "p2", "p3"]
    pool = p5._pool_random_effects(effects, variances)
    loo = p5.leave_one_out_analysis(effects, variances, paper_ids)
    per_study = [
        {"paper_id": pid, "r": r, "variance": v, "n": 50}
        for pid, r, v in zip(paper_ids, effects, variances)
    ]
    factor_result = {"pooled": pool, "leave_one_out": loo, "per_study": per_study}
    text = p5.forest_plot_text("age_at_infection", factor_result)
    assert "FOREST PLOT" in text
    assert "age_at_infection" in text
    assert "POOLED (RE)" in text
