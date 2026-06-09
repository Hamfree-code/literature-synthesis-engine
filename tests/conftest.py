"""Shared pytest fixtures (UPGRADE v3.1 — P0).

No real network calls in unit/integration: Anthropic, NCBI, UMLS, OpenAlex and
Crossref are all mocked. Dummy credentials are injected into the environment
*before* ``config.settings`` is imported anywhere, because Settings declares
ANTHROPIC_API_KEY as required.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Inject dummy credentials so `from config.settings import settings` never fails
# at import time in CI. Real values are never needed for non-live tests.
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-dummy")
os.environ.setdefault("NCBI_API_KEY", "test")
os.environ.setdefault("NCBI_EMAIL", "test@example.com")
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_KEY", "test")

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def pmc_full_xml() -> bytes:
    return (FIXTURES / "pmc_sample_full.xml").read_bytes()


@pytest.fixture
def pmc_messy_xml() -> bytes:
    return (FIXTURES / "pmc_sample_messy.xml").read_bytes()


@pytest.fixture
def extraction_valid() -> dict:
    import json

    return json.loads((FIXTURES / "extraction_valid.json").read_text(encoding="utf-8"))


class _FakeContentBlock:
    """Mimics an Anthropic content block (text or tool_use)."""

    def __init__(self, *, type: str, text: str = "", name: str = "", input: dict | None = None):
        self.type = type
        self.text = text
        self.name = name
        self.input = input


class _FakeUsage:
    def __init__(self, output_tokens: int = 1000):
        self.output_tokens = output_tokens
        self.input_tokens = 2000


class FakeMessage:
    """Mimics an Anthropic Message returned inside a batch result."""

    def __init__(self, *, content: list, stop_reason: str = "tool_use", output_tokens: int = 1000):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = _FakeUsage(output_tokens)


class _FakeResult:
    def __init__(self, type: str, message: FakeMessage | None = None, error=None):
        self.type = type
        self.message = message
        self.error = error


class FakeBatchResult:
    """Mimics one entry from client.messages.batches.results()."""

    def __init__(self, custom_id: str, result: _FakeResult):
        self.custom_id = custom_id
        self.result = result


def make_tool_result(
    custom_id: str,
    payload: dict,
    tool_name: str = "submit_extraction",
    stop_reason: str = "tool_use",
    output_tokens: int = 1200,
) -> FakeBatchResult:
    block = _FakeContentBlock(type="tool_use", name=tool_name, input=payload)
    msg = FakeMessage(content=[block], stop_reason=stop_reason, output_tokens=output_tokens)
    return FakeBatchResult(custom_id, _FakeResult("succeeded", msg))


def make_text_result(custom_id: str, text: str, stop_reason: str = "end_turn") -> FakeBatchResult:
    block = _FakeContentBlock(type="text", text=text)
    msg = FakeMessage(content=[block], stop_reason=stop_reason)
    return FakeBatchResult(custom_id, _FakeResult("succeeded", msg))


def make_maxtokens_result(custom_id: str) -> FakeBatchResult:
    msg = FakeMessage(content=[_FakeContentBlock(type="text", text="{partial")], stop_reason="max_tokens")
    return FakeBatchResult(custom_id, _FakeResult("succeeded", msg))


def make_error_result(custom_id: str, message: str = "rate_limited") -> FakeBatchResult:
    class _Err:
        def __init__(self, m):
            self.message = m

    return FakeBatchResult(custom_id, _FakeResult("errored", message=None, error=_Err(message)))
