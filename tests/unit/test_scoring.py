"""P0 — deep-analysis selection scoring (sample_size × design_weight × confidence)."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def triage_file(tmp_path, monkeypatch):
    """Point app_data at a tmp dir and write a triage_results.jsonl."""
    import app_paths

    data_dir = tmp_path
    monkeypatch.setattr(app_paths, "APP_DATA_DIR", data_dir, raising=False)
    monkeypatch.setattr(app_paths, "app_data", lambda rel: data_dir / rel, raising=False)

    import pipeline.phase3_extract as p3

    monkeypatch.setattr(p3, "app_data", lambda rel: data_dir / rel, raising=False)

    rows = [
        {
            "paper_id": "A",
            "is_topic_focused": True,
            "sample_size": 1000,
            "study_design": "RCT",
            "extraction_confidence": 0.9,
        },
        {
            "paper_id": "B",
            "is_topic_focused": True,
            "sample_size": 100,
            "study_design": "cohort",
            "extraction_confidence": 0.9,
        },
        {
            "paper_id": "C",
            "is_topic_focused": True,
            "sample_size": 500,
            "study_design": "meta_analysis",
            "extraction_confidence": 1.0,
        },
        {
            "paper_id": "D",
            "is_topic_focused": False,
            "sample_size": 9999,
            "study_design": "RCT",
            "extraction_confidence": 1.0,
        },
        {
            "paper_id": "E",
            "is_long_covid_focused": True,
            "sample_size": 50,
            "study_design": "other",
            "extraction_confidence": 0.5,
        },
    ]
    path = data_dir / "data/filtered/triage_results.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return p3


def test_ranking_order(triage_file):
    ids = triage_file.select_for_deep_analysis(top_n=10)
    # D excluded (not topic focused). A: 1000*1.0*0.9=900, C: 500*1.2*1.0=600, B:100*0.9=90, E:50*0.5*0.5=12.5
    assert ids[0] == "A"
    assert ids[1] == "C"
    assert "D" not in ids


def test_legacy_field_still_selected(triage_file):
    ids = triage_file.select_for_deep_analysis(top_n=10)
    assert "E" in ids  # selected via legacy is_long_covid_focused fallback


def test_top_n_caps(triage_file):
    assert len(triage_file.select_for_deep_analysis(top_n=2)) == 2
