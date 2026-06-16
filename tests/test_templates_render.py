"""Template render smoke tests — verify the EMCU-reframed templates render
without Jinja errors and satisfy the acceptance-facing rendering rules:

  * EMCU disclaimer present; no affirmative "systematic review" self-reference.
  * Symptom table uses a mention-frequency column, never "Prevalence".
  * GRADE is rendered per outcome (not per paper).
  * Quantitative synthesis shows structured refusals, not pooled [CALC] figures.
  * A speculative-max due-diligence brief emits gaps-only — no Phase II
    skeleton, no N=400, no named drugs.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from methodology import emcu
from methodology import output_ceiling as oc

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


@pytest.fixture
def env():
    e = Environment(loader=FileSystemLoader(str(TEMPLATES)), trim_blocks=True, lstrip_blocks=True)
    # stub the provenance/badge filters used by the templates
    e.filters["cite"] = lambda s: (s or "")
    e.filters["cite_doi"] = lambda s: "[1]"
    e.filters["llm"] = lambda _v=None: ""
    e.filters["calc"] = lambda _v=None: ""
    e.filters["consensus"] = lambda _v=None: ""
    return e


def _report_aggregates():
    return {
        "n_papers": 21,
        "rct_count": 0,
        "output_ceiling_tier": "speculative",
        "outcome_dictionary_version": "1.0",
        "reconciliations_triggered": 2,
        "flow_record": {
            "diagram": "EVIDENCE FLOW\nselected_deep → 21\nextraction succeeded → 9",
            "flow": {"selected_deep": 21, "extraction_succeeded": 9, "extraction_failed": 12, "substitutions": 0},
        },
        "rob_instrument_counts": {"JBI Prevalence checklist": 5, "Newcastle-Ottawa (NOS)": 3, "PROBAST": 1},
        "quadas_paper_count": 0,
        "evidence_bodies": [
            {
                "outcome": "cognitive_function",
                "comparison": "Long COVID vs control",
                "contributing_papers": ["PMC1", "PMC2", "PMC3"],
                "study_designs": ["cross_sectional", "cross_sectional", "cohort"],
                "starting_certainty": "low",
                "downgrades": {"risk_of_bias": -2, "imprecision": -1, "inconsistency": 0, "indirectness": 0, "publication_bias": 0},
                "upgrades": {"large_effect": 0, "dose_response": 0, "plausible_confounding": 0},
                "final_grade": "very_low",
                "rationale": "Starting certainty low (observational-dominated) [CALC]. Downgraded for risk_of_bias (-2) [LLM]. Final certainty very_low [CALC].",
            },
        ],
        "gated_synthesis": {
            "siciliano_claim": "No outcome in this corpus met preconditions for quantitative synthesis.",
            "any_outcome_qualified": False,
            "decisions": [
                {"outcome": "cognitive_function", "n_studies": 3, "pooling_performed": False,
                 "pooling_note": "no quantitative pooling was performed for cognitive_function because studies use incommensurable case definitions (different ascertainment)",
                 "egger_performed": False, "egger_note": "insufficient studies for small-study-effect assessment (n=3 < 10)"},
            ],
        },
        "consensus": {"fatigue": {"count": 9, "pct": 47.4}, "brain fog": {"count": 5, "pct": 26.3}},
        "definition_heterogeneity": {"4": 2, "12": 5},
        "findings_by_certainty": {"established": [], "probable": [], "possible": [], "speculative": [1, 2], "contradicted": []},
        "normalisation_review": ["left toe tingling"],
    }


def test_report_renders_emcu_correct(env):
    tpl = env.get_template("report.md.j2")
    out = tpl.render(
        date="2026-06-16", topic_title="Long COVID", n_papers=21, n_deep=9,
        synthesis={"executive_summary": "Body."},
        aggregates=_report_aggregates(), papers_by_id={}, short_cite=lambda p: "x",
        emcu_disclaimer=emcu.EMCU_DISCLAIMER,
    )
    # EMCU framing
    assert "structured evidence map, not a systematic review" in out
    assert emcu.lint_self_reference(out) == []
    # mention-frequency, never "Prevalence" as the column
    assert "Paper-mention frequency (% of symptom-reporting papers)" in out
    assert "47.4% of papers" in out
    # GRADE per outcome
    assert "GRADE Certainty, by Outcome" in out
    assert "cognitive function" in out and "very_low" in out
    # zero RCTs → out of scope
    assert "out of scope" in out
    # gated synthesis structured refusal, no pooled CALC estimate
    assert "no quantitative pooling was performed" in out
    assert "insufficient studies for small-study-effect assessment" in out
    # QUADAS applied to 0 papers
    assert "QUADAS-2 applied to 0 papers" in out
    # flow record present
    assert "EVIDENCE FLOW" in out
    # normalisation review surfaced
    assert "left toe tingling" in out


def test_due_diligence_speculative_gaps_only(env):
    dd = {
        "investment_thesis_one_line": "Thesis.",
        "executive_summary": "Summary.",
        "recommended_target_phenotype": {
            "phenotype": "autonomic",
            "confidence_in_recommendation": 38,
            "rationale": "Why.",
            "phase_ii_design_skeleton": {
                "inclusion_criteria": ["adults"], "exclusion_criteria": ["x"],
                "primary_endpoint": "fatigue", "secondary_endpoints": ["y"],
                "comparator": "usual care", "estimated_sample_size_basis": "N=400 at 80% power",
            },
            "deal_breakers": ["risk"],
        },
        "target_trial_emulation_inventory": {"studies": [{"doi": "PMC1", "drug_class": "metformin", "design_quality": "weak", "phase_ii_signal": "equivocal", "one_line": "z"}]},
    }
    gated = oc.gate_due_diligence(dd, "speculative")
    tpl = env.get_template("due_diligence.md.j2")
    out = tpl.render(
        date="2026-06-16", topic_title="Long COVID", n_papers=21, n_deep=9,
        dd=gated, aggregates=_report_aggregates(), papers_by_id={},
        emcu_disclaimer=emcu.EMCU_DISCLAIMER,
    )
    assert "structured evidence map, not a systematic review" in out
    # gaps-only path, NOT a Phase II skeleton
    assert "landscape + gaps only" in out
    assert "N=400" not in out
    assert "metformin" not in out
    assert "Inclusion criteria" not in out  # no skeleton inclusion list
    # low-confidence reframing
    assert "hypothesis for expert evaluation" in out.lower()


def test_executive_summary_has_disclaimer(env):
    tpl = env.get_template("executive_summary.md.j2")
    out = tpl.render(
        date="2026-06-16", topic_title="Long COVID", n_papers=21, n_deep=9,
        exec={"headline": "H"}, emcu_disclaimer=emcu.EMCU_DISCLAIMER,
    )
    assert "structured evidence map, not a systematic review" in out
    assert emcu.lint_self_reference(out) == []
