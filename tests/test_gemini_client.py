"""Tests for utils.gemini_client — JSON parsing reuse, async concurrency, and
failure capture. No network: the underlying async call is monkeypatched."""
from __future__ import annotations

import asyncio

import pytest

from utils import gemini_client as gc


def test_parse_json_response_is_reused():
    # Single source of truth — same lenient parser as claude_client.
    from utils.claude_client import parse_json_response
    assert gc.parse_json_response is parse_json_response


def test_gather_json_happy_path(monkeypatch):
    async def fake(model, prompt, max_tokens, temperature):
        return '{"ok": true, "echo": "' + prompt + '"}'
    monkeypatch.setattr(gc, "_generate_json_async", fake)

    prompts = [("p1", "alpha"), ("p2", "beta")]
    results, failures = asyncio.run(gc.gather_json("m", prompts, concurrency=2))
    assert failures == []
    assert results["p1"] == {"ok": True, "echo": "alpha"}
    assert set(results) == {"p1", "p2"}


def test_gather_json_captures_parse_failure(monkeypatch):
    async def fake(model, prompt, max_tokens, temperature):
        return "this is not json"
    monkeypatch.setattr(gc, "_generate_json_async", fake)

    results, failures = asyncio.run(gc.gather_json("m", [("p1", "x")]))
    assert results == {}
    assert failures[0]["key"] == "p1"
    assert failures[0]["reason"] == "json_parse_failed"


def test_gather_json_captures_api_error(monkeypatch):
    async def boom(model, prompt, max_tokens, temperature):
        raise RuntimeError("429 rate limited")
    monkeypatch.setattr(gc, "_generate_json_async", boom)

    results, failures = asyncio.run(gc.gather_json("m", [("p1", "x")]))
    assert results == {}
    assert failures[0]["reason"] == "api_error"
    assert "429" in failures[0]["detail"]


def test_gather_json_mixed(monkeypatch):
    async def fake(model, prompt, max_tokens, temperature):
        if prompt == "bad":
            return "nope"
        return '{"v": 1}'
    monkeypatch.setattr(gc, "_generate_json_async", fake)

    results, failures = asyncio.run(
        gc.gather_json("m", [("good", "ok"), ("bad", "bad")], concurrency=4)
    )
    assert results == {"good": {"v": 1}}
    assert [f["key"] for f in failures] == ["bad"]
