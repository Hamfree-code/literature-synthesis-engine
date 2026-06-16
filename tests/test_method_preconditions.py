"""WP-6 — statistical-method preconditions.

Acceptance (spec §7):
  * For the Long COVID run, no [CALC] pooled estimate appears (preconditions
    fail); every quantitative figure is either source-attributed [LLM] or
    absent.
  * Requesting Egger's on 9 studies returns a structured refusal with the
    precondition message, not a p-value.
"""
from __future__ import annotations

from methodology import synthesis_gating as sg
from methodology.case_definition import CaseDefinition, DefinitionSource


def _cd(weeks, source=DefinitionSource.WHO):
    return CaseDefinition(weeks, source, False)


# ---- Egger / small-study effects ------------------------------------------

def test_egger_refused_below_ten_studies():
    res = sg.egger_test(9)
    assert res.is_refusal()
    assert "insufficient studies" in res.reason
    assert res.value is None          # no p-value
    assert res.provenance_tag == ""   # not [CALC]


def test_egger_performed_at_ten_studies():
    res = sg.egger_test(10, tester=lambda: {"egger_p": 0.12})
    assert res.performed
    assert res.provenance_tag == "[CALC]"
    assert res.value["egger_p"] == 0.12


# ---- pooling preconditions -------------------------------------------------

def test_pooling_refused_single_study():
    ok, reason = sg.can_pool(["fatigue"], ["r"], [_cd(12)])
    assert not ok and "need ≥2" in reason


def test_pooling_refused_across_case_definitions():
    ok, reason = sg.can_pool(
        ["fatigue", "fatigue"], ["r", "r"], [_cd(4), _cd(12)]
    )
    assert not ok
    assert "case definition" in reason


def test_pooling_refused_different_outcomes():
    ok, reason = sg.can_pool(["fatigue", "dyspnea"], ["r", "r"], [_cd(12), _cd(12)])
    assert not ok and "different canonical outcomes" in reason


def test_pooling_refused_incommensurable_metrics():
    ok, reason = sg.can_pool(["fatigue", "fatigue"], ["r", "prevalence"], [_cd(12), _cd(12)])
    assert not ok and "commensurable" in reason


def test_pooling_allowed_when_all_preconditions_met():
    ok, reason = sg.can_pool(["fatigue", "fatigue"], ["r", "OR"], [_cd(12), _cd(12)])
    assert ok and reason is None


def test_random_effects_pool_refuses_with_structured_message():
    res = sg.random_effects_pool(["fatigue", "fatigue"], ["r", "r"], [_cd(4), _cd(12)])
    assert res.is_refusal()
    assert res.value is None
    assert res.provenance_tag == ""
    assert "no quantitative pooling was performed" in res.reason


def test_random_effects_pool_runs_when_qualified():
    res = sg.random_effects_pool(
        ["fatigue", "fatigue"], ["r", "r"], [_cd(12), _cd(12)],
        pooler=lambda: {"pooled_r": 0.3, "ci": [0.2, 0.4]},
    )
    assert res.performed and res.provenance_tag == "[CALC]"


# ---- externally-reported statistics are quarantined (§7.2) ----------------

def test_external_stat_is_llm_tagged_and_attributed():
    q = sg.quarantine_external_stat("30% brain fog (95% CI 28-32%)", "PMC999")
    assert q.provenance_tag == "[LLM]"
    assert "as reported by source PMC999" in q.render()


def test_external_stat_flags_undescribed_method():
    q = sg.quarantine_external_stat("30% (95% CI 28-32%)", "PMC999", source_method_described=False)
    assert any("not independently verifiable" in f for f in q.flags)


# ---- Siciliano claim only when something qualified (§7.3) ------------------

def test_siciliano_claim_removed_when_nothing_qualified():
    msg = sg.siciliano_claim(any_outcome_qualified=False)
    assert "No outcome" in msg and "Siciliano" not in msg


def test_siciliano_claim_allowed_when_qualified():
    msg = sg.siciliano_claim(any_outcome_qualified=True)
    assert "Siciliano" in msg
