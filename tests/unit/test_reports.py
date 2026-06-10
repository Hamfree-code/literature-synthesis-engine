"""P6 — manifest, citations, PRISMA/SoF/QA builders, DOCX export."""

from __future__ import annotations

from pathlib import Path

from utils.export_citations import to_bibtex, to_ris
from utils.report_builders import (
    grade_sof_table,
    one_pager_markdown,
    prisma_flow_svg,
    qa_sheet_markdown,
)
from utils.run_manifest import build_manifest, stable_sha256, verify_manifest

PROMPTS = Path(__file__).resolve().parents[2] / "config" / "prompts"


# ── Manifest ────────────────────────────────────────────────────────────────
def _manifest():
    return build_manifest(
        topic="fibromyalgia",
        mesh_terms=None,
        queries_by_source={"pmc": "fibromyalgia[tiab]"},
        sources_breakdown={"pmc": 100, "openalex": 20, "medrxiv": 5},
        phase_counts={"triaged": 120, "deep": 50},
        cost_usd=22.5,
        runtime_seconds=1800.0,
        prompts_dir=PROMPTS,
    )


def test_manifest_run_id_is_stable_and_verifies():
    m = _manifest()
    assert m["run_id"] == stable_sha256(m)
    assert verify_manifest(m) is True


def test_manifest_run_id_changes_with_content():
    m1 = _manifest()
    m2 = _manifest()
    m2["topic"] = "narcolepsy"
    assert stable_sha256(m1) != stable_sha256(m2)


def test_manifest_includes_prompt_hashes():
    m = _manifest()
    assert isinstance(m["prompt_sha256"], dict)
    if PROMPTS.exists():
        assert len(m["prompt_sha256"]) >= 1


def test_tampering_breaks_verification():
    m = _manifest()
    m["api_cost_usd"] = 0.0  # tamper without recomputing run_id
    assert verify_manifest(m) is False


# ── Citations ───────────────────────────────────────────────────────────────
PAPERS = [
    {
        "authors": ["Jane Roe", "John Doe"],
        "title": "A cohort study",
        "journal": "Nature",
        "year": 2023,
        "doi": "10.1/x",
        "url": "http://x",
        "source": "pmc",
    },
    {
        "authors": ["Sam Lee"],
        "title": "A preprint",
        "journal": "medRxiv (preprint)",
        "year": 2024,
        "doi": "10.1101/y",
        "source": "medrxiv",
    },
]


def test_ris_has_entries_and_types():
    ris = to_ris(PAPERS)
    assert ris.count("TY  - ") == 2
    assert "TY  - JOUR" in ris and "TY  - UNPB" in ris
    assert "DO  - 10.1/x" in ris


def test_bibtex_has_entries():
    bib = to_bibtex(PAPERS)
    assert bib.count("@") == 2
    assert "@article" in bib and "@misc" in bib
    assert "doi = {10.1/x}" in bib


# ── PRISMA / SoF / QA ────────────────────────────────────────────────────────
def test_prisma_svg_contains_counts():
    svg = prisma_flow_svg(
        {
            "identified_pmc": 100,
            "identified_openalex": 20,
            "identified_medrxiv": 5,
            "screened": 120,
            "excluded_screen": 70,
            "eligible": 50,
            "included_deep": 48,
            "excluded_retracted": 2,
        }
    )
    assert svg.startswith("<svg")
    assert "n = 125" in svg  # total identified
    assert "n = 48" in svg


def test_grade_sof_table_rows_and_certainty():
    meta = {
        "fatigue": {
            "n_studies": 8,
            "pooled_r": 0.31,
            "ci": [0.2, 0.42],
            "i_squared": 80.0,
            "publication_bias": {"publication_bias_risk": "high"},
        },
        "rare": {"n_studies": 1, "pooled_r": 0.5, "ci": [0.1, 0.9], "i_squared": 0.0, "publication_bias": {}},
    }
    table = grade_sof_table(meta, min_studies=2)
    assert "fatigue" in table
    assert "rare" not in table  # below min_studies
    assert "Low" in table  # 2 downgrades → Low


def test_grade_sof_empty_when_no_factor():
    assert "not reported" in grade_sof_table({}, min_studies=2)


def test_qa_sheet_contains_run_id_and_framing():
    md = qa_sheet_markdown(
        {"run_id": "abc123", "deep_success_rate": 99.0, "cui_verified_pct": 82.0, "engine_version": "3.1.0"}
    )
    assert "abc123" in md
    assert "not a registered systematic review" in md


def test_one_pager_has_sections():
    md = one_pager_markdown(
        "Fibromyalgia",
        {"headline": "X", "key_points": ["a", "b"]},
        {"run_id": "id1", "deep_success_rate": 99},
        "2026-06-10",
    )
    assert "Executive One-Pager" in md
    assert "Principal finding" in md


# ── DOCX ─────────────────────────────────────────────────────────────────────
def test_docx_renders(tmp_path):
    from utils.export_docx import markdown_to_docx

    md = (
        "# Title\n\nA paragraph.\n\n## Section\n\n- bullet one\n- bullet two\n\n"
        "| A | B |\n|---|---|\n| 1 | 2 |\n"
    )
    out = tmp_path / "r.docx"
    markdown_to_docx(md, out, title="Test", run_id="rid")
    assert out.exists() and out.stat().st_size > 0
    import docx

    d = docx.Document(str(out))
    text = "\n".join(p.text for p in d.paragraphs)
    assert "Hams & Co. Research Division" in text
