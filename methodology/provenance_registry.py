"""WP-9 — Provenance & citation integrity.

DEFECTS repaired here:
  1. Reference list was a wall of concatenated titles — bibliography bled into
     the citation field.
  2. Reference numbering differed between documents (report [1] != DD [1]).
  3. Inconsistent PMC IDs for the same paper.
  4. (quote-level) literal quotes that no longer match their source.

FIX:
  * ``canonical_id`` — one identifier per paper (PMCID preferred, DOI fallback,
    never free text).
  * ``CitationRegistry`` — numbers assigned once per run and SHARED across all
    documents (export/import the order so report [1] == due-diligence [1]).
  * ``detect_bibliography_bleed`` — a citation record longer than a ceiling or
    carrying >1 identifier is rejected as bleed.
  * ``strip_references_section`` — remove the references *content*, not just the
    heading, before extraction.
  * ``validate_pmcid`` / quote verification — format-check IDs and confirm every
    literal quote still substring-matches its source (else ``quote_drift``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Identifier formats
# ---------------------------------------------------------------------------
PMCID_RE = re.compile(r"^PMC\d{5,9}$")
_PMCID_ANYWHERE = re.compile(r"PMC\d{4,9}", re.IGNORECASE)
# A permissive DOI matcher (DOIs start 10. and contain a slash).
_DOI_ANYWHERE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+", re.IGNORECASE)


def validate_pmcid(pmcid: str | None) -> bool:
    """True iff ``pmcid`` matches the NCBI PMCID format ``PMC`` + 5-9 digits."""
    if not pmcid or not isinstance(pmcid, str):
        return False
    return bool(PMCID_RE.match(pmcid.strip()))


def canonical_id(paper: dict) -> str | None:
    """Resolve a paper's canonical id: prefer a valid PMCID, then DOI.

    Never returns free text. Returns ``None`` if neither a PMCID nor a DOI can
    be found (caller flags ``provenance_error``).
    """
    # explicit pmc_id field
    pmc = paper.get("pmc_id")
    if pmc and not str(pmc).upper().startswith("PMC"):
        pmc = f"PMC{pmc}"
    for cand in (pmc, paper.get("id")):
        if cand and validate_pmcid(str(cand).strip()):
            return str(cand).strip()
    # DOI fallback
    doi = paper.get("doi") or (paper.get("id") if str(paper.get("id", "")).startswith("10.") else None)
    if doi and _DOI_ANYWHERE.match(str(doi).strip()):
        return str(doi).strip()
    return None


# ---------------------------------------------------------------------------
# Bibliography-bleed detection (§9.3)
# ---------------------------------------------------------------------------
def count_identifiers(text: str) -> int:
    """Count distinct DOI / PMCID tokens in a string."""
    ids = set(m.group(0).upper() for m in _PMCID_ANYWHERE.finditer(text))
    ids |= set(m.group(0).lower() for m in _DOI_ANYWHERE.finditer(text))
    return len(ids)


def detect_bibliography_bleed(record: str, *, max_len: int = 600) -> bool:
    """A citation record must describe exactly one paper. Reject as bleed if it
    is longer than ``max_len`` chars or carries more than one identifier."""
    if not record:
        return False
    if len(record) > max_len:
        return True
    return count_identifiers(record) > 1


def assert_no_bleed(record: str, *, max_len: int = 600) -> None:
    if detect_bibliography_bleed(record, max_len=max_len):
        raise BibliographyBleedError(
            f"citation record rejected as bibliography bleed (len={len(record)}, "
            f"identifiers={count_identifiers(record)})"
        )


class BibliographyBleedError(ValueError):
    """Raised when a citation record contains more than one paper's data."""


# ---------------------------------------------------------------------------
# References-section stripping (§9.3)
# ---------------------------------------------------------------------------
_REF_HEADING = re.compile(
    r"^\s*(?:#+\s*)?(?:\d+\.?\s*)?(references|bibliography|works\s+cited|literature\s+cited|reference\s+list)\s*:?\s*$",
    re.IGNORECASE,
)
# A citation-shaped line: "[12] ..." or "12. Surname AB, ..." or "Surname AB, et al."
_CITATION_LINE = re.compile(
    r"^\s*(?:\[\d+\]|\d+\.)\s+\S+|^\s*[A-Z][a-z]+\s+[A-Z]{1,3}(?:,|\s)",
)


