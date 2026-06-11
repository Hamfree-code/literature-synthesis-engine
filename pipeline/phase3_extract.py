"""Phase 3: Extract structured data via Claude API.

v3 (Master Improvement Spec v3.0 — Priority 2.1): two-step deep extraction
with arbiter. Each paper is independently extracted by two Sonnet reviewers
at different temperatures (A=0.1, B=0.3) and the two outputs are reconciled
by a third Sonnet pass (arbiter, temp=0.0). The arbiter output is the
canonical extraction stored downstream. When `settings.ARBITER_ENABLED` is
false, the pipeline falls back to a single-pass extraction at temp=0.1 for
cost-conscious runs.
"""

from __future__ import annotations

# __APP_PATHS_INSTALLED__
from app_paths import app_data, resource

import json
import re

from rich.console import Console

from config.extraction_schema import ARBITER_TOOL, EXTRACTION_TOOL
from config.settings import settings
from utils.checkpointing import Checkpoint
from utils.claude_client import (
    message_output_tokens,
    message_stop_reason,
    parse_batch_message,
    parse_json_response,
    poll_batch,
    repair_json_to_schema,
    sanitize_custom_id,
    submit_batch,
)

console = Console()

TRIAGE_PROMPT = resource("config/prompts/triage_haiku.txt").read_text(encoding="utf-8")
EXTRACT_PROMPT = resource("config/prompts/extraction_sonnet.txt").read_text(encoding="utf-8")
ARBITER_PROMPT = resource("config/prompts/arbiter_sonnet.txt").read_text(encoding="utf-8")

# Sentinel string used to split the extraction prompt for prompt-caching.
# Must match the literal header in extraction_sonnet.txt.
_FULLTEXT_SENTINEL = "FULL TEXT (structured by section):"


def _topic_substitute(template: str) -> str:
    from utils.run_context import topic_title, topic_lower

    return template.replace("{topic_title}", topic_title()).replace("{topic}", topic_lower())


def build_triage_request(paper: dict) -> dict:
    prompt = _topic_substitute(TRIAGE_PROMPT)
    prompt_schema = prompt.split("ABSTRACT:")[0] + "ABSTRACT:"
    abstract_text = f"\n{paper['abstract']}"
    return {
        "custom_id": sanitize_custom_id(paper["id"]),
        "params": {
            "model": settings.ANTHROPIC_HAIKU_MODEL,
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_schema, "cache_control": {"type": "ephemeral"}},
                        {"type": "text", "text": abstract_text},
                    ],
                }
            ],
        },
    }


def reviewer_custom_id(paper_id: str, suffix: str = "") -> str:
    """Sanitize a paper_id and append an optional reviewer suffix, staying
    within the Anthropic Batch API custom_id 64-char limit."""
    base = re.sub(r"[^a-zA-Z0-9_-]", "_", paper_id)
    suf = f"__{suffix}" if suffix else ""
    return base[: 64 - len(suf)] + suf


_COMPRESSION_PREAMBLE = (
    "COMPRESSION MODE (retry): your previous output was truncated. Limit "
    "provenance to the 8 most relevant entries and keep probabilistic_summary, "
    "grade_rationale and critical_notes to <=2 sentences each. Preserve all "
    "numeric fields.\n\n"
)


def build_reviewer_request(paper: dict, *, temperature: float, suffix: str, compress: bool = False) -> dict:
    """Build a single deep-extraction request for one reviewer at a specific
    temperature. The prompt schema is shared and prompt-cached so the second
    reviewer pays the cached-input rate. UPGRADE v3.1 — P1: when
    settings.EXTRACTION_TOOL_USE, forces the ``submit_extraction`` tool so the
    model cannot return malformed JSON."""
    prompt = _topic_substitute(EXTRACT_PROMPT)
    prompt_schema = prompt.split(_FULLTEXT_SENTINEL)[0] + _FULLTEXT_SENTINEL
    body = paper.get("full_text") or paper.get("abstract") or ""
    if compress:
        body = _COMPRESSION_PREAMBLE + body
    params: dict = {
        "model": settings.ANTHROPIC_SONNET_MODEL,
        "max_tokens": settings.DEEP_MAX_TOKENS,
        "temperature": temperature,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_schema, "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": f"\n{body}"},
                ],
            }
        ],
    }
    if settings.EXTRACTION_TOOL_USE:
        params["tools"] = [EXTRACTION_TOOL]
        params["tool_choice"] = {"type": "tool", "name": EXTRACTION_TOOL["name"]}
    return {"custom_id": reviewer_custom_id(paper["id"], suffix), "params": params}


