"""P2 — UMLS verification (mocked) + Crossref retraction parsing + query filter."""

from __future__ import annotations

import httpx
import pytest
import respx

from utils import umls_client
from utils.retraction import parse_crossref_message


# ── Retraction parsing (pure, no network) ──────────────────────────────────
def test_retraction_via_type():
    msg = {"type": "retraction", "DOI": "10.1/x", "issued": {"date-parts": [[2021, 5, 1]]}}
    out = parse_crossref_message(msg)
    assert out["is_retracted"] is True
    assert out["retraction_date"] == "2021-5-1"


def test_retraction_via_relation():
    msg = {"relation": {"is-retracted-by": [{"id": "10.1/notice"}]}}
    out = parse_crossref_message(msg)
    assert out["is_retracted"] is True
    assert out["retraction_doi"] == "10.1/notice"


def test_retraction_via_title_prefix():
    msg = {"title": ["RETRACTED: A flawed study"]}
    assert parse_crossref_message(msg)["is_retracted"] is True


def test_not_retracted_returns_none():
    msg = {"type": "journal-article", "title": ["A perfectly fine paper"]}
    assert parse_crossref_message(msg) is None


# ── PubMed query excludes retracted by default ─────────────────────────────
def test_build_query_excludes_retracted(monkeypatch):
    from pipeline import phase1_ingest

    monkeypatch.setattr(phase1_ingest.settings, "INCLUDE_RETRACTED", False, raising=False)
    q = phase1_ingest.build_query(topic="fibromyalgia")
    assert 'NOT "Retracted Publication"[Publication Type]' in q


def test_build_query_can_include_retracted(monkeypatch):
    from pipeline import phase1_ingest

    monkeypatch.setattr(phase1_ingest.settings, "INCLUDE_RETRACTED", True, raising=False)
    q = phase1_ingest.build_query(topic="fibromyalgia")
    assert "Retracted Publication" not in q


# ── UMLS verification (mocked REST) ────────────────────────────────────────
@pytest.fixture(autouse=True)
def _clear_cache():
    umls_client._CACHE.clear()
    yield
    umls_client._CACHE.clear()


@respx.mock
def test_verify_entity_confirms_matching_cui(monkeypatch):
    monkeypatch.setattr(umls_client.settings, "UMLS_API_KEY", "k", raising=False)
    monkeypatch.setattr(umls_client.settings, "UMLS_VERIFY_ENABLED", True, raising=False)
    respx.get(url__regex=r".*/CUI/C0015672").mock(
        return_value=httpx.Response(200, json={"result": {"name": "Fatigue"}})
    )
    ent = {"verbatim_text": "fatigue", "umls_cui": "C0015672", "mesh_heading": "Fatigue"}
    with httpx.Client() as c:
        out = umls_client.verify_entity(c, ent)
    assert out["cui_verified"] is True
    assert out["preferred_name"] == "Fatigue"


@respx.mock
def test_verify_entity_falls_back_to_search(monkeypatch):
    monkeypatch.setattr(umls_client.settings, "UMLS_API_KEY", "k", raising=False)
    monkeypatch.setattr(umls_client.settings, "UMLS_VERIFY_ENABLED", True, raising=False)
    respx.get(url__regex=r".*/CUI/C9999999").mock(return_value=httpx.Response(404))
    respx.get(url__regex=r".*/search/current").mock(
        return_value=httpx.Response(
            200, json={"result": {"results": [{"ui": "C0015672", "name": "Fatigue"}]}}
        )
    )
    ent = {"verbatim_text": "fatigue", "umls_cui": "C9999999", "mesh_heading": "Fatigue"}
    with httpx.Client() as c:
        out = umls_client.verify_entity(c, ent)
    assert out["cui_verified"] is True
    assert out["umls_cui"] == "C0015672"
    assert out["llm_judgment"] is False


def test_offline_mode_is_noop(monkeypatch):
    monkeypatch.setattr(umls_client.settings, "UMLS_API_KEY", "", raising=False)
    ents = [{"verbatim_text": "fatigue", "umls_cui": "C1", "mesh_heading": "Fatigue"}]
    out = umls_client.verify_entities(ents)
    assert out[0].get("cui_verified", False) is False


def test_verification_rate():
    ents = [{"cui_verified": True}, {"cui_verified": False}, {"cui_verified": True}, {"cui_verified": False}]
    assert umls_client.verification_rate(ents) == 50.0
