"""Phase 1: Ingest papers from PMC + medRxiv (v2 — no CORD-19)."""

from __future__ import annotations

# __APP_PATHS_INSTALLED__
from app_paths import app_data

import asyncio
import json
from datetime import date, timedelta

import httpx
from Bio import Entrez
from lxml import etree
from rich.console import Console
from rich.progress import Progress
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from pipeline.sources.openalex import normalize_doi
from utils.checkpointing import Checkpoint

console = Console()
Entrez.email = settings.NCBI_EMAIL
Entrez.api_key = settings.NCBI_API_KEY


def expand_search_terms(topic: str) -> list[str]:
    """Single Haiku call to expand a condition into MeSH terms and synonyms.
    Cost: ~$0.001. Falls back to [topic] on any error.
    """
    from utils.claude_client import client

    prompt = (
        f'You are a medical librarian. For the condition "{topic}", '
        "return a JSON array of search terms that a systematic review would use: "
        "MeSH headings, common synonyms, abbreviations, and closely related conditions "
        "found in scientific literature. Maximum 15 terms. "
        "Return ONLY a JSON array of strings, no other text."
    )
    try:
        response = client.messages.create(
            model=settings.ANTHROPIC_HAIKU_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        try:
            terms = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("[")
            end = raw.rfind("]")
            if start < 0 or end <= start:
                raise
            terms = json.loads(raw[start : end + 1])
        if isinstance(terms, list):
            clean = [t.strip() for t in terms if isinstance(t, str) and t.strip()]
            if clean:
                return clean[:15]
    except Exception as e:
        console.print(f"[yellow]MeSH expansion failed ({e}); using topic as-is[/]")
    return [topic]


LONG_COVID_QUERY = (
    '("long covid"[Title/Abstract] OR "post-acute sequelae"[Title/Abstract] '
    'OR "PASC"[Title/Abstract] OR "post-COVID condition"[Title/Abstract]) '
    'AND ("2020"[PDAT] : "3000"[PDAT])'
)


def build_query(
    topic: str | None = None,
    mesh_terms: str | None = None,
    start_year: int = 2000,
    synonyms: list[str] | None = None,
) -> str:
    """Build a PubMed query combining a free-text topic with optional MeSH terms.

    If *synonyms* are provided (from Haiku MeSH expansion), they are OR-joined
    into the Title/Abstract clause for broader coverage.
    Explicit *mesh_terms* are AND-joined as a filter on top.
    """
    topic_clean = (topic or "").strip()
    if topic_clean and topic_clean.lower() != "long covid":
        all_terms = [topic_clean]
        if synonyms:
            for s in synonyms:
                if s.lower() != topic_clean.lower():
                    all_terms.append(s)
        title_abs = " OR ".join(f'"{t}"[Title/Abstract]' for t in all_terms)
        base = f'({title_abs}) AND ("{start_year}"[PDAT] : "3000"[PDAT])'
    else:
        base = LONG_COVID_QUERY
    if mesh_terms and mesh_terms.strip():
        base = f"({base}) AND ({mesh_terms.strip()})"
    # P2.2: exclude retracted publications by default (configurable).
    if not settings.INCLUDE_RETRACTED:
        base = f'({base}) NOT "Retracted Publication"[Publication Type]'
    return base


MEDRXIV_SEARCH_URL = "https://api.biorxiv.org/details/medrxiv"


@retry(stop=stop_after_attempt(5), wait=wait_exponential(min=2, max=60))
def fetch_pmc_ids(query: str, retmax: int = 10000) -> list[str]:
    handle = Entrez.esearch(db="pmc", term=query, retmax=retmax)
    result = Entrez.read(handle)
    handle.close()
    return result["IdList"]


@retry(stop=stop_after_attempt(5), wait=wait_exponential(min=2, max=60))
async def fetch_pmc_metadata(pmc_id: str, client: httpx.AsyncClient) -> dict | None:
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params = {
        "db": "pmc",
        "id": pmc_id,
        "rettype": "xml",
        "api_key": settings.NCBI_API_KEY,
    }
    r = await client.get(url, params=params, timeout=30)
    r.raise_for_status()
    return parse_pmc_xml(r.text, pmc_id)


def parse_pmc_xml(xml: str, pmc_id: str) -> dict | None:
    """Parse PMC XML into standard schema. Returns None if abstract missing."""
    try:
        root = etree.fromstring(xml.encode())
    except etree.XMLSyntaxError:
        return None

    def get_text(xpath: str) -> str | None:
        nodes = root.xpath(xpath)
        if not nodes:
            return None
        return " ".join("".join(n.itertext()) for n in nodes).strip() or None

    title = get_text(".//article-title") or get_text(".//title")
    abstract = get_text(".//abstract")
    if not abstract:
        return None

    year_nodes = root.xpath(".//pub-date/year/text()")
    year = int(year_nodes[0]) if year_nodes else None

    authors = []
    # P7/F-bug: scope author extraction to the article front and explicitly
    # exclude any contrib living inside the bibliography (<ref-list>/<back>), so
    # malformed PMC XML can never bleed citation names into the authors field.
    author_nodes = root.xpath(
        ".//contrib[@contrib-type='author'][not(ancestor::ref-list)][not(ancestor::back)]"
    )
    for contrib in author_nodes:
        surname = "".join(contrib.xpath(".//surname/text()"))
        given = "".join(contrib.xpath(".//given-names/text()"))
        name = f"{given} {surname}".strip()
        if name:
            authors.append(name)

    doi_nodes = root.xpath(".//article-id[@pub-id-type='doi']/text()")
    doi = doi_nodes[0].strip() if doi_nodes else None

    journal_nodes = root.xpath(".//journal-title/text()")
    journal = journal_nodes[0].strip() if journal_nodes else None

    return {
        "id": f"PMC{pmc_id}",
        "source": "pmc",
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "year": year,
        "journal": journal,
        "url": f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmc_id}/",
        "doi": doi,
        "full_text": None,
        "pmc_id": pmc_id,
    }


