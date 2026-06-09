"""P0 — XML section bucketing + bibliography-contamination regression."""

from __future__ import annotations

from utils.xml_parser import extract_structured_sections, sections_to_compact_text


def test_each_bucket_receives_its_own_section(pmc_full_xml):
    sec = extract_structured_sections(pmc_full_xml)
    assert "MARKER_METHODS" in sec["methods"]
    assert "MARKER_RESULTS" in sec["results"]
    assert "MARKER_DISCUSSION" in sec["discussion_limitations"]
    assert "MARKER_LIMITATIONS" in sec["discussion_limitations"]
    assert "MARKER_CONFLICTS" in sec["conflicts_funding"]


def test_methods_marker_not_in_results(pmc_full_xml):
    sec = extract_structured_sections(pmc_full_xml)
    assert "MARKER_METHODS" not in sec["results"]
    assert "MARKER_RESULTS" not in sec["methods"]


def test_reflist_never_contaminates_any_bucket(pmc_full_xml):
    sec = extract_structured_sections(pmc_full_xml)
    blob = "\n".join(sec.values())
    assert "MARKER_BIBLIO" not in blob  # ref-list is stripped before classifying


def test_messy_bibliography_is_stripped(pmc_messy_xml):
    sec = extract_structured_sections(pmc_messy_xml)
    blob = "\n".join(sec.values())
    assert "BIBLIO_LEAK" not in blob
    assert "GOOD_METHODS" in sec["methods"]
    assert "GOOD_DISCUSSION" in sec["discussion_limitations"]


def test_compact_text_preserves_order(pmc_full_xml):
    sec = extract_structured_sections(pmc_full_xml)
    compact = sections_to_compact_text(sec)
    assert compact.index("METHODS") < compact.index("RESULTS") < compact.index("DISCUSSION")


def test_malformed_xml_returns_empty_buckets():
    out = extract_structured_sections(b"<not-xml")
    assert set(out.keys()) == {
        "metadata",
        "methods",
        "results",
        "discussion_limitations",
        "conflicts_funding",
    }
    assert all(v == "" for v in out.values())
