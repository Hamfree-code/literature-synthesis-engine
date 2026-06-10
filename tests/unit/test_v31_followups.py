"""v3.1 follow-ups — pooling threshold + honest [VERIFIED] badge wiring."""

from __future__ import annotations

import json

import pipeline.phase5_analyze as p5
from utils.enterprise_report import ontology_section_markdown


def _rows(factor, rs):
    return [
        {"paper_id": f"p{i}", "factor": factor, "r": r, "variance": 0.02, "n": 100} for i, r in enumerate(rs)
    ]


def test_two_studies_are_not_pooled(monkeypatch):
    monkeypatch.setattr(p5.settings, "MIN_STUDIES_POOLING", 3, raising=False)
    out = p5.meta_analyze_by_factor(_rows("fatigue", [0.3, 0.34]))
    assert out["fatigue"]["pooled"] is None
    assert out["fatigue"]["pooled_skipped"] is True
    assert out["fatigue"]["n_studies"] == 2


def test_threshold_studies_are_pooled(monkeypatch):
    monkeypatch.setattr(p5.settings, "MIN_STUDIES_POOLING", 3, raising=False)
    out = p5.meta_analyze_by_factor(_rows("fatigue", [0.3, 0.34, 0.28]))
    assert out["fatigue"].get("pooled_skipped") is None
    assert out["fatigue"]["pooled"]["n_studies"] == 3


def test_pooling_threshold_is_configurable(monkeypatch):
    monkeypatch.setattr(p5.settings, "MIN_STUDIES_POOLING", 5, raising=False)
    out = p5.meta_analyze_by_factor(_rows("fatigue", [0.3, 0.34, 0.28, 0.31]))
    assert out["fatigue"]["pooled"] is None  # 4 < 5 → not pooled


def test_verified_badge_only_when_cui_verified(tmp_path):
    (tmp_path / "data/filtered").mkdir(parents=True)
    (tmp_path / "data/filtered/normalized_entities.jsonl").write_text(
        json.dumps(
            {
                "paper_id": "P1",
                "entities": [
                    {
                        "verbatim_text": "fatigue",
                        "cui_verified": True,
                        "preferred_name": "Fatigue",
                        "umls_cui": "C0015672",
                    },
                    {"verbatim_text": "brain fog", "cui_verified": False, "umls_cui": "C9999"},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    md = ontology_section_markdown(lambda rel: tmp_path / rel)
    assert "1/2 CUIs (50.0%)" in md
    assert "fatigue <sup>[VERIFIED]</sup>" in md
    assert "brain fog <sup>[VERIFIED]</sup>" not in md  # unverified never badged


def test_offline_run_shows_zero_verified_no_false_credibility(tmp_path):
    (tmp_path / "data/filtered").mkdir(parents=True)
    (tmp_path / "data/filtered/normalized_entities.jsonl").write_text(
        json.dumps(
            {
                "paper_id": "P1",
                "entities": [
                    {"verbatim_text": "x", "cui_verified": False},
                    {"verbatim_text": "y", "cui_verified": False},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    md = ontology_section_markdown(lambda rel: tmp_path / rel)
    assert "0/2" in md
    assert "every CUI is LLM-inferred" in md
    # The key guarantee: no [VERIFIED] badge is ever *applied* to an entity.
    assert "<sup>[VERIFIED]</sup>" not in md