async def fetch_pmc_fulltext(pmc_id: str, client: httpx.AsyncClient) -> str | None:
    """Fetch full structured text from PMC OA service.

    Uses utils.xml_parser.extract_structured_sections() to bucket content into
    metadata / methods / results / discussion_limitations / conflicts_funding,
    each with its own character cap (Master Improvement Spec v3.0 — Priority 1.2).
    Returns the sections concatenated with clear delimiters for the legacy
    extraction prompt; the structured dict is stored separately by enrich
    callers via fetch_pmc_fulltext_structured() when needed.
    """
    structured = await fetch_pmc_fulltext_structured(pmc_id, client)
    if not structured:
        return None
    from utils.xml_parser import sections_to_compact_text

    compact = sections_to_compact_text(structured)
    return compact or None


async def fetch_pmc_fulltext_structured(pmc_id: str, client: httpx.AsyncClient) -> dict | None:
    """Lower-level fetch that returns the structured-section dict instead of a
    concatenated string. Used by the v3 deep extraction path that injects
    sections separately into the Sonnet prompt.
    """
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params = {
        "db": "pmc",
        "id": pmc_id,
        "rettype": "full",
        "retmode": "xml",
        "api_key": settings.NCBI_API_KEY,
    }
    try:
        r = await client.get(url, params=params, timeout=60)
        r.raise_for_status()
    except httpx.HTTPError:
        return None

    from utils.xml_parser import extract_structured_sections

    sections = extract_structured_sections(r.content)
    if not any(sections.values()):
        return None
    return sections


def _doi_index() -> dict[str, str]:
    """Map paper_id → DOI from the ingested corpus (for Unpaywall fallback)."""
    out: dict[str, str] = {}
    for name in ("data/filtered/relevant_papers.jsonl", "data/raw/papers.jsonl"):
        path = app_data(name)
        if path.exists():
            for line in path.open(encoding="utf-8"):
                try:
                    p = json.loads(line)
                    if p.get("doi"):
                        out.setdefault(p["id"], p["doi"])
                except (json.JSONDecodeError, KeyError):
                    pass
    return out


