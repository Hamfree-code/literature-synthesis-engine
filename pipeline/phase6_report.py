"""Phase 6: Generate Markdown + HTML report with numbered citations."""
from __future__ import annotations
# __APP_PATHS_INSTALLED__
from app_paths import app_data, resource

import json
import re
from datetime import date
from pathlib import Path

import httpx
import markdown as md_lib
from jinja2 import Environment, FileSystemLoader
from rich.console import Console

from utils.checkpointing import Checkpoint

console = Console()


def short_authors(authors: list[str], max_n: int = 3) -> str:
    if not authors:
        return "Anonymous"
    if len(authors) == 1:
        return authors[0]
    if len(authors) <= max_n:
        return ", ".join(authors[:-1]) + " & " + authors[-1]
    return f"{authors[0]} et al."


def short_cite(paper: dict) -> str:
    """Inline citation like 'Smith et al. 2024'."""
    last_name = (paper.get("authors") or ["Anon"])[0].split()[-1]
    year = paper.get("year") or "n.d."
    if len(paper.get("authors") or []) > 1:
        return f"{last_name} et al. {year}"
    return f"{last_name} {year}"


def vancouver_ref(paper: dict, n: int) -> str:
    """Vancouver-style bibliography entry."""
    authors = ", ".join(paper.get("authors") or ["Anon"][:6])
    if len(paper.get("authors") or []) > 6:
        authors += ", et al."
    title = paper.get("title", "Untitled")
    journal = paper.get("journal") or "Preprint"
    year = paper.get("year") or "n.d."
    url = paper.get("url") or ""
    doi = paper.get("id")
    doi_link = f"https://doi.org/{doi}" if not doi.startswith("PMC") else url
    return f"{n}. {authors}. {title} *{journal}*. {year}. [{doi}]({doi_link})"


_crossref_cache: dict[str, dict | None] = {}


def resolve_crossref(doi: str) -> dict | None:
    """Query CrossRef API for citation metadata. Cached per session."""
    if doi in _crossref_cache:
        return _crossref_cache[doi]
    url = f"https://api.crossref.org/works/{doi}"
    headers = {"User-Agent": "LongCOVIDPipeline/2.0 (mailto:hhamri53@gmail.com)"}
    try:
        with httpx.Client(timeout=10) as client:
            r = client.get(url, headers=headers)
            if r.status_code != 200:
                _crossref_cache[doi] = None
                return None
            meta = r.json().get("message")
            _crossref_cache[doi] = meta
            return meta
    except Exception:
        _crossref_cache[doi] = None
        return None


def crossref_vancouver(meta: dict, n: int) -> str:
    """Format CrossRef metadata as a Vancouver citation string."""
    authors = meta.get("author", [])
    parts = []
    for a in authors[:6]:
        family = a.get("family", "")
        given = a.get("given", "")
        initial = given[0] if given else ""
        parts.append(f"{family} {initial}".strip())
    author_str = ", ".join(parts) if parts else "Unknown"
    if len(authors) > 6:
        author_str += ", et al."

    dp = (meta.get("published") or meta.get("published-online") or meta.get("created") or {})
    year = (dp.get("date-parts") or [[None]])[0][0] or "n.d."
    title = (meta.get("title") or ["Untitled"])[0]
    journal = (meta.get("container-title") or [""])[0] or "Preprint"
    volume = meta.get("volume", "")
    issue = meta.get("issue", "")
    pages = meta.get("page", "")
    doi = meta.get("DOI", "")

    vol_info = ""
    if volume:
        vol_info = f"{volume}"
        if issue:
            vol_info += f"({issue})"
        if pages:
            vol_info += f":{pages}"
        vol_info += ". "

    return f"{n}. {author_str}. {title}. *{journal}*. {year}. {vol_info}doi:{doi}"


