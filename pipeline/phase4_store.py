"""Phase 4: Persist v2 extractions to Supabase."""

from __future__ import annotations

# __APP_PATHS_INSTALLED__
from app_paths import app_data

import json

from rich.console import Console

from utils.checkpointing import Checkpoint
from utils.supabase_client import store_provenance, upsert_extraction, upsert_papers_batch

console = Console()

PAPER_COLUMNS = {"id", "source", "title", "authors", "year", "journal", "abstract", "url"}


def project_paper(p: dict) -> dict:
    return {k: v for k, v in p.items() if k in PAPER_COLUMNS}


def map_triage_to_schema(d: dict) -> dict:
    """v2 triage → extractions row."""
    out: dict = {}
    if d.get("study_design"):
        out["study_design"] = d["study_design"]
    if d.get("sample_size") is not None:
        out["sample_size"] = d["sample_size"]
    if d.get("population_country"):
        out["population"] = d["population_country"]
    weeks = d.get("definition_threshold_weeks", d.get("long_covid_definition_weeks"))
    if weeks is not None:
        out["long_covid_definition"] = f">={weeks} weeks"
        out["definition_threshold_weeks"] = weeks
    focused = d.get("is_topic_focused", d.get("is_long_covid_focused"))
    if focused is not None:
        out["is_topic_focused"] = bool(focused)
    if syms := d.get("main_symptoms"):
        out["symptoms"] = {s: 1 for s in syms}
    if bios := d.get("main_biomarkers"):
        out["biomarkers"] = {b: True for b in bios}
    if rfs := d.get("risk_factors_identified"):
        out["risk_factors"] = {r: True for r in rfs}
    if d.get("headline_finding"):
        out["authors_conclusions"] = d["headline_finding"]
    if d.get("extraction_confidence") is not None:
        out["extraction_confidence"] = d["extraction_confidence"]
    if flags := d.get("confidence_flags"):
        out["confidence_flags"] = flags
    return out


def _flatten_str_list(items) -> list[str]:
    """Coerce a list of strings or {phenotype: x} dicts into list[str]."""
    out = []
    if not items:
        return out
    for it in items:
        if isinstance(it, str):
            out.append(it)
        elif isinstance(it, dict):
            v = it.get("phenotype") or it.get("mechanism") or it.get("name")
            if v:
                out.append(str(v))
    return out