def build_deep_request(paper: dict) -> dict:
    """Backward-compatible single-pass request — used when ARBITER_ENABLED is
    false. Defaults to Reviewer A's temperature."""
    return build_reviewer_request(paper, temperature=0.1, suffix="")


def _gemini_reviewer_system_instruction() -> str:
    """Reviewer B (Gemini) system instruction: the same schema/instructions the
    Anthropic reviewers receive (the prompt prefix before the full-text body),
    plus an explicit JSON-only directive since Gemini has no forced tool_use."""
    prompt = _topic_substitute(EXTRACT_PROMPT)
    schema_part = prompt.split(_FULLTEXT_SENTINEL)[0]
    return (
        schema_part + "\n\nReturn ONLY a single valid JSON object matching the schema above. "
        "No markdown fences, no commentary."
    )


def _extract_b_via_gemini(
    papers: list[dict], *, temperature: float
) -> tuple[dict[str, dict], list[dict], list[dict]]:
    """Reviewer B on Gemini Flash via its Batch API. Returns the same
    ``(parsed_by_pid, attempts, failures)`` triple as ``_extract_with_retries``
    so ``_run_arbiter_pass`` is agnostic to the provider.

    Gemini's batch handles its own server-side retries, so there is no
    max_tokens compression loop here. Malformed JSON is salvaged with the same
    Haiku repair pass the Anthropic path uses, so a Gemini extraction is shaped
    identically to a Sonnet one. Any batch-level failure trips the ``gemini``
    breaker (surfaced in ``degraded_services``) and records the affected papers
    as failures — never lost."""
    from utils import gemini_client
    from utils.resilience import breaker

    parsed_by_pid: dict[str, dict] = {}
    attempts: list[dict] = []
    failures: list[dict] = []

    # One batch covers all papers, so a single failure means Reviewer B is
    # degraded for the whole run — threshold 1.
    cb = breaker("gemini", failure_threshold=1)

    def _attempt_record(pid: str, ok: bool, detail: str) -> dict:
        return {
            "paper_id": pid,
            "reviewer": "b",
            "attempt": 1,
            "stop_reason": None,
            "tokens_out": None,
            "parse_ok": ok,
            "detail": detail,
        }

    if not cb.allow():
        for p in papers:
            failures.append({"paper_id": p["id"], "reason": "gemini_circuit_open", "detail": ""})
        return parsed_by_pid, attempts, failures

    system_instruction = _gemini_reviewer_system_instruction()
    requests = [
        gemini_client.build_inlined_request(
            paper_id=p["id"],
            system_instruction=system_instruction,
            content=(p.get("full_text") or p.get("abstract") or ""),
            max_output_tokens=settings.DEEP_MAX_TOKENS,
            temperature=temperature,
        )
        for p in papers
    ]
    ordered_pids = [p["id"] for p in papers]

    try:
        console.print(
            f"  Reviewer B (Gemini {settings.GEMINI_FLASH_MODEL}): submitting {len(requests)} papers..."
        )
        job_name = gemini_client.submit_gemini_batch(requests, model=settings.GEMINI_FLASH_MODEL)
        results = gemini_client.poll_gemini_batch(job_name)
        cb.record_success()
    except Exception as e:  # GeminiBatchError or any transport/SDK error
        cb.record_failure()
        console.print(f"[yellow]Reviewer B (Gemini) degraded: {str(e)[:200]}[/]")
        for p in papers:
            failures.append({"paper_id": p["id"], "reason": "gemini_batch_error", "detail": str(e)[:300]})
        return parsed_by_pid, attempts, failures

    by_pid: dict[str | None, dict] = {}
    for i, res in enumerate(results):
        pid = res.get("paper_id") or (ordered_pids[i] if i < len(ordered_pids) else None)
        by_pid[pid] = res

    for pid in ordered_pids:
        res = by_pid.get(pid)
        if not res or res.get("error") or not res.get("text"):
            detail = (res or {}).get("error") or "no response"
            attempts.append(_attempt_record(pid, False, f"gemini: {detail}"))
            failures.append({"paper_id": pid, "reason": "gemini_no_response", "detail": str(detail)[:300]})
            continue
        parsed = parse_json_response(res["text"])
        detail = "gemini"
        if parsed is None and settings.REPAIR_PASS_ENABLED:
            parsed = repair_json_to_schema(res["text"], settings.ANTHROPIC_HAIKU_MODEL)
            detail = "gemini+repair"
        if parsed is None:
            attempts.append(_attempt_record(pid, False, "gemini: unparseable"))
            failures.append({"paper_id": pid, "reason": "gemini_parse_failed", "detail": "unparseable JSON"})
            continue
        attempts.append(_attempt_record(pid, True, detail))
        parsed_by_pid[pid] = parsed

    return parsed_by_pid, attempts, failures


