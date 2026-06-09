"""P4 — OpenAlex parsing/dedup + Unpaywall PDF extraction (pure functions)."""

from __future__ import annotations

from pipeline.sources.openalex import (
    dedup_against,
    normalize_doi,
    parse_work,
    reconstruct_abstract,
)


def test_normalize_doi_strips_prefix_and_case():
    assert normalize_doi("https://doi.org/10.1/AbC") == "10.1/abc"
    assert normalize_doi("doi:10.2/x") == "10.2/x"
    assert normalize_doi(None) == ""


def test_reconstruct_abstract_from_inverted_index():
    inv = {"Long": [0], "COVID": [1], "is": [2], "persistent": [3]}
    assert reconstruct_abstract(inv) == "Long COVID is persistent"
    assert reconstruct_abstract(None) is None


def test_parse_work_journal_article():
    work = {
        "title": "A study",
        "abstract_inverted_index": {"hello": [0], "world": [1]},
        "publication_year": 2023,
        "doi": "https://doi.org/10.1/AA",
        "type": "article",
        "authorships": [{"author": {"display_name": "Jane Roe"}}],
        "primary_location": {"source": {"display_name": "Nature"}, "landing_page_url": "http://x"},
        "ids": {},
    }
    p = parse_work(work)
    assert p["source"] == "openalex"
    assert p["doi"] == "10.1/aa"
    assert p["authors"] == ["Jane Roe"]
    assert p["abstract"] == "hello world"


def test_parse_work_preprint_tagged_medrxiv():
    work = {
        "title": "Preprint",
        "abstract_inverted_index": {"a": [0]},
        "type": "preprint",
        "doi": "10.1101/2023.01",
        "authorships": [],
        "primary_location": {"source": {"display_name": "medRxiv"}},
        "ids": {},
    }
    assert parse_work(work)["source"] == "medrxiv"


def test_parse_work_with_pmcid_merges_to_pmc_id():
    work = {
        "title": "x",
        "abstract_inverted_index": {"a": [0]},
        "type": "article",
        "doi": "10.1/x",
        "authorships": [],
        "primary_location": {},
        "ids": {"pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123456"},
    }
    assert parse_work(work)["id"] == "PMC123456"


def test_parse_work_without_abstract_returns_none():
    assert parse_work({"title": "x", "abstract_inverted_index": None}) is None


def test_dedup_pmc_wins():
    existing = {"10.1/dup"}
    new = [
        {"doi": "10.1/dup", "title": "duplicate"},
        {"doi": "10.1/fresh", "title": "fresh"},
        {"doi": "10.1/fresh", "title": "fresh-again"},  # intra-batch dup
    ]
    kept = dedup_against(new, existing)
    titles = [p["title"] for p in kept]
    assert titles == ["fresh"]
