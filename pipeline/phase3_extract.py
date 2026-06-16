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
from pathlib import Path

from rich.console import Console

from config.settings import settings
from methodology.extraction_schema import (
    FailureReason,
    classify_parse_failure,
    parse_or_repair,
    validate_extraction,
)
from methodology import flow_record as flow
from utils.checkpointing import Checkpoint
from utils.claude_client import parse_json_response, poll_batch, sanitize_custom_id, submit_batch

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
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_schema, "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": abstract_text},
                ],
            }],
        },
    }


def reviewer_custom_id(paper_id: str, suffix: str = "") -> str:
    """Sanitize a paper_id and append an optional reviewer suffix, staying
    within the Anthropic Batch API custom_id 64-char limit."""
    base = re.sub(r"[^a-zA-Z0-9_-]", "_", paper_id)
    suf = f"__{suffix}" if suffix else ""
    return base[: 64 - len(suf)] + suf


def build_reviewer_request(paper: dict, *, temperature: float, suffix: str) -> dict:
    """Build a single deep-extraction request for one reviewer at a specific
    temperature. The prompt schema is shared and prompt-cached so that the
    second reviewer pays the cached-input rate."""
    prompt = _topic_substitute(EXTRACT_PROMPT)
    prompt_schema = prompt.split(_FULLTEXT_SENTINEL)[0] + _FULLTEXT_SENTINEL
    body = paper.get("full_text") or paper.get("abstract") or ""
    return {
        "custom_id": reviewer_custom_id(paper["id"], suffix),
        "params": {
            "model": settings.ANTHROPIC_SONNET_MODEL,
            # WP-1: ceiling sized to the largest observed payload + headroom so
            # data-rich papers no longer truncate mid-object.
            "max_tokens": settings.DEEP_EXTRACTION_MAX_TOKENS,
            "temperature": temperature,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_schema, "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": f"\n{body}"},
                ],
            }],
        },
    }


def build_deep_request(paper: dict) -> dict:
    """Backward-compatible single-pass request — used when ARBITER_ENABLED is
    false. Defaults to Reviewer A's temperature."""
    return build_reviewer_request(paper, temperature=0.1, suffix="")


def build_arbiter_request(paper: dict, reviewer_a: dict, reviewer_b: dict) -> dict:
    """Build a single arbiter request that reconciles two reviewer outputs.
    The reviewer JSONs are injected as inline text — placeholders must use
    direct string replace (not str.format) because the JSON contains braces."""
    prompt = _topic_substitute(ARBITER_PROMPT)
    prompt = prompt.replace("{reviewer_a_json}", json.dumps(reviewer_a, ensure_ascii=False))
    prompt = prompt.replace("{reviewer_b_json}", json.dumps(reviewer_b, ensure_ascii=False))
    full_text = paper.get("full_text") or paper.get("abstract") or ""
    prompt = prompt.replace("{full_text}", full_text)
    return {
        "custom_id": reviewer_custom_id(paper["id"], "arb"),
        "params": {
            "model": settings.ANTHROPIC_SONNET_MODEL,
            "max_tokens": settings.DEEP_EXTRACTION_MAX_TOKENS,
            "temperature": 0.0,
            "messages": [{"role": "user", "content": prompt}],
        },
    }


def _repair_fn(raw: str) -> str:
    """WP-1 repair pass: hand a malformed extraction string to Haiku and ask for
    valid JSON conforming to the schema. Returns the model's text; the caller
    accepts it only if it validates."""
    from utils.claude_client import client

    schema_hint = _topic_substitute(EXTRACT_PROMPT).split(_FULLTEXT_SENTINEL)[0]
    instruction = (
        "Return ONLY valid JSON conforming to this schema; do not add commentary, "
        "markdown, or preamble. Repair the malformed JSON that follows the schema.\n\n"
        f"SCHEMA:\n{schema_hint}\n\nMALFORMED JSON TO REPAIR:\n{raw}"
    )
    msg = client.messages.create(
        model=settings.ANTHROPIC_HAIKU_MODEL,
        max_tokens=settings.DEEP_EXTRACTION_MAX_TOKENS,
        messages=[{"role": "user", "content": instruction}],
    )
    return msg.content[0].text if msg.content else ""


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
    papers = [p for p in papers if p.get("is_long_covid_focused")]
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


