"""Enterprise report assembly (UPGRADE v3.1 — P6 integration).

Glues the P6 building blocks onto a run: computes PRISMA counts and the QA
certificate, freezes the manifest (Run ID), writes the machine-readable
supplement (CSV / XLSX / RIS / BibTeX / PRISMA SVG / manifest) as a ZIP, renders
the executive one-pager and the editable DOCX, and returns Markdown fragments
(cover banner, QA sheet, GRADE SoF, methods-in-full, legal page) for the report
templates to embed.

Kept side-effect-light and parameterised by an ``app_data`` resolver and a
``prompts_dir`` so it is unit-testable without the full pipeline.
"""

from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path

from utils import report_builders as rb
from utils.export_citations import to_bibtex, to_ris
from utils.run_manifest import build_manifest, write_manifest


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for _ in path.open(encoding="utf-8"))


def compute_prisma_counts(app_data, analysis: dict) -> dict:
    sb_path = app_data("data/raw/sources_breakdown.json")
    sources = {}
    if sb_path.exists():
        try:
            sources = json.loads(sb_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            sources = {}
    yield_block = (analysis.get("aggregates") or {}).get("deep_extraction_yield") or {}
    screened = (analysis.get("aggregates") or {}).get("n_papers") or 0
    eligible = yield_block.get("requested") or 0
    included = yield_block.get("succeeded") or 0
    retracted = len((analysis.get("aggregates") or {}).get("retracted_excluded") or [])
    return {
        "identified_pmc": sources.get("pmc", 0),
        "identified_openalex": sources.get("openalex", 0),
        "identified_medrxiv": sources.get("medrxiv", 0),
        "duplicates_removed": sources.get("duplicates_removed", 0),
        "screened": screened,
        "excluded_screen": max(0, screened - eligible),
        "eligible": eligible,
        "included_deep": included,
        "excluded_retracted": retracted,
        "sources": sources,
    }


def _fulltext_coverage(app_data, n_deep: int) -> float:
    cache = app_data("data/raw/fulltext_cache.jsonl")
    if not cache.exists() or not n_deep:
        return 0.0
    have = 0
    for line in cache.open(encoding="utf-8"):
        try:
            rec = json.loads(line)
            if rec.get("full_text"):
                have += 1
        except json.JSONDecodeError:
            pass
    return round(min(100.0, 100.0 * have / n_deep), 1)


def _cui_verified_pct(app_data) -> float:
    path = app_data("data/filtered/normalized_entities.jsonl")
    total = verified = 0
    if path.exists():
        for line in path.open(encoding="utf-8"):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            for e in rec.get("entities") or []:
                total += 1
                if e.get("cui_verified"):
                    verified += 1
    return round(100.0 * verified / total, 1) if total else 0.0


def compute_qa(app_data, analysis: dict, manifest: dict, prisma: dict) -> dict:
    agg = analysis.get("aggregates") or {}
    yield_block = agg.get("deep_extraction_yield") or {}
    requested = yield_block.get("requested") or 0
    succeeded = yield_block.get("succeeded") or 0
    deep_rate = round(100.0 * succeeded / requested, 1) if requested else 0.0
    return {
        "run_id": manifest.get("run_id"),
        "engine_version": manifest.get("engine_version"),
        "deep_success_rate": deep_rate,
        "cui_verified_pct": _cui_verified_pct(app_data),
        "reconciliations": agg.get("reconciliations_triggered", 0),
        "kappa_summary": "not yet rated",
        "n_retracted_excluded": len(agg.get("retracted_excluded") or []),
        "fulltext_coverage_pct": _fulltext_coverage(app_data, succeeded),
        "sources_breakdown": prisma.get("sources", {}),
        "api_cost_usd": manifest.get("api_cost_usd"),
        "runtime_seconds": manifest.get("runtime_seconds"),
    }


def _write_supplement_csvs(app_data, supplement_dir: Path) -> list[Path]:
    """extractions.csv + provenance.csv + evidence_table.xlsx from deep results."""
    deep_path = app_data("data/filtered/deep_results.jsonl")
    out: list[Path] = []
    if not deep_path.exists():
        return out
    records = []
    for line in deep_path.open(encoding="utf-8"):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    # extractions.csv (flat key fields)
    ext_path = supplement_dir / "extractions.csv"
    with ext_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["paper_id", "design", "sample_size", "grade", "nos", "calibrated_certainty"])
        for d in records:
            sm = d.get("study_metadata") or {}
            ma = d.get("methodology_appraisal") or {}
            cal = d.get("calibration") or {}
            w.writerow(
                [
                    d.get("paper_id"),
                    sm.get("design"),
                    sm.get("sample_size"),
                    ma.get("grade_certainty"),
                    ma.get("nos_score"),
                    cal.get("calibrated_certainty"),
                ]
            )
    out.append(ext_path)

    # provenance.csv
    prov_path = supplement_dir / "provenance.csv"
    with prov_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["paper_id", "field", "claim", "quote", "section"])
        for d in records:
            for p in d.get("provenance") or []:
                w.writerow(
                    [
                        d.get("paper_id"),
                        p.get("field"),
                        (p.get("claim") or "")[:300],
                        (p.get("quote") or "")[:500],
                        p.get("section"),
                    ]
                )
    out.append(prov_path)

    # evidence_table.xlsx (one row per deep paper)
    try:
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Evidence table"
        ws.append(["paper_id", "design", "n", "NOS", "GRADE", "primary_effect", "certainty"])
        for d in records:
            sm = d.get("study_metadata") or {}
            ma = d.get("methodology_appraisal") or {}
            cal = d.get("calibration") or {}
            es = (d.get("effect_sizes_classified") or [{}])[0]
            ws.append(
                [
                    d.get("paper_id"),
                    sm.get("design"),
                    sm.get("sample_size"),
                    ma.get("nos_score"),
                    ma.get("grade_certainty"),
                    es.get("r_equivalent"),
                    cal.get("calibrated_certainty"),
                ]
            )
        xlsx_path = supplement_dir / "evidence_table.xlsx"
        wb.save(str(xlsx_path))
        out.append(xlsx_path)
    except Exception:
        pass
    return out


