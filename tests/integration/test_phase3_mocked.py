"""P1 — deep extraction with mocked Anthropic batch API.

Verifies forced tool-use parsing, max_tokens compression retry, attempt logging,
and that no paper is ever lost silently.
"""

from __future__ import annotations

import json

import pytest

import pipeline.phase3_extract as p3
from config.extraction_schema import ARBITER_TOOL, EXTRACTION_TOOL
from tests.conftest import make_error_result, make_maxtokens_result, make_tool_result


@pytest.fixture
def tmp_data(tmp_path, monkeypatch, extraction_valid):
    monkeypatch.setattr(p3, "app_data", lambda rel: tmp_path / rel, raising=False)
    (tmp_path / "data/filtered").mkdir(parents=True, exist_ok=True)
    return tmp_path


class _FakeBatch:
    """Stateful fake: submit_batch records the requests; poll_batch turns them
    into crafted results according to a per-custom_id script."""

    def __init__(self, script):
        self.script = script  # dict: cid -> callable(attempt) -> result | "ok"
        self.attempt_by_cid: dict[str, int] = {}
        self.last_requests = None

    def submit_batch(self, requests):
        self.last_requests = requests
        return "batch_fake"

    def poll_batch(self, batch_id, interval_sec=30):
        results = []
        for req in self.last_requests:
            cid = req["custom_id"]
            n = self.attempt_by_cid.get(cid, 0) + 1
            self.attempt_by_cid[cid] = n
            results.append(self.script(cid, n))
        return results


def _install(monkeypatch, fake):
    monkeypatch.setattr(p3, "submit_batch", fake.submit_batch, raising=True)
    monkeypatch.setattr(p3, "poll_batch", fake.poll_batch, raising=True)


def test_single_pass_all_succeed(tmp_data, monkeypatch, extraction_valid):
    monkeypatch.setattr(p3.settings, "ARBITER_ENABLED", False, raising=False)
    papers = [{"id": "PMC1", "abstract": "a"}, {"id": "PMC2", "abstract": "b"}]

    def script(cid, attempt):
        return make_tool_result(cid, extraction_valid, EXTRACTION_TOOL["name"])

    _install(monkeypatch, _FakeBatch(script))
    p3._run_single_pass(papers)

    results = [json.loads(line) for line in (tmp_data / "data/filtered/deep_results.jsonl").open()]
    assert {r["paper_id"] for r in results} == {"PMC1", "PMC2"}
    attempts = [json.loads(line) for line in (tmp_data / "data/filtered/extraction_attempts.jsonl").open()]
    assert all(a["parse_ok"] for a in attempts)


def test_max_tokens_then_recovers_on_retry(tmp_data, monkeypatch, extraction_valid):
    monkeypatch.setattr(p3.settings, "ARBITER_ENABLED", False, raising=False)
    monkeypatch.setattr(p3.settings, "DEEP_MAX_RETRIES", 2, raising=False)
    papers = [{"id": "PMC9", "abstract": "x"}]

    def script(cid, attempt):
        if attempt == 1:
            return make_maxtokens_result(cid)  # truncated first
        return make_tool_result(cid, extraction_valid, EXTRACTION_TOOL["name"])

    _install(monkeypatch, _FakeBatch(script))
    p3._run_single_pass(papers)

    results = [json.loads(line) for line in (tmp_data / "data/filtered/deep_results.jsonl").open()]
    assert results[0]["paper_id"] == "PMC9"
    attempts = [json.loads(line) for line in (tmp_data / "data/filtered/extraction_attempts.jsonl").open()]
    assert len(attempts) == 2  # one max_tokens, one success
    assert attempts[0]["stop_reason"] == "max_tokens"
    assert attempts[-1]["parse_ok"] is True


def test_persistent_failure_is_recorded_not_lost(tmp_data, monkeypatch):
    monkeypatch.setattr(p3.settings, "ARBITER_ENABLED", False, raising=False)
    papers = [{"id": "PMCERR", "abstract": "x"}]

    def script(cid, attempt):
        return make_error_result(cid, "overloaded")

    _install(monkeypatch, _FakeBatch(script))
    p3._run_single_pass(papers)

    fail_path = tmp_data / "data/filtered/deep_failures.jsonl"
    failures = [json.loads(line) for line in fail_path.open()]
    assert failures[0]["paper_id"] == "PMCERR"
    assert "api_error" in failures[0]["reason"]
    # never silently dropped: not present in results
    assert not (tmp_data / "data/filtered/deep_results.jsonl").read_text().strip()


def test_arbiter_pass_reconciles_two_reviewers(tmp_data, monkeypatch, extraction_valid):
    monkeypatch.setattr(p3.settings, "ARBITER_ENABLED", True, raising=False)
    papers = [{"id": "PMC5", "abstract": "y"}]

    arb_payload = dict(extraction_valid)
    arb_payload["reconciliation_triggered"] = True
    arb_payload["arbiter_notes"] = "resolved disagreement on sample size"

    def script(cid, attempt):
        if cid.endswith("__arb"):
            return make_tool_result(cid, arb_payload, ARBITER_TOOL["name"])
        return make_tool_result(cid, extraction_valid, EXTRACTION_TOOL["name"])

    _install(monkeypatch, _FakeBatch(script))
    p3._run_arbiter_pass(papers)

    results = [json.loads(line) for line in (tmp_data / "data/filtered/deep_results.jsonl").open()]
    assert results[0]["paper_id"] == "PMC5"
    assert results[0]["reconciliation_triggered"] is True
    assert results[0]["reviewer_a_raw"] is not None
    assert results[0]["reviewer_b_raw"] is not None


def test_extraction_schema_validates_fixture(extraction_valid):
    from config.extraction_schema import validate_extraction

    assert validate_extraction(extraction_valid) == []
    assert validate_extraction({"foo": 1})  # missing required blocks → problems
