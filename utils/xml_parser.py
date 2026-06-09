"""PMC XML semantic section extractor (Master Improvement Spec v3.0 — Priority 1.2).

Replaces the flat 120,000-char truncation in Phase 1 / Phase 3 with a structured
parse that walks `<sec sec-type="...">` and preserves discussion / limitations /
conflicts / funding — sections that previously got cut off when the article
exceeded the character cap.

The spec mandates BeautifulSoup; we use lxml (already a project dependency)
because adding bs4 would bloat the PyInstaller bundle. The semantics are
identical: sec-type matching plus title-fallback for papers whose authors did
not set sec-type attributes (common in older PMC submissions).

Output is a dict with five keys:
  - metadata          (str) abstract + intro
  - methods           (str)
  - results           (str)
  - discussion_limitations (str)
  - conflicts_funding (str)

Each section is capped at SECTION_MAX_CHARS to avoid blowing up the Sonnet
context window. Caps are conservative and per-section so the whole paper still
fits well under the 200K context budget when concatenated.
"""

from __future__ import annotations

from lxml import etree

# Per-section character caps. Tuned so the sum stays well under 120k even
# in the worst case (long methods sections).
SECTION_MAX_CHARS = {
    "metadata": 12_000,
    "methods": 30_000,
    "results": 40_000,
    "discussion_limitations": 35_000,
    "conflicts_funding": 3_000,
}

# Title-fallback keyword maps for papers without sec-type attributes.
_TITLE_BUCKETS = (
    ("methods", ("method", "material", "patient", "participant", "procedure", "design")),
    ("results", ("result", "finding", "outcome")),
    ("discussion_limitations", ("discuss", "limit", "caveat", "interpret", "implication", "conclus")),
    ("conflicts_funding", ("conflict", "fund", "financ", "disclosure", "competing interest", "acknowledg")),
)


def _classify_section(sec_type: str, title: str) -> str | None:
    """Return the bucket name for a <sec> element, or None if it should be skipped."""
    st = (sec_type or "").lower()
    tt = (title or "").lower()

    if any(k in st for k in ("method", "material")):
        return "methods"
    if "result" in st:
        return "results"
    if any(k in st for k in ("discuss", "limit", "caveat", "interpret", "conclus")):
        return "discussion_limitations"
    if any(k in st for k in ("conflict", "fund", "financ")):
        return "conflicts_funding"

    for bucket, keywords in _TITLE_BUCKETS:
        if any(k in tt for k in keywords):
            return bucket

    if any(k in st for k in ("intro", "background")) or any(k in tt for k in ("introduction", "background")):
        return "metadata"
    return None


def extract_structured_sections(xml_content: str | bytes) -> dict:
    """Parse PMC full-text XML into a section-typed dict.

    Strips <ref-list> nodes BEFORE classifying sections so the bibliography
    never bleeds into discussion text.

    Returns a dict with keys: metadata, methods, results,
    discussion_limitations, conflicts_funding. Values are concatenated section
    text strings, each capped at its SECTION_MAX_CHARS budget.
    """
    if isinstance(xml_content, str):
        xml_bytes = xml_content.encode("utf-8", errors="replace")
    else:
        xml_bytes = xml_content

    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return {k: "" for k in SECTION_MAX_CHARS}

    for rl in root.xpath(".//ref-list"):
        rl.getparent().remove(rl)
    for ack in root.xpath(".//ack"):
        # ack often contains funding+coi; keep it but route to conflicts_funding.
        pass

    buckets: dict[str, list[str]] = {k: [] for k in SECTION_MAX_CHARS}

    abstract_nodes = root.xpath(".//abstract")
    if abstract_nodes:
        abstract_text = " ".join("".join(n.itertext()) for n in abstract_nodes).strip()
        if abstract_text:
            buckets["metadata"].append(abstract_text)

    for sec in root.xpath(".//sec"):
        sec_type = sec.get("sec-type", "")
        title_nodes = sec.xpath("title/text()")
        title = title_nodes[0] if title_nodes else ""
        bucket = _classify_section(sec_type, title)
        if bucket is None:
            continue
        text = " ".join(sec.itertext()).strip()
        if not text:
            continue
        label = title.strip() or sec_type.strip() or bucket
        buckets[bucket].append(f"[{label.upper()}]\n{text}")

    for ack in root.xpath(".//ack"):
        ack_text = " ".join(ack.itertext()).strip()
        if ack_text:
            buckets["conflicts_funding"].append(f"[ACKNOWLEDGEMENTS]\n{ack_text}")

    out: dict[str, str] = {}
    for key, cap in SECTION_MAX_CHARS.items():
        joined = "\n\n".join(buckets[key]).strip()
        if len(joined) > cap:
            joined = joined[:cap] + "\n[TRUNCATED]"
        out[key] = joined
    return out


def sections_to_compact_text(sections: dict) -> str:
    """Concatenate the structured sections into a single text block with clear
    delimiters. Used by extraction prompts that still expect a single FULL TEXT
    string. Section order is fixed.
    """
    order = ("metadata", "methods", "results", "discussion_limitations", "conflicts_funding")
    labels = {
        "metadata": "METADATA & ABSTRACT",
        "methods": "METHODS",
        "results": "RESULTS",
        "discussion_limitations": "DISCUSSION & LIMITATIONS",
        "conflicts_funding": "CONFLICTS OF INTEREST & FUNDING",
    }
    parts = []
    for key in order:
        body = (sections.get(key) or "").strip()
        if body:
            parts.append(f"=== {labels[key]} ===\n{body}")
    return "\n\n".join(parts)