class CitationManager:
    """Replaces (CITE:DOI) markers with [N] and builds bibliography in order of first citation.

    Defensive: tries to recover from LLM-fabricated DOIs (e.g. `10.1101/2025.05.PMCxxxxxx`)
    by extracting the PMC suffix and matching against papers_by_id.
    """

    _PMC_RE = re.compile(r"(PMC\d{5,})", re.IGNORECASE)
    _NON_PAPER = re.compile(r"^(conceptual|not a (single |)paper|placeholder|unspecified)", re.IGNORECASE)

    def __init__(self, papers_by_id: dict[str, dict]):
        self.papers_by_id = papers_by_id
        self.doi_to_num: dict[str, int] = {}
        self.ordered_dois: list[str] = []
        # build a PMC-id index for fallback matching
        self._pmc_index: dict[str, str] = {}
        for pid in papers_by_id:
            m = self._PMC_RE.search(pid)
            if m:
                self._pmc_index[m.group(1).upper()] = pid

    def _normalize(self, doi: str) -> str | None:
        """Try to map a (possibly fabricated) citation token to a real paper_id.
        Returns None if the token is obviously a non-paper placeholder ('conceptual prior' etc).
        """
        d = doi.strip()
        if not d:
            return None
        # Obvious placeholders: skip
        if self._NON_PAPER.search(d):
            return None
        # If it's already a known paper_id, return as-is.
        if d in self.papers_by_id:
            return d
        # Extract PMCxxxxx if present anywhere in the token
        m = self._PMC_RE.search(d)
        if m and m.group(1).upper() in self._pmc_index:
            return self._pmc_index[m.group(1).upper()]
        # Strip a fake DOI prefix like "10.xxxx/" and retry
        if "/" in d:
            tail = d.split("/", 1)[1]
            if tail in self.papers_by_id:
                return tail
            m2 = self._PMC_RE.search(tail)
            if m2 and m2.group(1).upper() in self._pmc_index:
                return self._pmc_index[m2.group(1).upper()]
        # Last resort: register as-is and let bibliography mark as Unresolved.
        return d

    def _resolve(self, doi: str) -> int | None:
        """Returns the citation number for a (possibly fabricated) DOI token, or None to skip."""
        norm = self._normalize(doi)
        if norm is None:
            return None
        if norm not in self.doi_to_num:
            n = len(self.doi_to_num) + 1
            self.doi_to_num[norm] = n
            self.ordered_dois.append(norm)
        return self.doi_to_num[norm]

    def substitute(self, text: str) -> str:
        """Replace (CITE:DOI) or (CITE:DOI1; CITE:DOI2) with [1] / [1,2].
        Tokens that resolve to non-paper placeholders are dropped silently."""
        pattern = re.compile(r"\(CITE:([^)]+)\)")

        def repl(m: re.Match) -> str:
            raw = m.group(1)
            dois = [p.strip().replace("CITE:", "") for p in raw.split(";")]
            nums = [str(n) for n in (self._resolve(d) for d in dois) if n is not None]
            if not nums:
                return ""  # drop the marker entirely if everything was a placeholder
            return f"[{','.join(nums)}]"

        return pattern.sub(repl, text)

    def cite_doi(self, doi: str) -> str:
        """Direct DOI -> '[N]' helper used in templates."""
        n = self._resolve(doi)
        return f"[{n}]" if n is not None else ""

    def resolve_unresolved_via_crossref(self) -> None:
        """For DOIs not in papers_by_id, try CrossRef API."""
        unresolved = [d for d in self.ordered_dois
                      if d not in self.papers_by_id and "/" in d and d.startswith("10.")]
        if not unresolved:
            return
        console.print(f"[dim]Resolving {len(unresolved)} unresolved DOIs via CrossRef...[/]")
        resolved = 0
        for doi in unresolved:
            meta = resolve_crossref(doi)
            if meta:
                resolved += 1
        console.print(f"[dim]CrossRef: resolved {resolved}/{len(unresolved)}[/]")

    def bibliography_markdown(self) -> str:
        lines = []
        for n, doi in enumerate(self.ordered_dois, start=1):
            paper = self.papers_by_id.get(doi)
            if paper:
                lines.append(vancouver_ref(paper, n))
            elif "/" in doi and doi.startswith("10.") and _crossref_cache.get(doi):
                lines.append(crossref_vancouver(_crossref_cache[doi], n))
            else:
                lines.append(f"{n}. *Unresolved reference:* {doi}")
        return "\n\n".join(lines)


def llm_badge(_value=None) -> str:
    """Master Improvement Spec v3.0 — Priority 3.1: badge marker for fields that
    are LLM inferences. Returns a compact ' [LLM]' string for inline use in
    templates: e.g. `{{ "Established" }}{{ value | llm }}`."""
    return " <sup>[LLM]</sup>"


def calc_badge(_value=None) -> str:
    """Deterministic / computed badge."""
    return " <sup>[CALC]</sup>"


def consensus_badge(_value=None) -> str:
    """Arbiter-reconciled badge."""
    return " <sup>[CONSENSUS]</sup>"


def _make_env(citer: "CitationManager") -> Environment:
    env = Environment(loader=FileSystemLoader(str(resource("templates"))), trim_blocks=True, lstrip_blocks=True)
    env.filters["cite"] = citer.substitute
    env.filters["cite_doi"] = citer.cite_doi
    env.filters["llm"] = llm_badge
    env.filters["calc"] = calc_badge
    env.filters["consensus"] = consensus_badge
    return env