async def enrich_with_fulltext(paper_ids: list[str]) -> None:
    """Fetch and store full text for given paper IDs.

    UPGRADE v3.1 — P4.2: when PMC OA has no full text (non-PMC ids, or PMC miss),
    fall back to an Unpaywall-declared OA PDF. The source of each full text is
    recorded so the report can break down full-text coverage by route.
    """
    from utils.supabase_client import sb

    cache_path = app_data("data/raw/fulltext_cache.jsonl")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cached_ids: set[str] = set()
    if cache_path.exists():
        for line in cache_path.open(encoding="utf-8"):
            try:
                cached_ids.add(json.loads(line)["paper_id"])
            except (json.JSONDecodeError, KeyError):
                pass

    doi_index = _doi_index()
    ok_pmc = 0
    ok_unpaywall = 0
    skipped_cached = 0
    miss = 0

    def _persist(cache_f, pid: str, text: str, source: str) -> None:
        try:
            sb().table("papers").update({"full_text": text}).eq("id", pid).execute()
        except Exception:
            pass  # Supabase optional in local/dev
        cache_f.write(json.dumps({"paper_id": pid, "full_text": text, "fulltext_source": source}) + "\n")

    async with httpx.AsyncClient(http2=True, limits=httpx.Limits(max_connections=3)) as client:
        with cache_path.open("a", encoding="utf-8") as cache_f:
            for pid in paper_ids:
                if pid in cached_ids:
                    skipped_cached += 1
                    continue
                full_text = None
                if pid.startswith("PMC"):
                    full_text = await fetch_pmc_fulltext(pid.replace("PMC", ""), client)
                    if full_text:
                        _persist(cache_f, pid, full_text, "pmc_oa")
                        ok_pmc += 1
                        await asyncio.sleep(0.4)
                        continue
                # P4.2 fallback: Unpaywall OA PDF (covers medRxiv/non-PMC + PMC misses).
                doi = doi_index.get(pid)
                if doi and settings.UNPAYWALL_ENABLED:
                    from pipeline.sources.unpaywall import fetch_fulltext_via_unpaywall

                    text = await asyncio.to_thread(fetch_fulltext_via_unpaywall, doi)
                    if text:
                        _persist(cache_f, pid, text, "unpaywall_flat_pdf")
                        ok_unpaywall += 1
                        await asyncio.sleep(0.2)
                        continue
                miss += 1
                await asyncio.sleep(0.2)

    console.print(
        f"Full-text enrichment: {ok_pmc} PMC OA, {ok_unpaywall} Unpaywall, "
        f"{skipped_cached} cached, {miss} unavailable"
    )


