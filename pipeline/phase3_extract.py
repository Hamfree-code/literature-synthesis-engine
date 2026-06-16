"""Phase 3: Extract structured data via a multi-provider engine.

v3.1 — multi-provider two-step deep extraction with arbiter:
  * Triage filter: Gemini Flash (cheap, high-volume abstract pass).
  * Reviewer A: Claude Sonnet (Anthropic Batch API, temp 0.1).
  * Reviewer B: Gemini Pro (async concurrent calls, temp 0.3).
  * Arbiter: Claude Opus (Anthropic Batch API, temp 0.0) reconciles A+B.

Using two different providers as reviewers reduces single-model bias before
the arbiter reconciles them. Claude work rides the Anthropic Batch API (with
the resume registry in utils.claude_client); Gemini work runs as bounded async
calls with its own resume cache (reviewer_b_cache.jsonl). When
`settings.ARBITER_ENABLED` is false, the pipeline falls back to a single-pass
Claude Sonnet extraction for cost-conscious runs.
"""
from __future__ import annotations
# __APP_PATHS_INSTALLED__
from app_paths import app_data, resource

import json
import re
from pathlib import Path

from rich.console import Console

from config.settings import settings
from utils.checkpointing import Checkpoint
from utils.claude_client import forget_batch, parse_json_response, poll_batch, sanitize_custom_id, submit_batch

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
            "max_tokens": 16384,  # 8192 truncated large full-text extractions
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


def build_triage_prompt(paper: dict) -> str:
    """Plain-string triage prompt for Gemini (no Anthropic-batch envelope).
    Same content as build_triage_request: schema header + abstract."""
    prompt = _topic_substitute(TRIAGE_PROMPT)
    schema = prompt.split("ABSTRACT:")[0] + "ABSTRACT:"
    return f"{schema}\n{paper.get('abstract', '')}"


