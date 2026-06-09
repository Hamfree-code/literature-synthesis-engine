"""Anthropic API wrapper with retries, caching, and batch support."""

from __future__ import annotations

import json
import re
import time

import anthropic
from rich.console import Console
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings

console = Console()
client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def sanitize_custom_id(paper_id: str) -> str:
    """Anthropic Batch API requires custom_id matching ^[a-zA-Z0-9_-]{1,64}$."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", paper_id)[:64]


@retry(stop=stop_after_attempt(5), wait=wait_exponential(min=2, max=60))
def call_with_cache(
    model: str,
    system_or_prompt: str,
    user_content: str,
    max_tokens: int = 1024,
) -> str:
    """Single synchronous call with prompt caching on the static portion."""
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": system_or_prompt, "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": user_content},
                ],
            }
        ],
    )
    return response.content[0].text


def submit_batch(requests: list[dict]) -> str:
    """Submit a Message Batch and return the batch ID."""
    batch = client.messages.batches.create(requests=requests)
    console.print(f"Batch submitted: {batch.id}")
    return batch.id


def poll_batch(batch_id: str, interval_sec: int = 30) -> list:
    """Block until batch completes, return results."""
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            break
        console.print(f"  Batch {batch_id}: {batch.processing_status}")
        time.sleep(interval_sec)
    return list(client.messages.batches.results(batch_id))


def parse_json_response(raw: str) -> dict | None:
    """Strict JSON parse with one repair attempt for trailing prose."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def parse_batch_message(message, tool_name: str | None = None) -> dict | None:
    """Extract the structured payload from one Anthropic message.

    UPGRADE v3.1 — P1. When *tool_name* is given, looks for a forced tool_use
    block and returns its ``input`` dict (cannot be malformed by construction).
    Otherwise falls back to JSON-parsing the first text block. Returns None when
    nothing parseable is present.
    """
    content = getattr(message, "content", None) or []
    if tool_name:
        for block in content:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool_name:
                inp = getattr(block, "input", None)
                if isinstance(inp, dict):
                    return inp
        # tool was forced but absent → fall through to any text we can salvage
    for block in content:
        if getattr(block, "type", None) == "text":
            parsed = parse_json_response(getattr(block, "text", "") or "")
            if parsed is not None:
                return parsed
    return None


def message_stop_reason(message) -> str | None:
    return getattr(message, "stop_reason", None)


def message_output_tokens(message) -> int | None:
    usage = getattr(message, "usage", None)
    return getattr(usage, "output_tokens", None) if usage else None


def repair_json_to_schema(raw_text: str, model: str) -> dict | None:
    """Last-resort cheap repair: ask Haiku to coerce broken text into JSON.

    UPGRADE v3.1 — P1.3. Only reached if forced tool-use somehow failed; returns
    None on any error so the caller can mark the paper extraction_failed rather
    than lose it silently.
    """
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=8192,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Repair the following into a single valid JSON object. Output ONLY "
                        "the JSON, no prose, no markdown fences.\n\n" + raw_text[:60000]
                    ),
                }
            ],
        )
        return parse_json_response(resp.content[0].text)
    except Exception:
        return None