def build_markdown(analysis: dict, papers_by_id: dict[str, dict]) -> tuple[str, dict]:
    synthesis = analysis.get("synthesis") or {}
    aggregates = analysis.get("aggregates") or {}
    n_papers = aggregates.get("n_papers") or analysis.get("meta", {}).get("n_papers", 0)
    n_deep = sum(1 for _ in (app_data("data/filtered/deep_results.jsonl").open(encoding="utf-8") if app_data("data/filtered/deep_results.jsonl").exists() else []))

    citer = CitationManager(papers_by_id)
    env = _make_env(citer)

    from utils.run_context import topic_title
    template = env.get_template("report.md.j2")
    md_body = template.render(
        date=date.today().isoformat(),
        topic_title=topic_title(),
        n_papers=n_papers,
        n_deep=n_deep,
        synthesis=synthesis,
        aggregates=aggregates,
        papers_by_id=papers_by_id,
        short_cite=short_cite,
    )

    citer.resolve_unresolved_via_crossref()
    md_body += "\n\n## References\n\n" + citer.bibliography_markdown() + "\n"

    return md_body, {
        "n_citations": len(citer.ordered_dois),
        "n_papers": n_papers,
        "n_deep": n_deep,
    }


def build_due_diligence_markdown(analysis: dict, papers_by_id: dict[str, dict]) -> tuple[str, dict]:
    dd = analysis.get("due_diligence") or {}
    aggregates = analysis.get("aggregates") or {}
    n_papers = aggregates.get("n_papers") or analysis.get("meta", {}).get("n_papers", 0)
    n_deep = sum(1 for _ in (app_data("data/filtered/deep_results.jsonl").open(encoding="utf-8") if app_data("data/filtered/deep_results.jsonl").exists() else []))

    citer = CitationManager(papers_by_id)
    env = _make_env(citer)

    from utils.run_context import topic_title
    template = env.get_template("due_diligence.md.j2")
    md_body = template.render(
        date=date.today().isoformat(),
        topic_title=topic_title(),
        n_papers=n_papers,
        n_deep=n_deep,
        dd=dd,
        papers_by_id=papers_by_id,
    )

    citer.resolve_unresolved_via_crossref()
    md_body += "\n\n## References\n\n" + citer.bibliography_markdown() + "\n"

    return md_body, {
        "n_citations": len(citer.ordered_dois),
        "n_papers": n_papers,
        "n_deep": n_deep,
    }