def strip_references_section(text: str) -> str:
    """Remove the references section *content* (not just its heading).

    Strategy: truncate at the first standalone references heading. As a
    fallback for heading-less bibliographies, drop a trailing run of
    citation-shaped lines.
    """
    if not text:
        return text
    lines = text.splitlines()
    cut = None
    for i, line in enumerate(lines):
        if _REF_HEADING.match(line):
            cut = i
            break
    if cut is not None:
        return "\n".join(lines[:cut]).rstrip() + "\n"

    # Heading-less fallback: find the longest trailing run of citation-shaped
    # lines and drop it if it is substantial (>= 5 lines).
    run_start = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == "":
            continue
        if _CITATION_LINE.match(lines[i]):
            run_start = i
        else:
            break
    if len(lines) - run_start >= 5:
        return "\n".join(lines[:run_start]).rstrip() + "\n"
    return text


# ---------------------------------------------------------------------------
# Quote-level provenance verification (§9.5)
# ---------------------------------------------------------------------------
def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def verify_quote(quote: str, fulltext: str) -> bool:
    """True iff ``quote`` substring-matches ``fulltext`` after whitespace
    normalisation. A miss is a ``quote_drift`` and the quote must be pulled."""
    if not quote:
        return False
    return normalize_ws(quote).lower() in normalize_ws(fulltext).lower()


def filter_quotes(provenance: list[dict], fulltext: str) -> tuple[list[dict], list[dict]]:
    """Split provenance entries into (verified, drifted).

    A drifted entry is tagged ``quote_drift`` and excluded from the report.
    """
    verified: list[dict] = []
    drifted: list[dict] = []
    for entry in provenance or []:
        q = entry.get("quote", "")
        if verify_quote(q, fulltext):
            verified.append(entry)
        else:
            drifted.append({**entry, "flag": "quote_drift"})
    return verified, drifted


# ---------------------------------------------------------------------------
# Shared citation registry (§9.1, §9.2)
# ---------------------------------------------------------------------------
@dataclass
class CitationRegistry:
    """Assigns stable citation numbers keyed by canonical_id and shares the
    numbering across every document of a run.

    To share numbering across documents/processes, ``export_order()`` the
    ordered canonical ids from the first document and pass them as
    ``preassigned`` when constructing the registry for the next document.
    """

    papers_by_id: dict[str, dict] = field(default_factory=dict)
    _order: list[str] = field(default_factory=list)
    _num: dict[str, int] = field(default_factory=dict)
    provenance_errors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Build a PMC-suffix index so fabricated DOI wrappers can be recovered.
        self._pmc_index: dict[str, str] = {}
        for pid in self.papers_by_id:
            m = _PMCID_ANYWHERE.search(pid)
            if m:
                self._pmc_index[m.group(0).upper()] = pid

    @classmethod
    def with_order(cls, papers_by_id: dict[str, dict], preassigned: list[str]) -> "CitationRegistry":
        reg = cls(papers_by_id=papers_by_id)
        for cid in preassigned:
            reg._register(cid)
        return reg

    def _resolve_token(self, token: str) -> str | None:
        """Map a (possibly fabricated) citation token to a canonical id."""
        t = (token or "").strip()
        if not t:
            return None
        if t in self.papers_by_id:
            return canonical_id(self.papers_by_id[t]) or t
        m = _PMCID_ANYWHERE.search(t)
        if m and m.group(0).upper() in self._pmc_index:
            return self._pmc_index[m.group(0).upper()]
        if validate_pmcid(t):
            return t
        if _DOI_ANYWHERE.match(t):
            return t
        # Unresolvable — record a provenance error, do not fabricate.
        self.provenance_errors.append(t)
        return None

    def _register(self, canonical: str) -> int:
        if canonical not in self._num:
            self._num[canonical] = len(self._order) + 1
            self._order.append(canonical)
        return self._num[canonical]

    def number_for(self, token: str) -> int | None:
        """Resolve a token and return its (stable, shared) citation number."""
        canonical = self._resolve_token(token)
        if canonical is None:
            return None
        return self._register(canonical)

    def export_order(self) -> list[str]:
        """The ordered canonical ids — feed into the next document's registry
        so numbering is identical across all documents of the run."""
        return list(self._order)

    def ordered(self) -> list[str]:
        return list(self._order)
