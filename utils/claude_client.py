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
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": system_or_prompt, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": user_content},
            ],
        }],
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
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                return None
    return None
