"""P0/P3 — phase5 pure aggregation functions (no Anthropic, no network)."""

from __future__ import annotations

import json

import pytest

import pipeline.phase5_analyze as p5


def _deep_record(
    pid,
    design="prospective_cohort",
    n=200,
    grade="Moderate",
    nos=7,
    certainty="probable",
    factor="female sex",
    r=0.3,
    mech="autoimmunity",
):
    return {
        "paper_id": pid,
        "study_metadata": {"design": design, "sample_size": n, "title": f"Study {pid}"},
        "factual_extraction": {
            "symptoms_prevalence": {"fatigue": "0.4", "brain fog": "0.3"},
            "key_findings": ["finding one", "finding two"],
        },
        "methodology_appraisal": {
            "grade_certainty": grade,
            "nos_score": nos,
            "limitations_inferred": ["small sample"],
        },
        "bias_audit": {"surveillance_bias": True, "selection_bias": False},
        "phenotype_mapping": {"primary_mechanism": mech, "secondary_mechanisms": ["vascular_endothelial"]},
        "calibration": {"calibrated_certainty": certainty, "extraction_confidence": 0.8},
        "effect_sizes_classified": [{"factor": factor, "r_equivalent": r, "magnitude": "moderate"}],
        "moderators": {"age_mean": 45.0, "sex_female_pct": 60.0},
        "quality_assessment": {"quadas_total": 15},
    }


@pytest.fixture
def staged(tmp_path, monkeypatch):
    monkeypatch.setattr(p5, "app_data", lambda rel: tmp_path / rel, raising=False)
    (tmp_path / "data/filtered").mkdir(parents=True, exist_ok=True)
    triage = [
        {
            "paper_id": "A",
            "main_symptoms": ["Fatigue", "Brain Fog"],
            "study_design": "cohort",
            "definition_threshold_weeks": 12,
        },
        {
            "paper_id": "B",
            "main_symptoms": ["fatigue"],
            "study_design": "RCT",
            "long_covid_definition_weeks": 12,
        },
    ]
    deep = [
        _deep_record("A", r=0.30),
        _deep_record("B", r=0.34, certainty="established"),
        _deep_record("C", r=0.28, design="rct"),
    ]
    (tmp_path / "data/filtered/triage_results.jsonl").write_text(
        "\n".join(json.dumps(t) for t in triage), encoding="utf-8"
    )
    (tmp_path / "data/filtered/deep_results.jsonl").write_text(
        "\n".join(json.dumps(d) for d in deep), encoding="utf-8"
    )
    return tmp_path, p5


def test_symptom_consensus(staged):
    tmp, p5 = staged
    out = p5.compute_symptom_consensus(tmp / "data/filtered/triage_results.jsonl")
    assert out["fatigue"]["count"] == 2


def test_definition_heterogeneity_both_keys(staged):
    tmp, p5 = staged
    out = p5.compute_definition_heterogeneity(tmp / "data/filtered/triage_results.jsonl")
    assert out.get(12) == 2  # new + legacy keys both counted


def test_study_design_distribution(staged):
    tmp, p5 = staged
    out = p5.compute_study_design_distribution(tmp / "data/filtered/triage_results.jsonl")
    assert out["cohort"] == 1 and out["RCT"] == 1


def test_collect_quadas_and_effects(staged):
    tmp, p5 = staged
    deep = tmp / "data/filtered/deep_results.jsonl"
    quadas = p5.collect_quadas_scores(deep)
    assert all(r["quadas_total"] == 15 for r in quadas)
    effects = p5.collect_effect_sizes(deep)
    assert len(effects) == 3
    assert all(e["factor"] == "female sex" for e in effects)


def test_methodology_quality(staged):
    tmp, p5 = staged
    mq = p5.compute_methodology_quality(tmp / "data/filtered/deep_results.jsonl")
    assert mq["n_deep"] == 3
    assert mq["grade_distribution"]["Moderate"] == 3
    assert mq["bias_audit_counts"]["surveillance_bias"] == 3
    assert mq["phenotype_counts"]["autoimmunity"] == 3


def test_meta_analyze_and_forest_plot(staged):
    tmp, p5 = staged
    effects = p5.collect_effect_sizes(tmp / "data/filtered/deep_results.jsonl")
    meta = p5.meta_analyze_by_factor(effects)
    assert "female sex" in meta
    res = meta["female sex"]
    assert res["pooled"]["n_studies"] == 3
    plot = p5.forest_plot_text("female sex", res)
    assert "FOREST PLOT" in plot and "POOLED" in plot


def test_propagate_uncertainty_topic_neutral(staged, monkeypatch):
    import utils.run_context as rc

    monkeypatch.setattr(rc, "CONTEXT_PATH", staged[0] / "run_meta.json", raising=False)
    rc.save_run_context("fibromyalgia")
    tmp, p5 = staged
    deep = [json.loads(line) for line in (tmp / "data/filtered/deep_results.jsonl").open()]
    out = p5.propagate_uncertainty(deep)
    assert "fatigue" in out
    # F2: statement must use the topic title, never hardcoded "Long COVID"
    assert "Fibromyalgia" in out["fatigue"]["consensus_statement"]
    assert "Long COVID" not in out["fatigue"]["consensus_statement"]
    section = p5.build_uncertainty_report_section(out)
    assert isinstance(section, dict)


def test_load_retracted_ids(staged):
    tmp, p5 = staged
    (tmp / "data/filtered/retracted.jsonl").write_text(
        json.dumps({"paper_id": "B", "doi": "10.1/x"}) + "\n", encoding="utf-8"
    )
    assert p5.load_retracted_ids() == {"B"}


def test_select_model_thresholds(staged):
    _, p5 = staged
    assert p5.select_model(None) == "insufficient_data"
    assert p5.select_model(95) == "random_effects_critical"
    assert p5.select_model(10) == "fixed_effects"


def test_slim_helpers(staged):
    _, p5 = staged
    d = _deep_record("Z")
    slim = p5._slim_deep(d)
    assert slim["paper_id"] == "Z" and slim["grade"] == "Moderate"
    agg = {
        "n_papers": 3,
        "calibrated_consensus": {
            "fatigue": {"n_papers": 2, "mean_extraction_confidence": 0.8, "consensus_certainty": "probable"}
        },
    }
    out = p5._slim_aggregates(agg)
    assert out["n_papers"] == 3
