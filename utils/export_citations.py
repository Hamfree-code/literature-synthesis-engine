"""Bibliography exports for reference managers (UPGRADE v3.1 — P6.3).

Generates RIS (EndNote) and BibTeX (Zotero) from the in-corpus paper metadata so
client teams import the references directly.
"""

from __future__ import annotations

import re


def _bibkey(paper: dict, n: int) -> str:
    first = (paper.get("authors") or ["anon"])[0].split()[-1].lower()
    first = re.sub(r"[^a-z0-9]", "", first) or "anon"
    return f"{first}{paper.get('year') or 'nd'}{n}"


def _ris_type(paper: dict) -> str:
    src = (paper.get("source") or "").lower()
    return "UNPB" if "medrxiv" in src or "preprint" in (paper.get("journal") or "").lower() else "JOUR"


def to_ris(papers: list[dict]) -> str:
    """RIS format (EndNote/Mendeley/Zotero)."""
    blocks: list[str] = []
    for n, p in enumerate(papers, 1):
        lines = [f"TY  - {_ris_type(p)}"]
        for author in p.get("authors") or []:
            lines.append(f"AU  - {author}")
        if p.get("title"):
            lines.append(f"TI  - {p['title']}")
        if p.get("journal"):
            lines.append(f"JO  - {p['journal']}")
        if p.get("year"):
            lines.append(f"PY  - {p['year']}")
        doi = p.get("doi")
        if doi:
            lines.append(f"DO  - {doi}")
        if p.get("url"):
            lines.append(f"UR  - {p['url']}")
        lines.append("ER  - ")
        blocks.append("\n".join(lines))
    return "\n".join(blocks) + "\n"


def _bib_escape(text: str) -> str:
    return (text or "").replace("{", "").replace("}", "").replace("\\", "")


def to_bibtex(papers: list[dict]) -> str:
    """BibTeX format (Zotero/BibDesk/LaTeX)."""
    entries: list[str] = []
    for n, p in enumerate(papers, 1):
        is_preprint = _ris_type(p) == "UNPB"
        kind = "misc" if is_preprint else "article"
        key = _bibkey(p, n)
        fields = [f"  title = {{{_bib_escape(p.get('title') or 'Untitled')}}}"]
        authors = " and ".join(p.get("authors") or ["Anonymous"])
        fields.append(f"  author = {{{_bib_escape(authors)}}}")
        if p.get("year"):
            fields.append(f"  year = {{{p['year']}}}")
        if p.get("journal"):
            label = "howpublished" if is_preprint else "journal"
            fields.append(f"  {label} = {{{_bib_escape(p['journal'])}}}")
        if p.get("doi"):
            fields.append(f"  doi = {{{p['doi']}}}")
        if p.get("url"):
            fields.append(f"  url = {{{p['url']}}}")
        entries.append(f"@{kind}{{{key},\n" + ",\n".join(fields) + "\n}")
    return "\n\n".join(entries) + "\n"
