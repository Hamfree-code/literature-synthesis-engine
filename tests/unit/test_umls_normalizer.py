"""P2 — UMLS normalization entity collection + tool-call mapping (mocked)."""

from __future__ import annotations

from utils import umls_normalizer as un
from tests.conftest import _FakeContentBlock


def test_collect_entities_dedups_and_types():
    extraction = {
        "factual_extraction": {
            "symptoms_prevalence": {"Fatigue": "0.4", "fatigue": "0.4", "brain fog": "0.3"},
            "biomarker_findings": {"CRP": "high"},
            "risk_factors_quantified": [{"factor": "female sex"}],
        },
        "phenotype_mapping": {
            "primary_mechanism": "autoimmunity",
            "secondary_mechanisms": ["vascular_endothelial", {"mechanism": "viral_reservoir"}],
        },
    }
    ents = un._collect_entities(extraction)
    types = {e["entity_type"] for e in ents}
    assert {"phenotype", "biomarker", "risk_factor", "mechanism"} <= types
    # case-insensitive dedup on (type, verbatim)
    fatigue = [e for e in ents if e["verbatim_text"].lower() == "fatigue"]
    assert len(fatigue) == 1


def test_normalize_extraction_maps_via_tool(monkeypatch):
    class _Resp:
        content = [
            _FakeContentBlock(
                type="tool_use",
                name="normalize_biomedical_entities",
                input={
                    "normalized_entities": [
                        {
                            "verbatim_text": "fatigue",
                            "entity_type": "phenotype",
                            "umls_cui": "C0015672",
                            "mesh_heading": "Fatigue",
                        },
                    ]
                },
            )
        ]

    monkeypatch.setattr(un._client.messages, "create", lambda **kw: _Resp(), raising=False)
    extraction = {"factual_extraction": {"symptoms_prevalence": {"fatigue": "0.4"}}}
    out = un.normalize_extraction(extraction)
    assert out[0]["umls_cui"] == "C0015672"
    assert out[0]["llm_judgment"] is True  # CUI is LLM-inferred until verified


def test_normalize_extraction_empty_when_no_entities():
    assert un.normalize_extraction({"factual_extraction": {}}) == []


def test_normalize_extraction_handles_api_error(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("api down")

    monkeypatch.setattr(un._client.messages, "create", _boom, raising=False)
    out = un.normalize_extraction({"factual_extraction": {"symptoms_prevalence": {"x": "0.1"}}})
    assert out == []  # best-effort: failure returns empty, never raises