def build_executive_summary_markdown(analysis: dict, papers_by_id: dict[str, dict]) -> tuple[str, dict]:
    """Non-technical 2-page brief for non-scientist readers. No citations in body."""
    exec_data = analysis.get("executive_summary") or {}
    aggregates = analysis.get("aggregates") or {}
    n_papers = aggregates.get("n_papers") or analysis.get("meta", {}).get("n_papers", 0)
    n_deep = sum(1 for _ in (app_data("data/filtered/deep_results.jsonl").open(encoding="utf-8") if app_data("data/filtered/deep_results.jsonl").exists() else []))

    # No citation manager used — the exec summary deliberately omits citations.
    citer = CitationManager(papers_by_id)  # still passed to keep env consistent
    env = _make_env(citer)

    from utils.run_context import topic_title
    template = env.get_template("executive_summary.md.j2")
    md_body = template.render(
        date=date.today().isoformat(),
        topic_title=topic_title(),
        n_papers=n_papers,
        n_deep=n_deep,
        exec=exec_data,
    )

    return md_body, {
        "n_citations": 0,
        "n_papers": n_papers,
        "n_deep": n_deep,
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Long COVID Research Analysis</title>
<style>
:root {
  --fg: #1a1a1a;
  --muted: #555;
  --accent: #1e40af;
  --border: #d4d4d4;
  --bg-soft: #f7f7f5;
}
* { box-sizing: border-box; }
body {
  font-family: Georgia, "Source Serif Pro", serif;
  max-width: 780px;
  margin: 0 auto;
  padding: 3rem 2rem;
  color: var(--fg);
  line-height: 1.7;
  font-size: 17px;
  background: #fcfcfa;
}
h1 { font-size: 2.2rem; line-height: 1.2; margin-bottom: 0.3rem; }
h2 { font-size: 1.5rem; margin-top: 2.5rem; padding-bottom: 0.3rem; border-bottom: 1px solid var(--border); }
h3 { font-size: 1.2rem; margin-top: 1.8rem; color: var(--accent); }
h4 { font-size: 1.05rem; margin-top: 1.3rem; font-style: italic; }
p { margin: 0.7rem 0; text-align: justify; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
table { border-collapse: collapse; margin: 1rem 0; width: 100%; font-size: 0.92rem; font-family: -apple-system, sans-serif; }
th, td { padding: 0.45rem 0.7rem; text-align: left; border-bottom: 1px solid var(--border); }
th { background: var(--bg-soft); font-weight: 600; }
blockquote { border-left: 3px solid var(--accent); margin: 1rem 0; padding: 0.5rem 1rem; background: var(--bg-soft); font-style: italic; }
.meta { color: var(--muted); font-size: 0.9rem; margin-bottom: 2rem; }
.bib { font-size: 0.9rem; }
.bib p { margin: 0.4rem 0; padding-left: 1.5rem; text-indent: -1.5rem; }
sup, .cite-num { color: var(--accent); font-size: 0.85em; }
hr { border: none; border-top: 1px solid var(--border); margin: 2rem 0; }
code { background: var(--bg-soft); padding: 0.1rem 0.3rem; border-radius: 3px; font-size: 0.9em; }
</style>
</head>
<body>
{{ content }}
</body>
</html>
"""


def render_html(md_body: str) -> str:
    html_body = md_lib.markdown(md_body, extensions=["tables", "fenced_code", "toc"])
    # Convert [1], [1,2] in body to superscript styled spans
    html_body = re.sub(r"\[(\d+(?:,\d+)*)\]", r'<sup class="cite-num">[\1]</sup>', html_body)
    # Wrap references list (after "References" heading) in a .bib class — simple heuristic
    html_body = html_body.replace("<h2>References</h2>", '<h2>References</h2><div class="bib">')
    if '<div class="bib">' in html_body:
        html_body += "</div>"
    return HTML_TEMPLATE.replace("{{ content }}", html_body)


def run() -> None:
    checkpoint = Checkpoint("phase6_report")
    if checkpoint.is_complete():
        console.print("[green]Phase 6 already complete. Skipping.[/]")
        return

    console.print("[bold cyan]Phase 6: Generating report[/]")

    analysis_path = app_data("data/filtered/analysis.json")
    if not analysis_path.exists():
        console.print("[red]No analysis.json — run Phase 5 first.[/]")
        return

    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))

    papers_path = app_data("data/raw/papers.jsonl")
    papers_by_id: dict[str, dict] = {}
    if papers_path.exists():
        for line in papers_path.open(encoding="utf-8"):
            p = json.loads(line)
            papers_by_id[p["id"]] = p

    today = date.today().isoformat()

    def write_one(prefix: str, md_body: str, stats: dict) -> None:
        md_out = app_data(f"reports/{prefix}_{today}.md")
        md_out.parent.mkdir(parents=True, exist_ok=True)
        md_out.write_text(md_body, encoding="utf-8")
        console.print(f"Markdown: {md_out}")
        html = render_html(md_body)
        html_out = md_out.with_suffix(".html")
        html_out.write_text(html, encoding="utf-8")
        console.print(f"HTML: {html_out}")
        try:
            from app_pdf import markdown_to_pdf
            pdf_out = md_out.with_suffix(".pdf")
            markdown_to_pdf(md_body, pdf_out, title=prefix.replace("_", " ").title())
            console.print(f"PDF: {pdf_out}")
            try:
                from app_paths import USER_DESKTOP
                desktop_copy = USER_DESKTOP / pdf_out.name
                desktop_copy.write_bytes(pdf_out.read_bytes())
                console.print(f"PDF copied to desktop: {desktop_copy}")
            except Exception as e:
                console.print(f"[yellow]Could not copy PDF to desktop: {e}[/]")
        except Exception as e:
            console.print(f"[yellow]PDF generation failed: {e}[/]")
        console.print(f"  -> {stats['n_papers']} papers, {stats['n_deep']} deep, {stats['n_citations']} citations")

    from utils.run_context import topic_slug
    slug = topic_slug()

    md_body, stats = build_markdown(analysis, papers_by_id)
    write_one(f"research_{slug}", md_body, stats)

    if analysis.get("due_diligence"):
        dd_md, dd_stats = build_due_diligence_markdown(analysis, papers_by_id)
        write_one(f"research_{slug}_due_diligence", dd_md, dd_stats)

    # Executive summary — always emit, even if Sonnet failed. The template degrades gracefully.
    exec_md, exec_stats = build_executive_summary_markdown(analysis, papers_by_id)
    write_one(f"research_{slug}_executive_summary", exec_md, exec_stats)

    checkpoint.mark_complete()
    console.print("[green]Phase 6 complete.[/]")


if __name__ == "__main__":
    run()