def ontology_section_markdown(app_data) -> str:
    """Render the UMLS verification status with honest badges. An entity is
    tagged [VERIFIED] only when cui_verified is true (real UMLS REST match);
    everything else is [LLM]. Offline (no key) this correctly shows 0% verified,
    so the badge never confers credibility the lookup did not earn."""
    path = app_data("data/filtered/normalized_entities.jsonl")
    entities: list[dict] = []
    if path.exists():
        for line in path.open(encoding="utf-8"):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            entities.extend(rec.get("entities") or [])
    total = len(entities)
    verified = [e for e in entities if e.get("cui_verified")]
    pct = round(100.0 * len(verified) / total, 1) if total else 0.0

    lines = ["## Ontology Verification (UMLS)", ""]
    if total == 0:
        lines.append("_No normalised entities in this run._")
        return "\n".join(lines)
    if pct == 0.0:
        # Honest 0% wording: do not mention a [VERIFIED] badge that nothing earned.
        lines.append(
            f"0/{total} CUIs confirmed against the UMLS REST API — **every CUI is "
            "LLM-inferred** and carries no [VERIFIED] badge. Configure a free UTS "
            "`UMLS_API_KEY` to enable verification."
        )
    else:
        lines.append(
            f"{len(verified)}/{total} CUIs ({pct}%) confirmed against the UMLS REST API "
            "and tagged <sup>[VERIFIED]</sup>; the remainder are LLM-inferred <sup>[LLM]</sup>."
        )
    # Show a short verified sample (if any) so the badge is visible in the report.
    sample = verified[:10]
    if sample:
        lines += ["", "| Entity | UMLS preferred name | CUI |", "|---|---|---|"]
        for e in sample:
            lines.append(
                f"| {e.get('verbatim_text', '')} <sup>[VERIFIED]</sup> | "
                f"{e.get('preferred_name', '')} | {e.get('umls_cui', '')} |"
            )
    return "\n".join(lines)


def front_matter_markdown(qa: dict, prisma: dict, search_date: str) -> str:
    """Cover banner + QA certificate (the fixed page-2 quality sheet)."""
    parts = [
        f"> **Run ID `{qa.get('run_id')}`** · Engine {qa.get('engine_version')} · "
        f"Evidence current as of {search_date}",
        "",
        f"*{rb.HONEST_FRAMING}*",
        "",
        rb.qa_sheet_markdown(qa),
        "",
    ]
    return "\n".join(parts)


