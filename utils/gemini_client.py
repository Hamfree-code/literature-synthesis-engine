"""Google Gemini Batch API wrapper — Reviewer B in the two-step deep extraction.

Mirrors only the slice of ``utils/claude_client`` the pipeline relies on (submit
a batch, poll to completion, hand back per-paper text) so ``phase3`` can route
Reviewer B to Gemini Flash. The point is genuine cross-model diversity: a
Sonnet reviewer and a Gemini reviewer fail on *different* papers, so the Opus
arbiter has decorrelated evidence to reconcile, unlike two same-family Sonnet
reviewers that share their blind spots.

Design rules, consistent with the rest of the engine:
  - **Fail-secure.** A degraded or unreachable Gemini batch trips a circuit
    breaker and is surfaced via ``degraded_services``; papers are recorded as
    failures, never silently dropped or downgraded to an abstract.
  - **No hard dependency at import time.** ``google.genai`` is imported only
    inside :func:`_get_client`, so a pipeline with Gemini disabled (or without
    the optional ``google-genai`` package) imports and runs unchanged. Every
    other function here is pure dict manipulation — the Batch API accepts plain
    ``InlinedRequestDict`` / ``GenerateContentConfigDict`` dicts, so request
    construction needs no SDK types and stays unit-testable with a fake client.

Gemini's structured-output ``response_schema`` is an OpenAPI subset that can't
express the extraction schema's union types (``["boolean","string"]``,
``[...,"null"]``), so we ask for ``response_mime_type='application/json'`` plus
the schema-as-instructions prompt and let the caller normalise the JSON through
the same Haiku repair pass the Anthropic reviewers use. That keeps Reviewer B's
output shape identical to Reviewer A's with no brittle schema translation.
"""

from __future__ import annotations

import time

from config.settings import settings

# google.genai JobState enum names (see types.JobState).
_STATE_OK = "JOB_STATE_SUCCEEDED"
_STATE_PARTIAL = "JOB_STATE_PARTIALLY_SUCCEEDED"
_STATES_TERMINAL_BAD = {
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
}

# Module-level client, created lazily and cached. Tests monkeypatch _get_client.
_client = None


class GeminiBatchError(RuntimeError):
    """Raised when a Gemini batch job reaches a terminal failure state."""


def gemini_available() -> bool:
    """True only when a key is configured *and* the SDK is importable. Used by
    phase3 to decide whether to route Reviewer B to Gemini or fall back."""
    if not settings.GEMINI_API_KEY:
        return False
    try:
        import google.genai  # noqa: F401
    except Exception:
        return False
    return True


def _get_client():
    """Lazy, cached genai client. The only place ``google.genai`` is imported."""
    global _client
    if _client is None:
        from google import genai

        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


def build_inlined_request(
    *,
    paper_id: str,
    system_instruction: str,
    content: str,
    max_output_tokens: int,
    temperature: float,
) -> dict:
    """Build one ``InlinedRequestDict``. ``metadata.paper_id`` lets us map each
    response back to its paper without relying on response ordering."""
    return {
        "contents": content,
        "config": {
            "system_instruction": system_instruction,
            "response_mime_type": "application/json",
            "max_output_tokens": max_output_tokens,
            "temperature": temperature,
        },
        "metadata": {"paper_id": paper_id},
    }


def submit_gemini_batch(inlined_requests: list[dict], *, model: str) -> str:
    """Create an inline batch job and return its resource name (``batches/...``)."""
    job = _get_client().batches.create(model=model, src=inlined_requests)
    return job.name


def _state_name(job) -> str:
    state = getattr(job, "state", None)
    return getattr(state, "name", None) or str(state)


def poll_gemini_batch(job_name: str, *, interval_sec: int = 30) -> list[dict]:
    """Block until the job reaches a terminal state, then return one record per
    response: ``{"paper_id", "text", "error"}``. Raises :class:`GeminiBatchError`
    on a terminal failure state so the caller can trip the breaker.

    ``PARTIALLY_SUCCEEDED`` is treated as success — the per-item ``error`` field
    flags the individual papers that didn't complete."""
    client = _get_client()
    while True:
        job = client.batches.get(name=job_name)
        state = _state_name(job)
        if state in _STATES_TERMINAL_BAD:
            raise GeminiBatchError(f"{state}: {getattr(job, 'error', None)}")
        if state in (_STATE_OK, _STATE_PARTIAL):
            break
        time.sleep(interval_sec)

    dest = getattr(job, "dest", None)
    items = (getattr(dest, "inlined_responses", None) or []) if dest else []
    out: list[dict] = []
    for item in items:
        meta = getattr(item, "metadata", None) or {}
        pid = meta.get("paper_id") if isinstance(meta, dict) else None
        err = getattr(item, "error", None)
        if err is not None:
            out.append({"paper_id": pid, "text": None, "error": str(err)})
            continue
        resp = getattr(item, "response", None)
        text = getattr(resp, "text", None) if resp is not None else None
        out.append({"paper_id": pid, "text": text, "error": None})
    return out
