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


# ── P2 Gemini sprint: circuit breakers on OpenAlex / Unpaywall ──────────────
import httpx  # noqa: E402
import pytest  # noqa: E402
import respx  # noqa: E402

from pipeline.sources import openalex as oa  # noqa: E402
from pipeline.sources import unpaywall as up  # noqa: E402
from utils import resilience  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_breakers():
    resilience.reset_all()
    yield
    resilience.reset_all()


@pytest.fixture(autouse=True)
def _isolate_unpaywall_cache(tmp_path):
    """Point the persistent DOI→url cache at a tmp file so tests never touch
    (or get polluted by) the real app-data cache."""
    up._url_cache = resilience.JsonFileCache(tmp_path / "unpaywall_cache.json")
    yield
    up._url_cache = None


@respx.mock
def test_openalex_breaker_trips_on_repeated_5xx(monkeypatch):
    monkeypatch.setattr(oa.settings, "OPENALEX_MAILTO", "x@y.z", raising=False)
    respx.get(url__regex=r".*api\.openalex\.org.*").mock(return_value=httpx.Response(503))
    out = oa.search_openalex("fibromyalgia", max_results=100)
    assert out == []
    assert resilience.breaker("openalex").status()["state"] in ("degraded", "tripped")


@respx.mock
def test_unpaywall_breaker_records_failure(monkeypatch):
    monkeypatch.setattr(up.settings, "UNPAYWALL_ENABLED", True, raising=False)
    monkeypatch.setattr(up.settings, "UNPAYWALL_EMAIL", "x@y.z", raising=False)
    respx.get(url__regex=r".*api\.unpaywall\.org.*").mock(
        return_value=httpx.Response(200, json={"best_oa_location": {"url_for_pdf": "http://pdf/x.pdf"}})
    )
    respx.get("http://pdf/x.pdf").mock(return_value=httpx.Response(500))
    assert up.fetch_fulltext_via_unpaywall("10.1/x") is None
    assert resilience.breaker("unpaywall").total_failures >= 1


def test_unpaywall_breaker_short_circuits_when_tripped(monkeypatch):
    monkeypatch.setattr(up.settings, "UNPAYWALL_ENABLED", True, raising=False)
    cb = resilience.breaker("unpaywall")
    for _ in range(cb.failure_threshold):
        cb.record_failure()
    assert cb.tripped
    # tripped breaker → returns None without any network call
    assert up.fetch_fulltext_via_unpaywall("10.1/x") is None


def test_extract_pdf_text_handles_garbage():
    assert up.extract_pdf_text(b"not a pdf") is None


# ── P2 Gemini sprint (cont.): fail-secure lookups + persistent DOI cache ────


@respx.mock
def test_unpaywall_api_outage_feeds_breaker_and_is_not_cached(monkeypatch):
    """A 5xx from the Unpaywall API is a degradation, not a 'no OA': it must
    count as a breaker failure and must never be cached as a definitive miss."""
    monkeypatch.setattr(up.settings, "UNPAYWALL_ENABLED", True, raising=False)
    monkeypatch.setattr(up.settings, "UNPAYWALL_EMAIL", "x@y.z", raising=False)
    respx.get(url__regex=r".*api\.unpaywall\.org.*").mock(return_value=httpx.Response(503))
    assert up.fetch_fulltext_via_unpaywall("10.1/down") is None
    assert resilience.breaker("unpaywall").total_failures >= 1
    assert "10.1/down" not in (up._cache().get(up._CACHE_KEY) or {})


@respx.mock
def test_unpaywall_definitive_no_oa_is_cached_and_skips_network(monkeypatch):
    monkeypatch.setattr(up.settings, "UNPAYWALL_ENABLED", True, raising=False)
    monkeypatch.setattr(up.settings, "UNPAYWALL_EMAIL", "x@y.z", raising=False)
    route = respx.get(url__regex=r".*api\.unpaywall\.org.*").mock(
        return_value=httpx.Response(200, json={"best_oa_location": None})
    )
    assert up.fetch_fulltext_via_unpaywall("10.1/nooa") is None
    assert up.fetch_fulltext_via_unpaywall("10.1/nooa") is None  # served from cache
    assert route.call_count == 1
    assert (up._cache().get(up._CACHE_KEY) or {}).get("10.1/nooa") is None
    assert "10.1/nooa" in (up._cache().get(up._CACHE_KEY) or {})
    # A definitive answer is a healthy API: no breaker failures.
    assert resilience.breaker("unpaywall").total_failures == 0


@respx.mock
def test_unpaywall_unknown_doi_404_is_definitive_miss(monkeypatch):
    monkeypatch.setattr(up.settings, "UNPAYWALL_ENABLED", True, raising=False)
    monkeypatch.setattr(up.settings, "UNPAYWALL_EMAIL", "x@y.z", raising=False)
    respx.get(url__regex=r".*api\.unpaywall\.org.*").mock(return_value=httpx.Response(404))
    assert up.fetch_fulltext_via_unpaywall("10.1/unknown") is None
    assert "10.1/unknown" in (up._cache().get(up._CACHE_KEY) or {})
    assert resilience.breaker("unpaywall").total_failures == 0


@respx.mock
def test_unpaywall_url_cache_persists_to_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(up.settings, "UNPAYWALL_ENABLED", True, raising=False)
    monkeypatch.setattr(up.settings, "UNPAYWALL_EMAIL", "x@y.z", raising=False)
    up._url_cache = resilience.JsonFileCache(tmp_path / "u.json")
    respx.get(url__regex=r".*api\.unpaywall\.org.*").mock(
        return_value=httpx.Response(200, json={"best_oa_location": {"url_for_pdf": "http://pdf/a.pdf"}})
    )
    respx.get("http://pdf/a.pdf").mock(return_value=httpx.Response(200, content=b"%PDF-fake"))
    up.fetch_fulltext_via_unpaywall("10.1/a")
    # Reload from disk: the resolved url survives across runs.
    reloaded = resilience.JsonFileCache(tmp_path / "u.json")
    assert (reloaded.get(up._CACHE_KEY) or {}).get("10.1/a") == "http://pdf/a.pdf"