def _parse_batch_results(
    results, cid_to_pid: dict[str, str], *, repair: bool = False
) -> tuple[dict[str, dict], list[dict]]:
    """Parse a list of batch results into (parsed_by_paper_id, failures).

    WP-1: failures carry a TYPED reason (truncation | schema_violation |
    api_error | timeout) so no paper is dropped silently. When ``repair`` is
    set (the final/canonical extraction), a JSON parse failure gets one Haiku
    repair attempt — accepted only if it validates.
    """
    repair_fn = _repair_fn if (repair and settings.EXTRACTION_REPAIR_ENABLED) else None
    parsed_by_pid: dict[str, dict] = {}
    failures: list[dict] = []
    for r in results:
        paper_id = cid_to_pid.get(r.custom_id, r.custom_id)
        if r.result.type != "succeeded":
            err = getattr(r.result, "error", None)
            err_msg = getattr(err, "message", str(err)) if err else f"result.type={r.result.type}"
            reason = FailureReason.TIMEOUT if r.result.type == "timeout" else FailureReason.API_ERROR
            failures.append({"paper_id": paper_id, "reason": reason.value, "detail": err_msg[:300]})
            continue
        raw_text = r.result.message.content[0].text if r.result.message.content else ""
        obj, fail_reason = parse_or_repair(raw_text, repair_fn=repair_fn)
        if obj is None:
            failures.append({
                "paper_id": paper_id,
                "reason": (fail_reason or classify_parse_failure(raw_text)).value,
                "detail": f"raw len={len(raw_text)} prefix={raw_text[:200]!r}",
            })
            continue
        parsed_by_pid[paper_id] = obj
    return parsed_by_pid, failures


def _finalize_validation(
    extractions_by_pid: dict[str, dict]
) -> tuple[dict[str, dict], list[dict]]:
    """Validate canonical extractions against the schema before they reach the
    DB. Reject-with-reason; never drop silently (WP-1.1)."""
    valid: dict[str, dict] = {}
    failures: list[dict] = []
    for pid, obj in extractions_by_pid.items():
        outcome = validate_extraction(obj)
        if outcome.ok:
            valid[pid] = obj
        else:
            failures.append({
                "paper_id": pid,
                "reason": (outcome.reason or FailureReason.SCHEMA_VIOLATION).value,
                "detail": "; ".join(outcome.errors)[:300],
            })
    return valid, failures


def _write_flow_record(intended_ids: list[str], succeeded_ids: set[str], failures: list[dict]) -> None:
    """Emit the PRISMA-style flow record (WP-1.4) as structured JSON so no paper
    vanishes and n_intended / n_extracted / n_failed / n_substituted are
    explicit and reproducible."""
    failure_reasons = {f["paper_id"]: f.get("reason", FailureReason.SCHEMA_VIOLATION.value) for f in failures}
    selection = flow.reconcile_selection(
        intended_ids, succeeded_ids=succeeded_ids, replacement_pool=[], failure_reasons=failure_reasons,
    )
    by_reason: dict[str, int] = {}
    for f in failures:
        by_reason[f.get("reason", "unknown")] = by_reason.get(f.get("reason", "unknown"), 0) + 1
    record = flow.build_flow_record(
        identified=0, triaged=0, eligible=len(intended_ids),
        selection=selection, failures_by_reason=by_reason,
    )
    payload = {"flow": record.to_dict(), "selection": selection.to_dict(), "diagram": record.as_text_diagram()}
    out = app_data("data/filtered/flow_record.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(
        f"Flow record: intended={selection.n_intended} extracted={selection.n_extracted} "
        f"failed={selection.n_failed} substituted={selection.n_substituted}"
    )


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
        f"{sum(1 for x in failures if x['reason']=='api_error')} API, "
        f"{sum(1 for x in failures if x['reason']=='json_parse_failed')} JSON parse), "
        f"{provenance_total} provenance entries (persisted in Phase 4), "
        f"{reconciliations} reconciliations triggered by arbiter"
    )


def _run_single_pass(deep_papers: list[dict]) -> None:
    """Fallback path when ARBITER_ENABLED is false: one Sonnet call per paper."""
    cid_to_pid = {reviewer_custom_id(p["id"], ""): p["id"] for p in deep_papers}
    requests = [build_reviewer_request(p, temperature=0.1, suffix="") for p in deep_papers]
    console.print(f"  Submitting single-pass batch ({len(requests)} requests)...")
    batch_id = submit_batch(requests)
    results = poll_batch(batch_id)
    parsed, failures = _parse_batch_results(results, cid_to_pid, repair=True)
    parsed, vfailures = _finalize_validation(parsed)
    failures += vfailures
    _write_flow_record([p["id"] for p in deep_papers], set(parsed.keys()), failures)
    _write_outputs(parsed, failures, len(deep_papers))


