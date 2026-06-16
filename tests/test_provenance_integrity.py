"""WP-9 — provenance integrity.

Acceptance (spec §9):
  * Each reference renders as exactly one bibliographic record.
  * Report and due-diligence share identical numbering for identical papers.
  * The Medicare cohort resolves to a single, validated PMCID across documents.
  * Injecting a citation record with two PMCIDs triggers bibliography_bleed.
  * A quote altered by one word fails the substring check and is removed with a
    quote_drift flag.
"""
from __future__ import annotations

import pytest

from methodology import provenance_registry as pr


# ---- PMCID validation ------------------------------------------------------

@pytest.mark.parametrize("good", ["PMC12202091", "PMC123456", "PMC99999"])
def test_valid_pmcids(good):
    assert pr.validate_pmcid(good)


@pytest.mark.parametrize("bad", ["PMC", "PMCabc", "12202091", "PMC123", "PMC1320209X", None, ""])
def test_invalid_pmcids(bad):
    assert not pr.validate_pmcid(bad)


def test_canonical_id_prefers_pmcid():
    paper = {"id": "PMC12202091", "doi": "10.1001/jama.2024.1", "pmc_id": "12202091"}
    assert pr.canonical_id(paper) == "PMC12202091"


def test_canonical_id_falls_back_to_doi():
    paper = {"id": "medrxiv_10.1101_2024.01", "doi": "10.1101/2024.01.01.24300"}
    assert pr.canonical_id(paper) == "10.1101/2024.01.01.24300"


def test_canonical_id_none_when_no_identifier():
    assert pr.canonical_id({"id": "conceptual prior", "title": "x"}) is None


# ---- bibliography bleed (§9.3) --------------------------------------------

def test_bibliography_bleed_two_pmcids():
    record = "1. Some Author. A title. PMC12202091. Another bled-in entry PMC13202091."
    assert pr.detect_bibliography_bleed(record)
    with pytest.raises(pr.BibliographyBleedError):
        pr.assert_no_bleed(record)


def test_bibliography_bleed_concatenated_titles():
    record = "1. " + " ".join(f"Title number {i} about Long COVID symptoms." for i in range(70))
    assert pr.detect_bibliography_bleed(record)


def test_clean_record_is_not_bleed():
    record = "1. Smith J, Doe A. Long COVID cohort. *BMJ*. 2024. PMC12202091"
    assert not pr.detect_bibliography_bleed(record)
    pr.assert_no_bleed(record)  # must not raise


# ---- references stripping (§9.3) ------------------------------------------

def test_strip_references_heading():
    text = (
        "Intro body text.\n\nDiscussion: the effect was large.\n\n"
        "References\n"
        "1. Smith J. Title one. BMJ. 2024.\n"
        "2. Doe A. Title two. Lancet. 2023.\n"
    )
    stripped = pr.strip_references_section(text)
    assert "Discussion: the effect was large." in stripped
    assert "Smith J. Title one" not in stripped
    assert "References" not in stripped


def test_strip_references_no_section_left_intact():
    text = "Methods and results only. No bibliography here.\nConclusion stands.\n"
    assert pr.strip_references_section(text).strip() == text.strip()


# ---- quote drift (§9.5) ----------------------------------------------------

def test_verify_quote_whitespace_insensitive():
    full = "The   cohort showed\na 47% prevalence of fatigue."
    assert pr.verify_quote("The cohort showed a 47% prevalence of fatigue.", full)


def test_quote_altered_by_one_word_is_drift():
    full = "The cohort showed a 47% prevalence of fatigue."
    provenance = [
        {"field": "x", "quote": "The cohort showed a 47% prevalence of fatigue."},
        {"field": "y", "quote": "The cohort showed a 74% prevalence of fatigue."},  # altered
    ]
    verified, drifted = pr.filter_quotes(provenance, full)
    assert len(verified) == 1
    assert len(drifted) == 1
    assert drifted[0]["flag"] == "quote_drift"


# ---- shared citation numbering (§9.2) -------------------------------------

def test_shared_numbering_across_documents():
    papers = {
        "PMC111": {"id": "PMC111"},
        "PMC222": {"id": "PMC222"},
        "PMC333": {"id": "PMC333"},
    }
    # Report cites in order 333, 111, 222.
    report = pr.CitationRegistry(papers_by_id=papers)
    assert report.number_for("PMC333") == 1
    assert report.number_for("PMC111") == 2
    assert report.number_for("PMC222") == 3

    # Due-diligence reuses the SAME order → identical numbers, even if it cites
    # them in a different sequence.
    dd = pr.CitationRegistry.with_order(papers, preassigned=report.export_order())
    assert dd.number_for("PMC111") == 2  # report [2] == dd [2]
    assert dd.number_for("PMC333") == 1  # report [1] == dd [1]


def test_medicare_cohort_single_validated_pmcid():
    # Same paper referenced by two documents resolves to ONE canonical id.
    papers = {"PMC12202091": {"id": "PMC12202091", "pmc_id": "12202091"}}
    reg = pr.CitationRegistry(papers_by_id=papers)
    n1 = reg.number_for("PMC12202091")
    # a fabricated DOI-wrapped variant recovers to the same canonical id
    n2 = reg.number_for("10.1101/2025.05.PMC12202091")
    assert n1 == n2 == 1
    assert reg.ordered() == ["PMC12202091"]


def test_unresolvable_token_records_provenance_error_not_fabrication():
    reg = pr.CitationRegistry(papers_by_id={"PMC111": {"id": "PMC111"}})
    assert reg.number_for("conceptual prior") is None
    assert "conceptual prior" in reg.provenance_errors