# Opus 4.7/4.8 and Fable 5 reject temperature/top_p (HTTP 400). The arbiter
# model is configurable (defaults to Opus), so gate the sampling param on the
# model family instead of sending it unconditionally.
_NO_SAMPLING_PARAMS = ("opus-4-7", "opus-4-8", "fable-5", "mythos-5")


def _accepts_temperature(model: str) -> bool:
    return not any(tag in model for tag in _NO_SAMPLING_PARAMS)


def build_arbiter_request(paper: dict, reviewer_a: dict, reviewer_b: dict, *, compress: bool = False) -> dict:
    """Build a single arbiter request that reconciles two reviewer outputs.
    The reviewer JSONs are injected as inline text — placeholders must use
    direct string replace (not str.format) because the JSON contains braces.

    The arbiter runs on ``ANTHROPIC_ARBITER_MODEL`` (Opus by default): the
    strongest neutral adjudicator for reconciling the two Sonnet reviewers. We
    only send ``temperature`` when the model accepts it — Opus 4.7/4.8 and Fable
    reject sampling params, and the reconciliation is deterministic-by-prompt
    regardless."""
    prompt = _topic_substitute(ARBITER_PROMPT)
    prompt = prompt.replace("{reviewer_a_json}", json.dumps(reviewer_a, ensure_ascii=False))
    prompt = prompt.replace("{reviewer_b_json}", json.dumps(reviewer_b, ensure_ascii=False))
    full_text = paper.get("full_text") or paper.get("abstract") or ""
    prompt = prompt.replace("{full_text}", full_text)
    if compress:
        prompt = _COMPRESSION_PREAMBLE + prompt
    params: dict = {
        "model": settings.ANTHROPIC_ARBITER_MODEL,
        "max_tokens": settings.DEEP_MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }
    if _accepts_temperature(settings.ANTHROPIC_ARBITER_MODEL):
        params["temperature"] = 0.0
    if settings.EXTRACTION_TOOL_USE:
        params["tools"] = [ARBITER_TOOL]
        params["tool_choice"] = {"type": "tool", "name": ARBITER_TOOL["name"]}
    return {"custom_id": reviewer_custom_id(paper["id"], "arb"), "params": params}