async def fetch_medrxiv_papers(
    query_terms: list[str],
    start_date: str = "2020-01-01",
    end_date: str | None = None,
    max_results: int = 1000,
) -> list[dict]:
    """Fetch preprints from medRxiv via biorxiv.org date-interval API.

    The API returns ALL papers in the date range (not keyword-filtered),
    so we split into 90-day chunks to stay within API limits and filter
    client-side by checking query_terms against title+abstract.
    """
    end = end_date or date.today().isoformat()
    start_dt = date.fromisoformat(start_date)
    end_dt = date.fromisoformat(end)

    intervals: list[tuple[str, str]] = []
    chunk_start = start_dt
    while chunk_start < end_dt:
        chunk_end = min(chunk_start + timedelta(days=90), end_dt)
        intervals.append((chunk_start.isoformat(), chunk_end.isoformat()))
        chunk_start = chunk_end + timedelta(days=1)

    results: list[dict] = []
    terms_lower = [t.lower() for t in query_terms if t.strip()]
    seen_dois: set[str] = set()
    total_scanned = 0

    console.print(f"[dim]medRxiv: scanning {len(intervals)} intervals for terms {terms_lower[:5]}[/]")

    async with httpx.AsyncClient(timeout=45) as client:
        for iv_start, iv_end in intervals:
            if len(results) >= max_results:
                break
            cursor = 0
            retries = 0
            while True:
                url = f"{MEDRXIV_SEARCH_URL}/{iv_start}/{iv_end}/{cursor}/json"
                try:
                    r = await client.get(url)
                    r.raise_for_status()
                    data = r.json()
                except httpx.TimeoutException:
                    retries += 1
                    if retries > 3:
                        console.print(f"[yellow]medRxiv: timeout on {iv_start}, skipping interval[/]")
                        break
                    await asyncio.sleep(2)
                    continue
                except Exception as e:
                    console.print(f"[yellow]medRxiv error ({iv_start} cursor {cursor}): {e}[/]")
                    break

                collection = data.get("collection", [])
                msgs = data.get("messages") or [{}]
                total_in_interval = int(msgs[0].get("total") or 0)
                if not collection:
                    break

                total_scanned += len(collection)
                for item in collection:
                    doi = item.get("doi", "")
                    if doi in seen_dois:
                        continue
                    text = ((item.get("title") or "") + " " + (item.get("abstract") or "")).lower()
                    if any(t in text for t in terms_lower):
                        seen_dois.add(doi)
                        authors_raw = item.get("authors", "")
                        if isinstance(authors_raw, str):
                            authors = [a.strip() for a in authors_raw.split(";") if a.strip()]
                        else:
                            authors = [str(authors_raw)]
                        results.append(
                            {
                                "id": f"medrxiv_{doi.replace('/', '_')}",
                                "source": "medrxiv",
                                "title": item.get("title"),
                                "abstract": item.get("abstract"),
                                "authors": authors,
                                "year": int((item.get("date") or "2020")[:4]),
                                "journal": "medRxiv (preprint)",
                                "url": f"https://www.medrxiv.org/content/{doi}",
                                "doi": doi,
                                "full_text": None,
                                "pmc_id": None,
                            }
                        )

                cursor += len(collection)
                if total_in_interval and cursor >= total_in_interval:
                    break
                if len(results) >= max_results:
                    break
                await asyncio.sleep(0.3)

    console.print(f"medRxiv: scanned {total_scanned} preprints, matched {len(results)}")
    return results[:max_results]