def _run_arbiter_pass(deep_papers: list[dict]) -> None:
    """Two-step extraction + arbiter (Master Improvement Spec v3.0 — Priority 2.1)."""
    # ---- Step 1: Reviewers A and B in a single batch (paired) -------------
    cid_a_to_pid: dict[str, str] = {}
    cid_b_to_pid: dict[str, str] = {}
    reviewer_requests = []
    for p in deep_papers:
        cid_a = reviewer_custom_id(p["id"], "a")
        cid_b = reviewer_custom_id(p["id"], "b")
        cid_a_to_pid[cid_a] = p["id"]
        cid_b_to_pid[cid_b] = p["id"]
        reviewer_requests.append(build_reviewer_request(p, temperature=0.1, suffix="a"))
        reviewer_requests.append(build_reviewer_request(p, temperature=0.3, suffix="b"))

    console.print(f"  Reviewer pass: submitting {len(reviewer_requests)} requests (A+B per paper)...")
    reviewer_batch_id = submit_batch(reviewer_requests)
    reviewer_results = poll_batch(reviewer_batch_id)

    parsed_a, fail_a = _parse_batch_results(reviewer_results, cid_a_to_pid)
    parsed_b, fail_b = _parse_batch_results(reviewer_results, cid_b_to_pid)
    # _parse_batch_results was called twice on the same result list — each call
    # also generates spurious failure entries for the OTHER reviewer's IDs. We
    # discard those.
    fail_a = [x for x in fail_a if x["paper_id"] in cid_a_to_pid.values()]
    fail_b = [x for x in fail_b if x["paper_id"] in cid_b_to_pid.values()]

    console.print(
        f"  Reviewer pass complete: A={len(parsed_a)}/{len(deep_papers)}, "
        f"B={len(parsed_b)}/{len(deep_papers)}"
    )

    # ---- Step 2: Arbiter pass for papers where BOTH reviewers succeeded ----
    arbiter_ready = [p for p in deep_papers if p["id"] in parsed_a and p["id"] in parsed_b]
    arbiter_skipped: dict[str, dict] = {}
    for p in deep_papers:
        if p["id"] in parsed_a and p["id"] not in parsed_b:
            arbiter_skipped[p["id"]] = parsed_a[p["id"]]
            arbiter_skipped[p["id"]]["reconciliation_triggered"] = False
            arbiter_skipped[p["id"]]["arbiter_notes"] = "Reviewer B failed; using Reviewer A unilaterally."
        elif p["id"] in parsed_b and p["id"] not in parsed_a:
            arbiter_skipped[p["id"]] = parsed_b[p["id"]]
            arbiter_skipped[p["id"]]["reconciliation_triggered"] = False
            arbiter_skipped[p["id"]]["arbiter_notes"] = "Reviewer A failed; using Reviewer B unilaterally."

    if arbiter_ready:
        cid_arb_to_pid = {reviewer_custom_id(p["id"], "arb"): p["id"] for p in arbiter_ready}
        arbiter_requests = [
            build_arbiter_request(p, parsed_a[p["id"]], parsed_b[p["id"]])
            for p in arbiter_ready
        ]
        console.print(f"  Arbiter pass: submitting {len(arbiter_requests)} reconciliations...")
        arb_batch_id = submit_batch(arbiter_requests)
        arb_results = poll_batch(arb_batch_id)
        parsed_arb, fail_arb = _parse_batch_results(arb_results, cid_arb_to_pid, repair=True)
    else:
        parsed_arb = {}
        fail_arb = []

    # ---- Step 3: Aggregate ------------------------------------------------
    # Preserve reviewer_a_raw / reviewer_b_raw on the arbiter output for audit.
    final_extractions: dict[str, dict] = {}
    for pid, arb in parsed_arb.items():
        arb["reviewer_a_raw"] = parsed_a.get(pid)
        arb["reviewer_b_raw"] = parsed_b.get(pid)
        final_extractions[pid] = arb
    for pid, single in arbiter_skipped.items():
        final_extractions[pid] = single

    # Combine failures: a paper that failed BOTH reviewers OR was a single
    # reviewer success but arbiter failed.
    combined_failures: list[dict] = []
    for f in fail_a:
        if f["paper_id"] not in parsed_b:
            combined_failures.append({**f, "reason": f"reviewer_a_{f['reason']}_AND_b_failed"})
    for f in fail_arb:
        combined_failures.append({**f, "reason": f"arbiter_{f['reason']}"})

    # WP-1: validate canonical extractions before they reach the DB, then emit
    # the PRISMA flow record so no paper vanishes silently.
    final_extractions, validation_failures = _finalize_validation(final_extractions)
    combined_failures += validation_failures
    _write_flow_record([p["id"] for p in deep_papers], set(final_extractions.keys()), combined_failures)

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
    from utils.umls_normalizer import normalize_extraction

    out_path = app_data("data/filtered/normalized_entities.jsonl")
    total = 0
    papers_with_entities = 0
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
                papers_with_entities += 1
                total += len(normalized)
                f.write(json.dumps({"paper_id": pid, "entities": normalized}, ensure_ascii=False) + "\n")
    console.print(
        f"UMLS normalization: {total} entities normalised across {papers_with_entities} papers"
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