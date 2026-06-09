"""Supabase client wrapper."""

from __future__ import annotations

from supabase import Client, create_client

from config.settings import settings

_client: Client | None = None


def sb() -> Client:
    global _client
    if _client is None:
        _client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    return _client


def upsert_paper(paper: dict) -> None:
    sb().table("papers").upsert(paper, on_conflict="id").execute()


def upsert_papers_batch(papers: list[dict]) -> None:
    sb().table("papers").upsert(papers, on_conflict="id").execute()


def upsert_extraction(paper_id: str, level: str, data: dict) -> None:
    row = {"paper_id": paper_id, "extraction_level": level, **data}
    sb().table("extractions").upsert(row, on_conflict="paper_id,extraction_level").execute()


def store_embedding(paper_id: str, vec: list[float]) -> None:
    sb().table("embeddings").upsert({"paper_id": paper_id, "embedding": vec}).execute()


def paper_already_extracted(paper_id: str, level: str) -> bool:
    result = (
        sb()
        .table("extractions")
        .select("paper_id")
        .eq("paper_id", paper_id)
        .eq("extraction_level", level)
        .execute()
    )
    return len(result.data) > 0


def store_provenance(paper_id: str, level: str, entries: list[dict]) -> None:
    """Bulk insert provenance entries for a paper. Skips entries with empty quote."""
    if not entries:
        return
    rows = [
        {
            "paper_id": paper_id,
            "extraction_level": level,
            "field_name": e.get("field", "unknown"),
            "claim": e.get("claim", "") or "",
            "quote": e.get("quote", "") or "",
            "section": e.get("section"),
            "page": e.get("page"),
            "confidence": e.get("confidence"),
        }
        for e in entries
        if e.get("quote")
    ]
    if rows:
        sb().table("provenance").insert(rows).execute()


def get_provenance_for_paper(paper_id: str) -> list[dict]:
    result = sb().table("provenance").select("*").eq("paper_id", paper_id).execute()
    return result.data or []


def get_provenance_for_claim(field_name: str) -> list[dict]:
    result = sb().table("provenance").select("*").eq("field_name", field_name).execute()
    return result.data or []