def build_reviewer_prompt(paper: dict) -> str:
    """Plain-string deep-extraction prompt for the Gemini reviewer (Reviewer B).
    Same schema/body content as build_reviewer_request."""
    prompt = _topic_substitute(EXTRACT_PROMPT)
    schema = prompt.split(_FULLTEXT_SENTINEL)[0] + _FULLTEXT_SENTINEL
    body = paper.get("full_text") or paper.get("abstract") or ""
    return f"{schema}\n{body}"


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
            # Opus (the arbiter model) deprecated the `temperature` parameter and
            # rejects any request that sends it ("`temperature` is deprecated for
            # this model."), so we must NOT pass temperature here — unlike the
            # Sonnet reviewer requests above, which still accept it.
            "model": settings.ANTHROPIC_OPUS_MODEL,   # arbiter runs on Opus
            "max_tokens": 16384,  # match reviewer cap so reconciled JSON isn't truncated
            "messages": [{"role": "user", "content": prompt}],
        },
    }


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
    console.print(f"Triaging {len(papers)} papers via Gemini {settings.GEMINI_FLASH_MODEL}...")

    import asyncio

    from utils.gemini_client import gather_json

    # Gemini runs as bounded concurrent async calls (no Anthropic batch envelope).
    prompts = [(p["id"], build_triage_prompt(p)) for p in papers]
    parsed_by_pid, failures = asyncio.run(
        gather_json(settings.GEMINI_FLASH_MODEL, prompts, max_tokens=1024, temperature=0.0)
    )

    out = app_data("data/filtered/triage_results.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out.open("w", encoding="utf-8") as f:
        for paper_id, parsed in parsed_by_pid.items():
            f.write(json.dumps({"paper_id": paper_id, **parsed}) + "\n")
            count += 1

    fail_out = app_data("data/filtered/triage_failures.jsonl")
    if failures:
        with fail_out.open("w", encoding="utf-8") as ff:
            for x in failures:
                ff.write(json.dumps(
                    {"paper_id": x["key"], "reason": x["reason"], "detail": x.get("detail", "")},
                    ensure_ascii=False) + "\n")
    elif fail_out.exists():
        fail_out.unlink()

    console.print(f"Triage complete: {count}/{len(papers)} extracted ({len(failures)} failed)")
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


def _parse_batch_results(results, cid_to_pid: dict[str, str]) -> tuple[dict[str, dict], list[dict]]:
    """Parse a list of batch results into (parsed_by_paper_id, failures)."""
    parsed_by_pid: dict[str, dict] = {}
    failures: list[dict] = []
    for r in results:
        paper_id = cid_to_pid.get(r.custom_id, r.custom_id)
        if r.result.type != "succeeded":
            err = getattr(r.result, "error", None)
            err_msg = getattr(err, "message", str(err)) if err else f"result.type={r.result.type}"
            failures.append({"paper_id": paper_id, "reason": "api_error", "detail": err_msg[:300]})
            continue
        raw_text = r.result.message.content[0].text if r.result.message.content else ""
        parsed = parse_json_response(raw_text)
        if not parsed:
            failures.append({
                "paper_id": paper_id,
                "reason": "json_parse_failed",
                "detail": f"raw len={len(raw_text)} prefix={raw_text[:200]!r}",
            })
            continue
        parsed_by_pid[paper_id] = parsed
    return parsed_by_pid, failures


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
    batch_id = submit_batch(requests, label="deep_single")
    results = poll_batch(batch_id)
    parsed, failures = _parse_batch_results(results, cid_to_pid)
    _write_outputs(parsed, failures, len(deep_papers))
    forget_batch("deep_single")


def _reviewer_b_cache_path() -> Path:
    return app_data("data/filtered/reviewer_b_cache.jsonl")


def _load_reviewer_b_cache() -> dict[str, dict]:
    """Reviewer B (Gemini) is not on the Anthropic batch registry, so it gets
    its own resume cache: a crash mid-arbiter must not force re-paying Gemini."""
    path = _reviewer_b_cache_path()
    out: dict[str, dict] = {}
    if path.exists():
        for line in path.open(encoding="utf-8"):
            try:
                rec = json.loads(line)
                out[rec["paper_id"]] = rec["extraction"]
            except (json.JSONDecodeError, KeyError):
                pass
    return out


def _append_reviewer_b_cache(new_b: dict[str, dict]) -> None:
    if not new_b:
        return
    path = _reviewer_b_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for pid, ext in new_b.items():
            f.write(json.dumps({"paper_id": pid, "extraction": ext}, ensure_ascii=False) + "\n")


def _run_arbiter_pass(deep_papers: list[dict]) -> None:
    """Multi-provider two-step extraction + arbiter.

    Reviewer A = Claude Sonnet (Anthropic Batch), Reviewer B = Gemini Pro (async),
    arbiter = Claude Opus (Anthropic Batch). A and B can no longer share a batch,
    so each provider has its own resume path.
    """
    import asyncio

    from utils.gemini_client import gather_json

    # ---- Step 1a: Reviewer A — Claude Sonnet via Anthropic Batch ----------
    cid_a_to_pid = {reviewer_custom_id(p["id"], "a"): p["id"] for p in deep_papers}
    a_requests = [build_reviewer_request(p, temperature=0.1, suffix="a") for p in deep_papers]
    console.print(f"  Reviewer A (Claude Sonnet): submitting {len(a_requests)} requests...")
    a_batch_id = submit_batch(a_requests, label="deep_reviewers_a")
    parsed_a, fail_a = _parse_batch_results(poll_batch(a_batch_id), cid_a_to_pid)

    # ---- Step 1b: Reviewer B — Gemini Pro via async, with resume cache ----
    parsed_b = _load_reviewer_b_cache()
    todo_b = [p for p in deep_papers if p["id"] not in parsed_b]
    if todo_b:
        console.print(
            f"  Reviewer B (Gemini {settings.GEMINI_PRO_MODEL}): {len(todo_b)} papers "
            f"({len(parsed_b)} from cache)..."
        )
        prompts_b = [(p["id"], build_reviewer_prompt(p)) for p in todo_b]
        new_b, fail_b_raw = asyncio.run(
            gather_json(settings.GEMINI_PRO_MODEL, prompts_b, max_tokens=24576, temperature=0.3)
        )
        _append_reviewer_b_cache(new_b)
        parsed_b.update(new_b)
    else:
        fail_b_raw = []
        console.print(f"  Reviewer B (Gemini): all {len(parsed_b)} papers served from cache")

    console.print(
        f"  Reviewer pass complete: A={len(parsed_a)}/{len(deep_papers)}, "
        f"B={len(parsed_b)}/{len(deep_papers)}"
    )

    # ---- Step 2: Arbiter (Claude Opus) for papers where BOTH succeeded ----
    arbiter_ready = [p for p in deep_papers if p["id"] in parsed_a and p["id"] in parsed_b]
    arbiter_skipped: dict[str, dict] = {}
    for p in deep_papers:
        pid = p["id"]
        if pid in parsed_a and pid not in parsed_b:
            arbiter_skipped[pid] = {**parsed_a[pid], "reconciliation_triggered": False,
                                    "arbiter_notes": "Reviewer B (Gemini) failed; using Reviewer A unilaterally."}
        elif pid in parsed_b and pid not in parsed_a:
            arbiter_skipped[pid] = {**parsed_b[pid], "reconciliation_triggered": False,
                                    "arbiter_notes": "Reviewer A (Claude) failed; using Reviewer B unilaterally."}

    if arbiter_ready:
        cid_arb_to_pid = {reviewer_custom_id(p["id"], "arb"): p["id"] for p in arbiter_ready}
        arbiter_requests = [
            build_arbiter_request(p, parsed_a[p["id"]], parsed_b[p["id"]])
            for p in arbiter_ready
        ]
        console.print(f"  Arbiter (Claude Opus): submitting {len(arbiter_requests)} reconciliations...")
        arb_batch_id = submit_batch(arbiter_requests, label="deep_arbiter")
        parsed_arb, fail_arb = _parse_batch_results(poll_batch(arb_batch_id), cid_arb_to_pid)
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

    # Combine failures: a paper that failed BOTH reviewers OR succeeded with one
    # reviewer but the arbiter failed. fail_a uses "paper_id"; Gemini fail uses "key".
    combined_failures: list[dict] = []
    for f in fail_a:
        if f["paper_id"] not in parsed_b:
            combined_failures.append({**f, "reason": f"reviewer_a_{f['reason']}_AND_b_failed"})
    for f in fail_b_raw:
        if f["key"] not in parsed_a:
            combined_failures.append({"paper_id": f["key"], "detail": f.get("detail", ""),
                                      "reason": f"reviewer_b_{f['reason']}_AND_a_failed"})
    for f in fail_arb:
        combined_failures.append({**f, "reason": f"arbiter_{f['reason']}"})

    _write_outputs(final_extractions, combined_failures, len(deep_papers))
    # Consumed and persisted — release the paid Anthropic batch ids and B cache.
    forget_batch("deep_reviewers_a")
    forget_batch("deep_arbiter")
    _reviewer_b_cache_path().unlink(missing_ok=True)


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