async def run(max_papers: int = 5000, topic: str | None = None, mesh_terms: str | None = None) -> None:
    checkpoint = Checkpoint("phase1_ingest")

    from utils.run_context import save_run_context

    save_run_context(topic, mesh_terms)

    if checkpoint.is_complete():
        console.print("[green]Phase 1 already complete. Skipping.[/]")
        return

    console.print("[bold cyan]Phase 1: Ingesting papers (PMC + medRxiv)[/]")

    topic_clean = (topic or "long covid").strip()
    synonyms: list[str] = []
    if topic_clean.lower() != "long covid":
        console.print("[cyan]Expanding search terms via Haiku...[/]")
        synonyms = expand_search_terms(topic_clean)
        console.print(f"[dim]Expanded terms: {synonyms}[/]")

    query = build_query(topic=topic, mesh_terms=mesh_terms, synonyms=synonyms)
    console.print(f"[dim]PubMed query: {query}[/dim]")
    ids = fetch_pmc_ids(query, retmax=max_papers)
    console.print(f"PMC: {len(ids)} candidate IDs")

    out_path = app_data("data/raw/papers.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    already_fetched: set[str] = set()
    if out_path.exists():
        with out_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    p = json.loads(line)
                    if pid := p.get("pmc_id"):
                        already_fetched.add(str(pid))
                except json.JSONDecodeError:
                    pass
    remaining = [pid for pid in ids if pid not in already_fetched]
    console.print(f"Already fetched: {len(already_fetched)}, remaining: {len(remaining)}")

    sem = asyncio.Semaphore(3)

    async def fetch_throttled(pid: str, client: httpx.AsyncClient) -> dict | None:
        async with sem:
            result = await fetch_pmc_metadata(pid, client)
            await asyncio.sleep(0.4)
            return result

    async with httpx.AsyncClient(http2=True, limits=httpx.Limits(max_connections=3)) as client:
        with Progress() as progress:
            task = progress.add_task("PMC metadata", total=len(remaining))
            with out_path.open("a", encoding="utf-8") as f:
                for i in range(0, len(remaining), 50):
                    batch = remaining[i : i + 50]
                    results = await asyncio.gather(
                        *(fetch_throttled(pid, client) for pid in batch),
                        return_exceptions=True,
                    )
                    ok = 0
                    for r in results:
                        if isinstance(r, dict) and r.get("abstract"):
                            f.write(json.dumps(r) + "\n")
                            ok += 1
                    progress.update(task, advance=len(batch), description=f"PMC (ok: {ok}/{len(batch)})")

    # Existing DOIs (PMC wins dedup ties — we already hold its full text).
    pmc_dois: set[str] = set()
    n_pmc = 0
    with out_path.open(encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
                n_pmc += 1
                if rec.get("doi"):
                    pmc_dois.add(normalize_doi(rec["doi"]))
            except json.JSONDecodeError:
                pass

    sources_breakdown = {"pmc": n_pmc, "openalex": 0, "medrxiv": 0}

    # P4.1: OpenAlex discovery (incl. server-side preprint search that replaces
    # the slow client-side medRxiv scan unless MEDRXIV_LEGACY is forced).
    if settings.OPENALEX_ENABLED:
        from pipeline.sources.openalex import dedup_against, search_openalex

        oa_query = topic_clean if topic_clean.lower() != "long covid" else "long covid OR PASC"
        console.print("[cyan]Querying OpenAlex (works + preprints, server-side)...[/]")
        oa_papers = await asyncio.to_thread(search_openalex, oa_query, max_results=min(2000, max_papers))
        oa_unique = dedup_against(oa_papers, pmc_dois)
        with out_path.open("a", encoding="utf-8") as f:
            for p in oa_unique:
                if p.get("abstract"):
                    f.write(json.dumps(p) + "\n")
                    pmc_dois.add(normalize_doi(p.get("doi")))
                    if p["source"] == "medrxiv":
                        sources_breakdown["medrxiv"] += 1
                    else:
                        sources_breakdown["openalex"] += 1
        console.print(
            f"OpenAlex: {len(oa_papers)} candidates, {len(oa_unique)} unique after dedupe "
            f"({sources_breakdown['openalex']} journal, {sources_breakdown['medrxiv']} preprint)"
        )

    # Legacy client-side medRxiv scan — only when explicitly requested.
    if settings.MEDRXIV_LEGACY:
        console.print("[cyan]Fetching medRxiv preprints (legacy client-side scan)...[/]")
        topic_low = (topic or "long covid").strip().lower()
        if "covid" in topic_low or "pasc" in topic_low:
            medrxiv_terms = ["long covid", "post-acute sequelae", "PASC", "post-COVID condition"]
        elif synonyms:
            medrxiv_terms = [s.lower() for s in synonyms]
        else:
            medrxiv_terms = [topic_low]
            if " " in topic_low:
                medrxiv_terms.append(topic_low.replace(" ", "-"))
        medrxiv_papers = await fetch_medrxiv_papers(
            query_terms=medrxiv_terms, max_results=min(2000, max_papers // 2)
        )
        new_medrxiv = [p for p in medrxiv_papers if normalize_doi(p.get("doi")) not in pmc_dois]
        with out_path.open("a", encoding="utf-8") as f:
            for p in new_medrxiv:
                if p.get("abstract"):
                    f.write(json.dumps(p) + "\n")
                    sources_breakdown["medrxiv"] += 1
        console.print(f"medRxiv legacy: {len(medrxiv_papers)} candidates, {len(new_medrxiv)} unique")

    # Persist the per-source breakdown for the report / run manifest (P6).
    app_data("data/raw/sources_breakdown.json").write_text(
        json.dumps(sources_breakdown, indent=2), encoding="utf-8"
    )
    console.print(f"[dim]Sources: {sources_breakdown}[/]")

    checkpoint.mark_complete()
    console.print("[green]Phase 1 complete.[/]")


if __name__ == "__main__":
    asyncio.run(run(max_papers=settings.MAX_PAPERS))