def map_deep_to_schema(d: dict) -> dict:
    """v2 deep → extractions row using new columns."""
    out: dict = {}
    sm = d.get("study_metadata") or {}
    fx = d.get("factual_extraction") or {}
    ma = d.get("methodology_appraisal") or {}
    ba = d.get("bias_audit") or {}
    pm = d.get("phenotype_mapping") or {}
    cal = d.get("calibration") or {}

    if sm.get("design"):
        out["study_design"] = str(sm["design"])[:200]
    if sm.get("sample_size") is not None:
        out["sample_size"] = sm["sample_size"]
    if sm.get("population_description"):
        out["population"] = str(sm["population_description"])[:500]
    elif sm.get("population"):
        out["population"] = str(sm["population"])[:200]
    if sm.get("pandemic_era"):
        out["pandemic_era"] = str(sm["pandemic_era"])[:50]

    if fx.get("long_covid_definition"):
        out["long_covid_definition"] = str(fx["long_covid_definition"])[:500]
    if fx.get("symptoms_prevalence"):
        out["symptoms"] = fx["symptoms_prevalence"]
    if fx.get("biomarker_findings"):
        out["biomarkers"] = fx["biomarker_findings"]
    if fx.get("risk_factors_quantified"):
        out["risk_factors"] = fx["risk_factors_quantified"]

    lims = (ma.get("limitations_self_reported") or []) + (ma.get("limitations_inferred") or [])
    if lims:
        out["limitations"] = [str(x) for x in lims]
    if ma.get("grade_certainty"):
        out["grade_certainty"] = ma["grade_certainty"]
    if ma.get("grade_rationale"):
        out["grade_rationale"] = str(ma["grade_rationale"])[:2000]
    if ma.get("nos_score") is not None:
        try:
            out["nos_score"] = int(ma["nos_score"])
            out["methodology_quality"] = min(5, max(1, round(out["nos_score"] / 9 * 5)))
        except (TypeError, ValueError):
            pass

    if ba:
        out["bias_audit"] = ba

    if pm:
        out["phenotype_mapping"] = {
            "primary_mechanism": pm.get("primary_mechanism"),
            "secondary_mechanisms": _flatten_str_list(pm.get("secondary_mechanisms")),
            "phenotype_confidence": pm.get("phenotype_confidence"),
        }

    if cal.get("extraction_confidence") is not None:
        out["extraction_confidence"] = cal["extraction_confidence"]
    if cal.get("confidence_flags"):
        out["confidence_flags"] = cal["confidence_flags"]
    if cal.get("calibrated_certainty"):
        out["calibrated_certainty"] = cal["calibrated_certainty"]
    if cal.get("calibrated_certainty_rationale"):
        out["calibrated_certainty_rationale"] = str(cal["calibrated_certainty_rationale"])[:2000]
    if cal.get("uncertainty_sources"):
        out["uncertainty_sources"] = cal["uncertainty_sources"]
    if cal.get("probabilistic_summary"):
        out["probabilistic_summary"] = str(cal["probabilistic_summary"])[:500]

    if d.get("critical_notes"):
        out["authors_conclusions"] = str(d["critical_notes"])[:2000]

    # v3: arbiter + LLM judgment flagging
    if "reconciliation_triggered" in d:
        out["reconciliation_triggered"] = bool(d["reconciliation_triggered"])
    if "arbiter_notes" in d and d["arbiter_notes"]:
        out["arbiter_notes"] = str(d["arbiter_notes"])[:2000]
    if d.get("llm_judgment_flags"):
        out["llm_judgment_flags"] = d["llm_judgment_flags"]
    # Raw reviewer outputs kept for audit; trim long fields to fit jsonb.
    if d.get("reviewer_a_raw") is not None:
        out["reviewer_a_raw"] = d["reviewer_a_raw"]
    if d.get("reviewer_b_raw") is not None:
        out["reviewer_b_raw"] = d["reviewer_b_raw"]

    return out


