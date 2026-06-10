"""OpenAlex discovery source (UPGRADE v3.1 — P4.1).

Adds non-PMC discovery (incl. server-side preprint search that replaces the slow
client-side medRxiv scan) via the OpenAlex works API. No key required; we send a
``mailto`` for the polite pool. Dedup is by normalised DOI, and PMC always wins a
tie because we already hold its full text.

The parsing helpers (``reconstruct_abstract``, ``parse_work``, ``normalize_doi``)
are pure and unit-tested without network.
"""

from __future__ import annotations

import httpx
from rich.console import Console

from config.settings import settings

console = Console()

_OPENALEX = "https://api.openalex.org/works"
# OpenAlex source id for medRxiv (used to scope the preprint filter).
MEDRXIV_SOURCE_ID = "S4306400573"


def normalize_doi(doi: str | None) -> str:
    """Lowercase, strip the URL prefix and any whitespace. '' for falsy input."""
    if not doi:
        return ""
    d = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if d.startswith(prefix):
            d = d[len(prefix) :]
    return d.strip()


def reconstruct_abstract(inverted_index: dict | None) -> str | None:
    """OpenAlex ships abstracts as an inverted index {word: [positions]}.
    Rebuild the linear text. Returns None when absent."""
    if not inverted_index:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted_index.items():
        for i in idxs:
            positions.append((i, word))
    if not positions:
        return None
    positions.sort()
    return " ".join(word for _, word in positions)


def parse_work(work: dict) -> dict | None:
    """Map an OpenAlex work to the pipeline's paper schema. None if no abstract."""
    abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
    if not abstract:
        return None
    doi = normalize_doi(work.get("doi"))
    authorships = work.get("authorships") or []
    authors = [a.get("author", {}).get("display_name") for a in authorships]
    authors = [a for a in authors if a]

    primary = work.get("primary_location") or {}
    source = primary.get("source") or {}
    journal = source.get("display_name")
    is_preprint = (work.get("type") == "preprint") or (source.get("id", "").endswith(MEDRXIV_SOURCE_ID))

    pmcid = None
    ids = work.get("ids") or {}
    if ids.get("pmcid"):
        pmcid = str(ids["pmcid"]).rsplit("/", 1)[-1].replace("PMC", "")

    # Stable id: prefer a PMC id (so it dedups/merges with the PMC source),
    # else use the normalised DOI.
    if pmcid:
        pid = f"PMC{pmcid}"
    elif doi:
        pid = f"openalex_{doi.replace('/', '_')}"
    else:
        pid = f"openalex_{(work.get('id') or '').rsplit('/', 1)[-1]}"

    return {
        "id": pid,
        "source": "medrxiv" if is_preprint else "openalex",
        "title": work.get("title") or work.get("display_name"),
        "abstract": abstract,
        "authors": authors,
        "year": work.get("publication_year"),
        "journal": journal or ("medRxiv (preprint)" if is_preprint else None),
        "url": (primary.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else None)),
        "doi": doi or None,
        "full_text": None,
        "pmc_id": pmcid,
    }


def search_openalex(
    query: str,
    *,
    mailto: str = "",
    max_results: int = 1000,
    preprints_only: bool = False,
    from_year: int = 2000,
) -> list[dict]:
    """Cursor-paginated OpenAlex search. Returns parsed paper dicts."""
    mailto = mailto or settings.OPENALEX_MAILTO or settings.NCBI_EMAIL
    headers = {"User-Agent": f"LitSynthEngine/3.1 (mailto:{mailto or 'research@example.com'})"}
    filters = [f"from_publication_date:{from_year}-01-01"]
    if preprints_only:
        filters.append("type:preprint")
    params_base = {
        "search": query,
        "filter": ",".join(filters),
        "per-page": 200,
        "mailto": mailto or "research@example.com",
    }

    # Circuit breaker (Gemini sprint P2): a flapping OpenAlex trips after a few
    # failed pages so we stop hammering it; the degradation is surfaced in the
    # QA sheet via degraded_services, never hidden.
    from utils.resilience import breaker

    cb = breaker("openalex", failure_threshold=5)
    out: list[dict] = []
    cursor = "*"
    try:
        with httpx.Client(headers=headers, timeout=30) as client:
            while cursor and len(out) < max_results:
                if not cb.allow():
                    console.print("[yellow]OpenAlex circuit breaker tripped; stopping discovery[/]")
                    break
                params = {**params_base, "cursor": cursor}
                try:
                    r = client.get(_OPENALEX, params=params)
                except httpx.HTTPError:
                    cb.record_failure()
                    continue
                if r.status_code != 200:
                    cb.record_failure()
                    break
                cb.record_success()
                data = r.json()
                for work in data.get("results") or []:
                    parsed = parse_work(work)
                    if parsed:
                        out.append(parsed)
                    if len(out) >= max_results:
                        break
                cursor = (data.get("meta") or {}).get("next_cursor")
    except httpx.HTTPError as e:
        console.print(f"[yellow]OpenAlex error: {e}[/]")
    return out[:max_results]


def dedup_against(new_papers: list[dict], existing_dois: set[str]) -> list[dict]:
    """Drop new papers whose normalised DOI already exists (PMC wins). Also
    dedups within the new batch."""
    seen = set(existing_dois)
    kept: list[dict] = []
    for p in new_papers:
        d = normalize_doi(p.get("doi"))
        if d and d in seen:
            continue
        if d:
            seen.add(d)
        kept.append(p)
    return kept
