"""P6 — enterprise report assembly end-to-end (no network)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from utils import enterprise_report

PROMPTS = Path(__file__).resolve().parents[2] / "config" / "prompts"


@pytest.fixture
def staged(tmp_path, monkeypatch):
    import utils.run_context as rc

    monkeypatch.setattr(rc, "CONTEXT_PATH", tmp_path / "run_meta.json", raising=False)
    rc.save_run_context("fibromyalgia")

    def app_data(rel):
        p = tmp_path / rel
        return p

    (tmp_path / "data/raw").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data/filtered").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)

    (tmp_path / "data/raw/sources_breakdown.json").write_text(
        json.dumps({"pmc": 100, "openalex": 20, "medrxiv": 5}), encoding="utf-8"
    )
    (tmp_path / "data/raw/run_stats.json").write_text(
        json.dumps(
            {
                "api_cost_usd": 22.5,
                "runtime_seconds": 1800,
                "queries_by_source": {"pmc": "fibromyalgia[tiab]"},
            }
        ),
        encoding="utf-8",
    )
    deep = [
        {
            "paper_id": "PMC1",
            "study_metadata": {"design": "rct", "sample_size": 200},
            "methodology_appraisal": {"grade_certainty": "Moderate", "nos_score": 7},
            "calibration": {"calibrated_certainty": "probable"},
            "effect_sizes_classified": [{"r_equivalent": 0.3}],
            "provenance": [{"field": "x", "claim": "c", "quote": "q", "section": "Results"}],
        },
    ]
    (tmp_path / "data/filtered/deep_results.jsonl").write_text(
        "\n".join(json.dumps(d) for d in deep), encoding="utf-8"
    )
    (tmp_path / "data/filtered/normalized_entities.jsonl").write_text(
        json.dumps({"paper_id": "PMC1", "entities": [{"cui_verified": True}, {"cui_verified": False}]})
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "data/raw/fulltext_cache.jsonl").write_text(
        json.dumps({"paper_id": "PMC1", "full_text": "x", "fulltext_source": "pmc_oa"}) + "\n",
        encoding="utf-8",
    )
    analysis = {
        "aggregates": {
            "n_papers": 120,
            "reconciliations_triggered": 3,
            "retracted_excluded": ["PMC_BAD"],
            "deep_extraction_yield": {"requested": 50, "succeeded": 50, "failed": 0},
            "meta_analysis_by_factor": {
                "fatigue": {
                    "n_studies": 8,
                    "pooled_r": 0.31,
                    "ci": [0.2, 0.42],
                    "i_squared": 80.0,
                    "publication_bias": {"publication_bias_risk": "high"},
                },
            },
        },
        "executive_summary": {"headline": "Signal found", "key_points": ["a", "b"]},
    }
    return app_data, analysis, tmp_path


def test_generate_produces_all_artifacts(staged):
    app_data, analysis, tmp_path = staged
    papers = {
        "PMC1": {
            "authors": ["Jane Roe"],
            "title": "T",
            "journal": "Nature",
            "year": 2023,
            "doi": "10.1/x",
            "source": "pmc",
        }
    }
    out = enterprise_report.generate(
        app_data,
        analysis,
        papers,
        slug="fibromyalgia",
        today="2026-06-10",
        topic="fibromyalgia",
        mesh_terms=None,
        prompts_dir=PROMPTS,
        reports_dir=tmp_path / "reports",
    )
    assert len(out["run_id"]) == 64
    assert out["qa"]["deep_success_rate"] == 100.0
    assert out["qa"]["cui_verified_pct"] == 50.0
    assert out["qa"]["n_retracted_excluded"] == 1
    assert "fatigue" in out["appendix"]  # GRADE SoF
    assert "Run Quality Certificate" in out["front_matter"]

    zip_path = Path(out["supplement_zip"])
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert {
        "run_manifest.json",
        "prisma_flow.svg",
        "references.ris",
        "references.bib",
        "extractions.csv",
        "provenance.csv",
    } <= names


def test_front_matter_has_no_hardcoded_covid(staged):
    app_data, analysis, tmp_path = staged
    out = enterprise_report.generate(
        app_data,
        analysis,
        {},
        slug="fibromyalgia",
        today="2026-06-10",
        topic="fibromyalgia",
        mesh_terms=None,
        prompts_dir=PROMPTS,
        reports_dir=tmp_path / "reports",
    )
    assert "Long COVID" not in out["front_matter"]
    assert "Long COVID" not in out["appendix"]