def run_triage(max_papers: int | None = None) -> None:
    checkpoint = Checkpoint("phase3_triage")
    if checkpoint.is_complete():
        console.print("[green]Triage already complete. Skipping.[/]")
        return

    filtered_papers_path = app_data("data/filtered/relevant_papers.jsonl")
    if not filtered_papers_path.exists():
        raw_path = app_data("data/raw/papers.jsonl")
        if not raw_path.exists():
            console.print("[red]No papers found — run Phase 1 first.[/]")
            return
        console.print("[yellow]No relevant_papers.jsonl — auto-promoting from data/raw/papers.jsonl[/]")
        filtered_papers_path.parent.mkdir(parents=True, exist_ok=True)
        filtered_papers_path.write_text(raw_path.read_text(encoding="utf-8"))

    papers = [json.loads(line) for line in filtered_papers_path.open(encoding="utf-8")]
    if max_papers:
        papers = papers[:max_papers]
    console.print(f"Triaging {len(papers)} papers via Batch API...")

    cid_to_pid = {sanitize_custom_id(p["id"]): p["id"] for p in papers}

    all_results = []
    for i in range(0, len(papers), 1000):
        chunk = papers[i : i + 1000]
        requests = [build_triage_request(p) for p in chunk]
        batch_id = submit_batch(requests)
        results = poll_batch(batch_id)
        all_results.extend(results)

    out = app_data("data/filtered/triage_results.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out.open("w", encoding="utf-8") as f:
        for r in all_results:
            if r.result.type == "succeeded":
                parsed = parse_json_response(r.result.message.content[0].text)
                if parsed:
                    paper_id = cid_to_pid.get(r.custom_id, r.custom_id)
                    f.write(json.dumps({"paper_id": paper_id, **parsed}) + "\n")
                    count += 1

    console.print(f"Triage complete: {count}/{len(papers)} extracted")
    checkpoint.mark_complete()


def select_for_deep_analysis(top_n: int = 500) -> list[str]:
    """Rank by sample_size × design_weight × extraction_confidence. Return paper IDs."""
    triage_path = app_data("data/filtered/triage_results.jsonl")
    if not triage_path.exists():
        return []
    papers = [json.loads(line) for line in triage_path.open(encoding="utf-8")]
    # P7/F1: prefer the topic-neutral flag, fall back to the legacy COVID name so
    # both old and new triage outputs select correctly.
    papers = [p for p in papers if p.get("is_topic_focused", p.get("is_long_covid_focused"))]
    design_weight = {"RCT": 1.0, "cohort": 1.0, "meta_analysis": 1.2}
    papers.sort(
        key=lambda p: (
            (p.get("sample_size") or 0)
            * design_weight.get(p.get("study_design"), 0.5)
            * (p.get("extraction_confidence") or 0.5)
        ),
        reverse=True,
    )
    return [p["paper_id"] for p in papers[:top_n]]


def _load_deep_papers(paper_ids: list[str]) -> list[dict]:
    """Hydrate selected papers with their cached full_text."""
    papers_by_id: dict[str, dict] = {}
    for line in app_data("data/filtered/relevant_papers.jsonl").open(encoding="utf-8"):
        p = json.loads(line)
        papers_by_id[p["id"]] = p

    fulltext_cache_path = app_data("data/raw/fulltext_cache.jsonl")
    if fulltext_cache_path.exists():
        for line in fulltext_cache_path.open(encoding="utf-8"):
            try:
                rec = json.loads(line)
                if rec["paper_id"] in papers_by_id:
                    papers_by_id[rec["paper_id"]]["full_text"] = rec["full_text"]
            except (json.JSONDecodeError, KeyError):
                pass

    return [papers_by_id[pid] for pid in paper_ids if pid in papers_by_id]


def _tool_name_for(reviewer: str) -> str | None:
    if not settings.EXTRACTION_TOOL_USE:
        return None
    return ARBITER_TOOL["name"] if reviewer == "arb" else EXTRACTION_TOOL["name"]


def _parse_one_result(r, paper_id: str, reviewer: str, attempt: int) -> tuple[dict | None, dict, str | None]:
    """Parse a single batch result. Returns (parsed | None, attempt_record, reason | None).

    reason is one of: None (success), 'api_error', 'max_tokens', 'parse_failed'.
    UPGRADE v3.1 — P1: prefers the forced tool_use payload; logs stop_reason and
    output tokens for the extraction_attempts table.
    """
    record = {
        "paper_id": paper_id,
        "reviewer": reviewer,
        "attempt": attempt,
        "stop_reason": None,
        "tokens_out": None,
        "parse_ok": False,
        "detail": "",
    }
    if r.result.type != "succeeded":
        err = getattr(r.result, "error", None)
        msg = getattr(err, "message", str(err)) if err else f"result.type={r.result.type}"
        record["detail"] = str(msg)[:300]
        return None, record, "api_error"

    message = r.result.message
    record["stop_reason"] = message_stop_reason(message)
    record["tokens_out"] = message_output_tokens(message)
    parsed = parse_batch_message(message, _tool_name_for(reviewer))

    if parsed is None and settings.REPAIR_PASS_ENABLED:
        # Should be unreachable under tool-use; salvage any text before giving up.
        text_blocks = [
            getattr(b, "text", "") for b in (message.content or []) if getattr(b, "type", None) == "text"
        ]
        if text_blocks:
            parsed = repair_json_to_schema("\n".join(text_blocks), settings.ANTHROPIC_HAIKU_MODEL)
            if parsed is not None:
                record["detail"] = "recovered via repair pass"

    if parsed is None:
        reason = "max_tokens" if record["stop_reason"] == "max_tokens" else "parse_failed"
        record["detail"] = record["detail"] or f"stop={record['stop_reason']}"
        return None, record, reason

    record["parse_ok"] = True
    return parsed, record, None


def _extract_with_retries(
    papers: list[dict], *, suffix: str, temperature: float
) -> tuple[dict[str, dict], list[dict], list[dict]]:
    """Run one reviewer (or single-pass) extraction with max_tokens compression
    retries. Returns (parsed_by_pid, attempts, failures). UPGRADE v3.1 — P1.2."""
    pending: dict[str, dict] = {p["id"]: p for p in papers}
    parsed_by_pid: dict[str, dict] = {}
    attempts: list[dict] = []
    failures: list[dict] = []
    reviewer = suffix or "single"

    max_attempts = settings.DEEP_MAX_RETRIES + 1
    for attempt in range(1, max_attempts + 1):
        if not pending:
            break
        compress = attempt > 1
        cid_to_pid = {reviewer_custom_id(pid, suffix): pid for pid in pending}
        requests = [
            build_reviewer_request(p, temperature=temperature, suffix=suffix, compress=compress)
            for p in pending.values()
        ]
        if compress:
            console.print(f"  [yellow]Compression retry {attempt - 1}: {len(requests)} oversized papers[/]")
        results = poll_batch(submit_batch(requests))

        next_pending: dict[str, dict] = {}
        for r in results:
            pid = cid_to_pid.get(r.custom_id)
            if pid is None:
                continue
            parsed, record, reason = _parse_one_result(r, pid, reviewer, attempt)
            attempts.append(record)
            if parsed is not None:
                parsed_by_pid[pid] = parsed
            elif reason == "max_tokens" and attempt < max_attempts:
                next_pending[pid] = pending[pid]
            else:
                failures.append({"paper_id": pid, "reason": reason, "detail": record["detail"]})
        pending = next_pending

    for pid in pending:  # exhausted retries
        failures.append(
            {"paper_id": pid, "reason": "max_tokens_exhausted", "detail": "still oversized after retries"}
        )
    return parsed_by_pid, attempts, failures


def _write_attempts(attempts: list[dict]) -> None:
    out = app_data("data/filtered/extraction_attempts.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for a in attempts:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")


def _write_outputs(extractions_by_pid: dict[str, dict], failures: list[dict], n_requested: int) -> None:
    out = app_data("data/filtered/deep_results.jsonl")
    fail_out = app_data("data/filtered/deep_failures.jsonl")
    provenance_total = 0
    reconciliations = 0
    with out.open("w", encoding="utf-8") as f:
        for paper_id, parsed in extractions_by_pid.items():
            provenance_total += len(parsed.get("provenance") or [])
            if parsed.get("reconciliation_triggered"):
                reconciliations += 1
            f.write(json.dumps({"paper_id": paper_id, **parsed}, ensure_ascii=False) + "\n")

    if failures:
        with fail_out.open("w", encoding="utf-8") as ff:
            for x in failures:
                ff.write(json.dumps(x, ensure_ascii=False) + "\n")
    elif fail_out.exists():
        fail_out.unlink()

    console.print(
        f"Deep extraction complete: {len(extractions_by_pid)}/{n_requested} extracted "
        f"({len(failures)} failed: "
        f"{sum(1 for x in failures if x['reason'] == 'api_error')} API, "
        f"{sum(1 for x in failures if x['reason'] == 'json_parse_failed')} JSON parse), "
        f"{provenance_total} provenance entries (persisted in Phase 4), "
        f"{reconciliations} reconciliations triggered by arbiter"
    )


def _run_single_pass(deep_papers: list[dict]) -> None:
    """Fallback path when ARBITER_ENABLED is false: one Sonnet call per paper."""
    parsed, attempts, failures = _extract_with_retries(deep_papers, suffix="", temperature=0.1)
    _write_attempts(attempts)
    _write_outputs(parsed, failures, len(deep_papers))


def _run_arbiter_pass(deep_papers: list[dict]) -> None:
    """Two-step extraction + arbiter. UPGRADE v3.1 — P1: reviewers A and B run in
    separate, retry-aware passes (no more double-parse over a shared result list,
    cf. finding F5), each with max_tokens compression retries."""
    attempts: list[dict] = []

    # ---- Step 1: Reviewers A and B (each retry-aware) ---------------------
    # Reviewer A is always Sonnet (temperature 0.1). Reviewer B is Sonnet
    # (temperature 0.3) by default, or Gemini Flash when REVIEWER_B_PROVIDER is
    # "gemini" and a key is configured — cross-model diversity decorrelates the
    # extraction errors a same-family pair would share.
    from utils.gemini_client import gemini_available

    parsed_a, att_a, fail_a = _extract_with_retries(deep_papers, suffix="a", temperature=0.1)
    if settings.REVIEWER_B_PROVIDER == "gemini" and gemini_available():
        b_provider = "gemini"
        parsed_b, att_b, fail_b = _extract_b_via_gemini(deep_papers, temperature=0.3)
    else:
        b_provider = "anthropic"
        parsed_b, att_b, fail_b = _extract_with_retries(deep_papers, suffix="b", temperature=0.3)
    attempts.extend(att_a)
    attempts.extend(att_b)
    fail_a_by_pid = {f["paper_id"]: f for f in fail_a}
    fail_b_by_pid = {f["paper_id"]: f for f in fail_b}

    console.print(
        f"  Reviewer pass complete: A=Sonnet {len(parsed_a)}/{len(deep_papers)}, "
        f"B={b_provider} {len(parsed_b)}/{len(deep_papers)}"
    )

    # ---- Step 2: Arbiter for papers where BOTH reviewers succeeded --------
    arbiter_ready = [p for p in deep_papers if p["id"] in parsed_a and p["id"] in parsed_b]
    arbiter_skipped: dict[str, dict] = {}
    for p in deep_papers:
        pid = p["id"]
        if pid in parsed_a and pid not in parsed_b:
            rec = dict(parsed_a[pid])
            rec["reconciliation_triggered"] = False
            rec["arbiter_notes"] = "Reviewer B failed; using Reviewer A unilaterally."
            arbiter_skipped[pid] = rec
        elif pid in parsed_b and pid not in parsed_a:
            rec = dict(parsed_b[pid])
            rec["reconciliation_triggered"] = False
            rec["arbiter_notes"] = "Reviewer A failed; using Reviewer B unilaterally."
            arbiter_skipped[pid] = rec

    parsed_arb: dict[str, dict] = {}
    arb_failures: list[dict] = []
    if arbiter_ready:
        pending = {p["id"]: p for p in arbiter_ready}
        max_attempts = settings.DEEP_MAX_RETRIES + 1
        for attempt in range(1, max_attempts + 1):
            if not pending:
                break
            compress = attempt > 1
            cid_to_pid = {reviewer_custom_id(pid, "arb"): pid for pid in pending}
            requests = [
                build_arbiter_request(p, parsed_a[p["id"]], parsed_b[p["id"]], compress=compress)
                for p in pending.values()
            ]
            console.print(f"  Arbiter pass: submitting {len(requests)} reconciliations...")
            results = poll_batch(submit_batch(requests))
            next_pending: dict[str, dict] = {}
            for r in results:
                pid = cid_to_pid.get(r.custom_id)
                if pid is None:
                    continue
                parsed, record, reason = _parse_one_result(r, pid, "arb", attempt)
                attempts.append(record)
                if parsed is not None:
                    parsed_arb[pid] = parsed
                elif reason == "max_tokens" and attempt < max_attempts:
                    next_pending[pid] = pending[pid]
                else:
                    arb_failures.append(
                        {"paper_id": pid, "reason": f"arbiter_{reason}", "detail": record["detail"]}
                    )
            pending = next_pending
        for pid in pending:
            arb_failures.append({"paper_id": pid, "reason": "arbiter_max_tokens_exhausted", "detail": ""})

    # ---- Step 3: Aggregate ------------------------------------------------
    final_extractions: dict[str, dict] = {}
    for pid, arb in parsed_arb.items():
        arb["reviewer_a_raw"] = parsed_a.get(pid)
        arb["reviewer_b_raw"] = parsed_b.get(pid)
        arb["reviewer_b_provider"] = b_provider
        final_extractions[pid] = arb
    for pid, single in arbiter_skipped.items():
        single.setdefault("reviewer_b_provider", b_provider)
        final_extractions.setdefault(pid, single)

    # A paper is a true failure only if it ended up in NO final extraction.
    combined_failures: list[dict] = []
    for pid in {p["id"] for p in deep_papers}:
        if pid in final_extractions:
            continue
        fa = fail_a_by_pid.get(pid)
        fb = fail_b_by_pid.get(pid)
        arb_f = next((f for f in arb_failures if f["paper_id"] == pid), None)
        if arb_f:
            combined_failures.append(arb_f)
        elif fa and fb:
            combined_failures.append(
                {
                    "paper_id": pid,
                    "reason": f"both_reviewers_failed:{fa['reason']}/{fb['reason']}",
                    "detail": "",
                }
            )
        elif fa or fb:
            f = fa or fb
            combined_failures.append({"paper_id": pid, "reason": f["reason"], "detail": f.get("detail", "")})

    _write_attempts(attempts)
    _write_outputs(final_extractions, combined_failures, len(deep_papers))


def _run_umls_normalization() -> None:
    """For each successful deep extraction, attach UMLS CUIs / MeSH headings
    to the entities via a single Haiku tool call per paper. Writes the
    normalised entities to data/filtered/normalized_entities.jsonl for Phase 4
    to upsert into the extracted_phenotypes table.
    """
    deep_path = app_data("data/filtered/deep_results.jsonl")
    if not deep_path.exists():
        return
    from utils.umls_client import verification_available, verify_entities
    from utils.umls_normalizer import normalize_extraction

    out_path = app_data("data/filtered/normalized_entities.jsonl")
    total = 0
    verified = 0
    papers_with_entities = 0
    do_verify = verification_available()  # P2: only when UMLS_API_KEY is set
    with out_path.open("w", encoding="utf-8") as f:
        for line in deep_path.open(encoding="utf-8"):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            pid = rec.get("paper_id")
            if not pid:
                continue
            normalized = normalize_extraction(rec)
            if normalized:
                if do_verify:
                    normalized = verify_entities(normalized, cache_path=app_data("data/raw/umls_cache.json"))
                    verified += sum(1 for e in normalized if e.get("cui_verified"))
                papers_with_entities += 1
                total += len(normalized)
                f.write(json.dumps({"paper_id": pid, "entities": normalized}, ensure_ascii=False) + "\n")
    pct = round(100.0 * verified / total, 1) if total else 0.0
    suffix = f", {verified} CUIs verified ({pct}%)" if do_verify else " (UMLS verification offline)"
    console.print(
        f"UMLS normalization: {total} entities normalised across {papers_with_entities} papers{suffix}"
    )


def run_deep(paper_ids: list[str]) -> None:
    checkpoint = Checkpoint("phase3_deep")
    if checkpoint.is_complete():
        console.print("[green]Deep extraction already complete. Skipping.[/]")
        return

    if not paper_ids:
        console.print("[yellow]No papers selected for deep analysis.[/]")
        return

    deep_papers = _load_deep_papers(paper_ids)
    with_fulltext = sum(1 for p in deep_papers if p.get("full_text"))
    mode = "arbiter" if settings.ARBITER_ENABLED else "single-pass"
    console.print(
        f"Deep-extracting {len(deep_papers)} papers via Sonnet Batch API "
        f"[{with_fulltext} with full-text, mode={mode}]"
    )

    if settings.ARBITER_ENABLED:
        _run_arbiter_pass(deep_papers)
    else:
        _run_single_pass(deep_papers)

    if settings.UMLS_NORMALIZATION_ENABLED:
        _run_umls_normalization()

    checkpoint.mark_complete()


def run(max_deep: int = 500, max_papers: int | None = None) -> None:
    run_triage(max_papers=max_papers)
    top_ids = select_for_deep_analysis(top_n=max_deep)
    console.print(f"Selected {len(top_ids)} papers for deep analysis")

    if top_ids:
        from pipeline.phase1_ingest import enrich_with_fulltext
        import asyncio

        asyncio.run(enrich_with_fulltext(top_ids))

    run_deep(top_ids)


if __name__ == "__main__":
    run(max_deep=settings.MAX_DEEP_ANALYSIS)
