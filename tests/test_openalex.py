"""Tests for the OpenAlex layer in pipeline.phase1_ingest — abstract
reconstruction, schema mapping, and the Reviewer B resume cache. Offline."""
from __future__ import annotations

import json

from pipeline import phase1_ingest as p1
from pipeline import phase3_extract as p3


# ── OpenAlex abstract reconstruction ─────────────────────────────────────

def test_reconstruct_abstract_orders_by_position():
    inv = {"Long": [0], "COVID": [1], "is": [2], "real": [3]}
    assert p1.reconstruct_abstract(inv) == "Long COVID is real"


def test_reconstruct_abstract_handles_repeated_words():
    inv = {"the": [0, 2], "big": [1], "dog": [3]}
    assert p1.reconstruct_abstract(inv) == "the big the dog"


def test_reconstruct_abstract_empty():
    assert p1.reconstruct_abstract(None) is None
    assert p1.reconstruct_abstract({}) is None


# ── OpenAlex → paper schema mapping ──────────────────────────────────────

def test_openalex_to_paper_maps_fields():
    work = {
        "id": "https://openalex.org/W123",
        "title": "A study",
        "publication_year": 2024,
        "doi": "https://doi.org/10.1/abc",
        "abstract_inverted_index": {"Fatigue": [0], "matters": [1]},
        "authorships": [{"author": {"display_name": "Jane Doe"}}],
        "primary_location": {"source": {"display_name": "J. Long COVID"}},
        "best_oa_location": {"pdf_url": "https://x/y.pdf"},
    }
    paper = p1._openalex_to_paper(work)
    assert paper["id"] == "openalex_W123"
    assert paper["source"] == "openalex"
    assert paper["doi"] == "10.1/abc"            # https prefix stripped
    assert paper["abstract"] == "Fatigue matters"
    assert paper["authors"] == ["Jane Doe"]
    assert paper["journal"] == "J. Long COVID"
    assert paper["oa_pdf_url"] == "https://x/y.pdf"


def test_openalex_to_paper_drops_when_no_abstract():
    work = {"id": "https://openalex.org/W9", "title": "No abstract", "abstract_inverted_index": None}
    assert p1._openalex_to_paper(work) is None


# ── Reviewer B resume cache (Gemini) ─────────────────────────────────────

def test_reviewer_b_cache_roundtrip_and_resume(monkeypatch, tmp_path):
    cache = tmp_path / "reviewer_b_cache.jsonl"
    monkeypatch.setattr(p3, "_reviewer_b_cache_path", lambda: cache)

    assert p3._load_reviewer_b_cache() == {}

    p3._append_reviewer_b_cache({"PMC1": {"x": 1}, "PMC2": {"y": 2}})
    loaded = p3._load_reviewer_b_cache()
    assert loaded == {"PMC1": {"x": 1}, "PMC2": {"y": 2}}

    # Appending more accumulates (resume keeps prior work).
    p3._append_reviewer_b_cache({"PMC3": {"z": 3}})
    assert set(p3._load_reviewer_b_cache()) == {"PMC1", "PMC2", "PMC3"}


def test_reviewer_b_cache_skips_corrupt_lines(monkeypatch, tmp_path):
    cache = tmp_path / "reviewer_b_cache.jsonl"
    cache.write_text(
        json.dumps({"paper_id": "PMC1", "extraction": {"ok": 1}}) + "\n"
        + "{ broken json\n"
        + json.dumps({"no_paper_id": True}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(p3, "_reviewer_b_cache_path", lambda: cache)
    assert p3._load_reviewer_b_cache() == {"PMC1": {"ok": 1}}
