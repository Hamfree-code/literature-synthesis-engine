"""Gemini API wrapper — the Gemini half of the multi-provider extraction engine.

Triage (Gemini Flash) and deep-extraction Reviewer B (Gemini Pro) run through
here. Reviewer A stays on Claude Sonnet and the arbiter on Claude Opus via
utils.claude_client.

Design notes:
- We reuse `parse_json_response` from claude_client so JSON handling is
  identical across providers (single source of truth).
- We request `response_mime_type="application/json"` rather than a strict
  `response_schema`. Gemini rejects deeply-nested schemas, and the extraction
  prompts already specify the JSON shape in text — so this sidesteps the
  schema-limit problem entirely.
- Unlike Anthropic's Batch API, Gemini work runs as concurrency-bounded async
  calls (`gather_json`). Each call is retried so a transient blip doesn't drop
  a result; callers persist results to a resume cache themselves.
"""
from __future__ import annotations

import asyncio

from google import genai
from google.genai import types
from rich.console import Console
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from utils.claude_client import parse_json_response  # reused — single source of truth

console = Console()

_client: genai.Client | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


def _config(model: str, max_tokens: int, temperature: float | None) -> types.GenerateContentConfig:
    # Thinking handling is model-dependent and the two families are opposites:
    #  - Flash (triage) silently spends the whole output budget on hidden
    #    reasoning, truncating the JSON (finish=MAX_TOKENS) — a 96% parse drop.
    #    It must run with thinking_budget=0 so the full budget goes to output.
    #  - Pro (Reviewer B) *requires* thinking ("Budget 0 is invalid. This model
    #    only works in thinking mode."), so it gets dynamic thinking (-1) plus a
    #    generous output cap so reasoning tokens don't starve the JSON payload.
    is_flash = "flash" in model.lower()
    kwargs: dict = {
        "max_output_tokens": max_tokens,
        "response_mime_type": "application/json",
        "thinking_config": types.ThinkingConfig(thinking_budget=0 if is_flash else -1),
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    return types.GenerateContentConfig(**kwargs)


@retry(stop=stop_after_attempt(5), wait=wait_exponential(min=2, max=60))
def generate_json(model: str, prompt: str, *, max_tokens: int = 4096,
                  temperature: float | None = None) -> str:
    """Single synchronous Gemini call returning raw text (JSON-shaped)."""
    resp = client().models.generate_content(
        model=model, contents=prompt, config=_config(model, max_tokens, temperature)
    )
    return resp.text or ""


@retry(stop=stop_after_attempt(5), wait=wait_exponential(min=2, max=60))
async def _generate_json_async(model: str, prompt: str, max_tokens: int,
                               temperature: float | None) -> str:
    resp = await client().aio.models.generate_content(
        model=model, contents=prompt, config=_config(model, max_tokens, temperature)
    )
    return resp.text or ""


async def gather_json(
    model: str,
    prompts: list[tuple[str, str]],
    *,
    max_tokens: int = 4096,
    temperature: float | None = None,
    concurrency: int | None = None,
) -> tuple[dict[str, dict], list[dict]]:
    """Run many Gemini calls concurrently (bounded by GEMINI_CONCURRENCY).

    `prompts` is a list of (key, prompt) — key is typically a paper_id. Returns
    (parsed_by_key, failures) where parsed_by_key maps key -> parsed JSON dict
    and failures is a list of {key, reason, detail}. Mirrors the failure shape
    of claude_client / phase3's `_parse_batch_results`.
    """
    sem = asyncio.Semaphore(concurrency or settings.GEMINI_CONCURRENCY)
    results: dict[str, dict] = {}
    failures: list[dict] = []

    async def _one(key: str, prompt: str) -> None:
        async with sem:
            try:
                raw = await _generate_json_async(model, prompt, max_tokens, temperature)
            except Exception as e:
                failures.append({"key": key, "reason": "api_error",
                                 "detail": f"{type(e).__name__}: {e}"[:300]})
                return
        parsed = parse_json_response(raw)
        if parsed is None:
            failures.append({"key": key, "reason": "json_parse_failed",
                             "detail": f"raw len={len(raw)} prefix={raw[:200]!r}"})
            return
        results[key] = parsed

    await asyncio.gather(*(_one(k, p) for k, p in prompts))
    return results, failures