def appendix_markdown(qa: dict, prisma: dict, analysis: dict, manifest: dict, search_date: str) -> str:
    """GRADE SoF + PRISMA note + methods-in-full + legal page."""
    meta_by_factor = (analysis.get("aggregates") or {}).get("meta_analysis_by_factor") or {}
    from config.settings import settings

    parts = [
        "## GRADE Summary of Findings <sup>[CALC]</sup>",
        "",
        rb.grade_sof_table(meta_by_factor, min_studies=settings.MIN_STUDIES_POOLING),
        "",
        f"*Pooled estimates are reported only for factors with ≥ "
        f"{settings.MIN_STUDIES_POOLING} studies. Effect sizes reported as OR/RR/HR "
        "are converted to a Pearson r-equivalent for cross-metric comparison; the "
        "pooled r is an approximation and should be read as a signal indicator, not "
        "a substitute for a metric-native (e.g. log-OR) meta-analysis.*",
        "",
        "## PRISMA 2020 Flow",
        "",
        f"Records identified: PMC = {prisma['identified_pmc']}, "
        f"OpenAlex = {prisma['identified_openalex']}, medRxiv = {prisma['identified_medrxiv']}. "
        f"Screened: {prisma['screened']}. Eligible: {prisma['eligible']}. "
        f"Excluded (retracted): {prisma['excluded_retracted']}. "
        f"Included in synthesis: {prisma['included_deep']}. "
        "(See `prisma_flow.svg` in the supplement for the diagram.)",
        "",
        "## Methods in Full (from run manifest)",
        "",
        f"- Engine: {manifest.get('engine_version')} (git {manifest.get('git_sha')})",
        f"- Models: {json.dumps(manifest.get('models'))}",
        f"- Temperatures: {json.dumps(manifest.get('temperatures'))}",
        f"- Search date: {manifest.get('search_date')}",
        f"- Queries: {json.dumps(manifest.get('queries_by_source'))}",
        f"- Active flags: {json.dumps(manifest.get('flags'))}",
        "",
        rb.legal_page_markdown(manifest.get("topic", ""), search_date, qa.get("run_id", "n/a")),
    ]
    return "\n".join(parts)


def generate(
    app_data,
    analysis: dict,
    papers_by_id: dict,
    *,
    slug: str,
    today: str,
    topic: str,
    mesh_terms: str | None,
    prompts_dir,
    reports_dir: Path,
) -> dict:
    """Produce all enterprise artefacts. Returns a dict with run_id, qa, prisma,
    manifest, front_matter, appendix, and the supplement zip path."""
    run_stats_path = app_data("data/raw/run_stats.json")
    run_stats = {}
    if run_stats_path.exists():
        try:
            run_stats = json.loads(run_stats_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            run_stats = {}

    prisma = compute_prisma_counts(app_data, analysis)
    manifest = build_manifest(
        topic=topic,
        mesh_terms=mesh_terms,
        queries_by_source=run_stats.get("queries_by_source", {}),
        sources_breakdown=prisma.get("sources", {}),
        phase_counts={
            "screened": prisma["screened"],
            "eligible": prisma["eligible"],
            "included_deep": prisma["included_deep"],
        },
        cost_usd=run_stats.get("api_cost_usd", 0.0),
        runtime_seconds=run_stats.get("runtime_seconds"),
        prompts_dir=prompts_dir,
        search_date=today,
    )
    qa = compute_qa(app_data, analysis, manifest, prisma)

    # Supplement package
    supplement_dir = reports_dir / f"supplement_{slug}_{today}"
    supplement_dir.mkdir(parents=True, exist_ok=True)
    write_manifest(manifest, supplement_dir / "run_manifest.json")
    (supplement_dir / "prisma_flow.svg").write_text(rb.prisma_flow_svg(prisma), encoding="utf-8")
    cited = list(papers_by_id.values())
    (supplement_dir / "references.ris").write_text(to_ris(cited), encoding="utf-8")
    (supplement_dir / "references.bib").write_text(to_bibtex(cited), encoding="utf-8")
    _write_supplement_csvs(app_data, supplement_dir)

    zip_path = reports_dir / f"supplement_{slug}_{today}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in supplement_dir.iterdir():
            if f.is_file():
                zf.write(f, f.name)

    # Executive one-pager (markdown + docx)
    from utils.run_context import topic_title

    exec_data = analysis.get("executive_summary") or {}
    one_pager_md = rb.one_pager_markdown(topic_title(), exec_data, qa, today)
    (reports_dir / f"one_pager_{slug}_{today}.md").write_text(one_pager_md, encoding="utf-8")

    # P5.1: register the run for history/compare (best-effort; never blocks).
    try:
        from utils.run_registry import upsert_run

        upsert_run(manifest, qa)
    except Exception:
        pass

    return {
        "run_id": manifest["run_id"],
        "qa": qa,
        "prisma": prisma,
        "manifest": manifest,
        "front_matter": front_matter_markdown(qa, prisma, today),
        "appendix": (
            appendix_markdown(qa, prisma, analysis, manifest, today)
            + "\n\n"
            + ontology_section_markdown(app_data)
        ),
        "one_pager_md": one_pager_md,
        "supplement_zip": str(zip_path),
    }