def screen_retractions(deep_ids: set[str], papers_by_id: dict[str, dict]) -> list[dict]:
    """P2.2: cross-check each deep paper's DOI against Crossref. Writes
    data/filtered/retracted.jsonl (consumed by Phase 5) and updates the papers
    table. Bounded to the deep set so Crossref is never hit thousands of times.
    Returns the list of retraction records."""
    import httpx

    from config.settings import settings
    from utils.retraction import check_crossref_retraction

    if not settings.RETRACTION_CHECK_ENABLED:
        return []

    retracted: list[dict] = []
    ua = {"User-Agent": "LitSynthEngine/3.1 (mailto:research@example.com)"}
    with httpx.Client(headers=ua, timeout=15) as client:
        for pid in sorted(deep_ids):
            doi = (papers_by_id.get(pid) or {}).get("doi")
            if not doi:
                continue
            info = check_crossref_retraction(doi, client=client)
            if info and info.get("is_retracted"):
                retracted.append({"paper_id": pid, "doi": doi, **info})

    out = app_data("data/filtered/retracted.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for rec in retracted:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    if retracted:
        console.print(f"[red]Retraction screen: {len(retracted)} retracted paper(s) flagged[/]")
        try:
            from utils.supabase_client import sb

            for rec in retracted:
                sb().table("papers").update(
                    {
                        "is_retracted": True,
                        "retraction_doi": rec.get("retraction_doi"),
                        "retraction_date": rec.get("retraction_date"),
                    }
                ).eq("id", rec["paper_id"]).execute()
        except Exception as e:
            console.print(f"[yellow]Could not persist retraction flags: {e}[/]")
    else:
        console.print("[green]Retraction screen: no retractions found in deep set[/]")
    return retracted


def run() -> None:
    checkpoint = Checkpoint("phase4_store")
    if checkpoint.is_complete():
        console.print("[green]Phase 4 already complete. Skipping.[/]")
        return

    console.print("[bold cyan]Phase 4: Persisting to Supabase[/]")

    papers_path = app_data("data/raw/papers.jsonl")
    triage_path = app_data("data/filtered/triage_results.jsonl")

    # Index raw papers (with DOIs) for the retraction screen below.
    papers_raw_by_id: dict[str, dict] = {}
    if papers_path.exists():
        for line in papers_path.open(encoding="utf-8"):
            try:
                p = json.loads(line)
                papers_raw_by_id[p["id"]] = p
            except (json.JSONDecodeError, KeyError):
                pass

    if papers_path.exists():
        papers = [project_paper(json.loads(line)) for line in papers_path.open(encoding="utf-8")]
        for i in range(0, len(papers), 100):
            batch = papers[i : i + 100]
            upsert_papers_batch(batch)
        console.print(f"Upserted {len(papers)} papers")

    if triage_path.exists():
        count = 0
        for line in triage_path.open(encoding="utf-8"):
            data = json.loads(line)
            paper_id = data.pop("paper_id")
            mapped = map_triage_to_schema(data)
            upsert_extraction(paper_id, "abstract", mapped)
            count += 1
        console.print(f"Upserted {count} triage extractions")

    deep_path = app_data("data/filtered/deep_results.jsonl")
    deep_ids: set[str] = set()
    if deep_path.exists():
        count = 0
        prov_total = 0
        for line in deep_path.open(encoding="utf-8"):
            data = json.loads(line)
            paper_id = data.pop("paper_id")
            deep_ids.add(paper_id)
            provenance_entries = data.pop("provenance", None) or []
            mapped = map_deep_to_schema(data)
            upsert_extraction(paper_id, "fulltext", mapped)
            if provenance_entries:
                try:
                    store_provenance(paper_id, "fulltext", provenance_entries)
                    prov_total += len(provenance_entries)
                except Exception as e:
                    console.print(f"[yellow]Provenance store failed for {paper_id}: {e}[/]")
            count += 1
        console.print(f"Upserted {count} deep extractions, {prov_total} provenance entries")

    # P2.2: retraction screen over the deep set (writes retracted.jsonl for Phase 5).
    if deep_ids:
        screen_retractions(deep_ids, papers_raw_by_id)

    # v3: persist UMLS-normalised entities to extracted_phenotypes
    norm_path = app_data("data/filtered/normalized_entities.jsonl")
    if norm_path.exists():
        from utils.supabase_client import sb

        rows = []
        for line in norm_path.open(encoding="utf-8"):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            pid = rec.get("paper_id")
            for e in rec.get("entities") or []:
                rows.append(
                    {
                        "paper_id": pid,
                        "verbatim_text": e.get("verbatim_text", "")[:500],
                        "umls_cui": e.get("umls_cui") or None,
                        "mesh_heading": e.get("mesh_heading") or None,
                        "entity_type": e.get("entity_type"),
                        "llm_judgment": bool(e.get("llm_judgment", True)),
                        # P2.1: real-UMLS verification result
                        "cui_verified": bool(e.get("cui_verified", False)),
                        "preferred_name": e.get("preferred_name") or None,
                    }
                )
        if rows:
            try:
                for i in range(0, len(rows), 200):
                    sb().table("extracted_phenotypes").insert(rows[i : i + 200]).execute()
                console.print(f"Inserted {len(rows)} normalised phenotype rows")
            except Exception as e:
                console.print(f"[yellow]extracted_phenotypes insert failed: {e}[/]")

    checkpoint.mark_complete()
    console.print("[green]Phase 4 complete.[/]")


if __name__ == "__main__":
    run()
