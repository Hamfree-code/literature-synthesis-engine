"""Hardening — circuit breaker, persistent cache, and the retraction 3-state
distinction (the safety-critical one: a down Crossref must not read as 'clean')."""

from __future__ import annotations

import httpx
import pytest
import respx

from utils import resilience
from utils.resilience import CircuitBreaker, JsonFileCache, breaker, degraded_services, reset_all


@pytest.fixture(autouse=True)
def _reset():
    reset_all()
    yield
    reset_all()


def test_breaker_trips_after_threshold():
    cb = CircuitBreaker("svc", failure_threshold=3)
    assert cb.allow()
    cb.record_failure()
    cb.record_failure()
    assert cb.allow()  # 2 < 3
    cb.record_failure()
    assert not cb.allow()  # tripped
    assert cb.status()["state"] == "tripped"


def test_breaker_success_resets_consecutive():
    cb = CircuitBreaker("svc", failure_threshold=2)
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    assert cb.allow()  # consecutive count reset by the success
    assert cb.status()["state"] == "degraded"  # but failures were seen


def test_registry_and_degraded_services():
    breaker("a").record_success()
    b = breaker("b", failure_threshold=1)
    b.record_failure()
    assert "b" in degraded_services()
    assert "a" not in degraded_services()
    assert set(resilience.health_report().keys()) == {"a", "b"}


def test_json_file_cache_persists(tmp_path):
    p = tmp_path / "cache.json"
    c = JsonFileCache(p)
    assert "k" not in c
    c.set("k", {"v": 1})
    c.save()
    c2 = JsonFileCache(p)  # reload from disk
    assert "k" in c2
    assert c2.get("k") == {"v": 1}


def test_json_file_cache_tolerates_corrupt_file(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    c = JsonFileCache(p)  # must not raise
    assert len(c) == 0


# ── Retraction 3-state (safety-critical) ────────────────────────────────────
from utils.retraction import check_retraction_status  # noqa: E402


@respx.mock
def test_retraction_status_clean():
    respx.get(url__regex=r".*/works/.*").mock(
        return_value=httpx.Response(200, json={"message": {"type": "journal-article", "title": ["Fine"]}})
    )
    with httpx.Client() as c:
        status, info = check_retraction_status("10.1/ok", client=c)
    assert status == "clean" and info is None


@respx.mock
def test_retraction_status_retracted():
    respx.get(url__regex=r".*/works/.*").mock(
        return_value=httpx.Response(200, json={"message": {"type": "retraction", "DOI": "10.1/notice"}})
    )
    with httpx.Client() as c:
        status, info = check_retraction_status("10.1/bad", client=c)
    assert status == "retracted" and info["is_retracted"] is True


@respx.mock
def test_retraction_status_error_is_not_clean():
    # Crossref down → 503. MUST surface as 'error', never 'clean'.
    respx.get(url__regex=r".*/works/.*").mock(return_value=httpx.Response(503))
    with httpx.Client() as c:
        status, info = check_retraction_status("10.1/x", client=c)
    assert status == "error"
    assert status != "clean"  # the whole point


@respx.mock
def test_retraction_status_network_error_is_error():
    respx.get(url__regex=r".*/works/.*").mock(side_effect=httpx.ConnectError("down"))
    with httpx.Client() as c:
        status, _ = check_retraction_status("10.1/x", client=c)
    assert status == "error"


def test_qa_retraction_label_incomplete():
    from utils.report_builders import _retraction_label

    assert "INCOMPLETE" in _retraction_label(
        {"retraction_screen_complete": False, "retraction_checks_failed": 4}
    )
    assert "complete" in _retraction_label({"retraction_screen_complete": True})
    assert _retraction_label({}) == "not run"
