"""Anthropic API wrapper with retries, caching, and batch support."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import anthropic
from rich.console import Console
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings

console = Console()
client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


# ── Batch registry ───────────────────────────────────────────────────────
# A submitted Message Batch is money already spent. We persist each batch id
# keyed by a caller-supplied label so that if the process dies while polling
# (or during a later phase), a re-run can RESUME the existing batch instead of
# resubmitting and paying twice. The registry lives under data/checkpoints, so
# the topic-change guard wipes it together with the other run state.

def _registry_path() -> Path:
    from app_paths import app_data
    p = app_data("data/checkpoints/batches.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_registry() -> dict:
    path = _registry_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_registry(reg: dict) -> None:
    try:
        _registry_path().write_text(json.dumps(reg, indent=2), encoding="utf-8")
    except OSError as e:
        console.print(f"[yellow]Could not persist batch registry: {e}[/]")


def remembered_batch(label: str) -> str | None:
    """Return a previously-submitted, not-yet-consumed batch id for *label*."""
    return _load_registry().get(label)


def forget_batch(label: str) -> None:
    """Drop a label from the registry once its results have been consumed."""
    reg = _load_registry()
    if label in reg:
        del reg[label]
        _save_registry(reg)


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


@retry(stop=stop_after_attempt(5), wait=wait_exponential(min=2, max=60))
def _create_batch(requests: list[dict]):
    return client.messages.batches.create(requests=requests)


def submit_batch(requests: list[dict], *, label: str | None = None) -> str:
    """Submit a Message Batch and return the batch ID.

    When *label* is given, an already-submitted batch for that label is reused
    instead of creating a new (paid) one — this makes a crashed/retried phase
    idempotent. The id is persisted before returning so it survives a crash
    between submission and polling.
    """
    if label:
        existing = remembered_batch(label)
        if existing:
            console.print(f"[cyan]Resuming existing batch for '{label}': {existing}[/]")
            return existing

    batch = _create_batch(requests)
    console.print(f"Batch submitted: {batch.id}")
    if label:
        reg = _load_registry()
        reg[label] = batch.id
        _save_registry(reg)
    return batch.id


@retry(stop=stop_after_attempt(5), wait=wait_exponential(min=2, max=60))
def _retrieve_batch(batch_id: str):
    """Wrapped so a transient network blip while polling does not throw away a
    batch we have already paid for."""
    return client.messages.batches.retrieve(batch_id)


@retry(stop=stop_after_attempt(5), wait=wait_exponential(min=2, max=60))
def _fetch_results(batch_id: str) -> list:
    return list(client.messages.batches.results(batch_id))


def poll_batch(batch_id: str, interval_sec: int = 30) -> list:
    """Block until the batch reaches the terminal 'ended' status, then return
    results. Polling is bounded by settings.BATCH_MAX_POLL_HOURS and every API
    call is retried, so transient errors don't discard a paid batch."""
    deadline = time.monotonic() + settings.BATCH_MAX_POLL_HOURS * 3600
    while True:
        batch = _retrieve_batch(batch_id)
        if batch.processing_status == "ended":
            break
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"Batch {batch_id} still '{batch.processing_status}' after "
                f"{settings.BATCH_MAX_POLL_HOURS}h. Its id is persisted; re-run to resume."
            )
        console.print(f"  Batch {batch_id}: {batch.processing_status}")
        time.sleep(interval_sec)
    return _fetch_results(batch_id)


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
