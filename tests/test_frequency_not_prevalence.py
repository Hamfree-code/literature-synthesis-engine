"""WP-4 — mention-frequency cannot render under a prevalence column.

Acceptance (spec §5):
  * No table column titled "Prevalence" contains paper-mention frequencies.
  * The symptom landscape renderer rejects a payload that places
    mention-frequency under a prevalence-typed column.
"""
from __future__ import annotations

import pytest

from methodology import prevalence as pv
from methodology.prevalence import (
    ColumnType,
    MentionFrequency,
    PooledPatientPrevalence,
    PrevalenceMislabelError,
)


def test_mention_frequency_value_and_unit():
    mf = pv.compute_mention_frequency(9, 19)
    assert mf.value_pct == 47.4
    assert "% of papers" in mf.render()


def test_null_prevalence_renders_em_dash():
    p = PooledPatientPrevalence(value=None)
    assert p.render() == pv.EM_DASH


def test_single_source_prevalence_is_attributed_and_llm():
    p = PooledPatientPrevalence(value=30.0, source_id="PMC999")
    assert p.provenance_tag == "[LLM]"
    assert "as reported by source PMC999" in p.render()


def test_mention_frequency_rejected_under_prevalence_column():
    mf = MentionFrequency(papers_mentioning=9, papers_any_symptom=19)
    with pytest.raises(PrevalenceMislabelError):
        pv.assert_cell_type(mf, ColumnType.PATIENT_PREVALENCE)


def test_render_table_rejects_mislabelled_payload():
    rows = [{
        "Pooled patient prevalence": MentionFrequency(9, 19),  # WRONG type for this column
    }]
    spec = [("Pooled patient prevalence", ColumnType.PATIENT_PREVALENCE)]
    with pytest.raises(PrevalenceMislabelError):
        pv.render_symptom_table(rows, spec)


def test_render_table_rejects_prevalence_title_on_frequency_column():
    spec = [("Prevalence", ColumnType.MENTION_FREQUENCY)]
    with pytest.raises(PrevalenceMislabelError):
        pv.render_symptom_table([], spec)


def test_correctly_typed_table_renders():
    rows = [
        {
            pv.MENTION_FREQUENCY_COLUMN_TITLE: MentionFrequency(9, 19),
            pv.PATIENT_PREVALENCE_COLUMN_TITLE: PooledPatientPrevalence(value=None),
        },
    ]
    spec = [
        (pv.MENTION_FREQUENCY_COLUMN_TITLE, ColumnType.MENTION_FREQUENCY),
        (pv.PATIENT_PREVALENCE_COLUMN_TITLE, ColumnType.PATIENT_PREVALENCE),
    ]
    out = pv.render_symptom_table(rows, spec)
    assert out[0][pv.MENTION_FREQUENCY_COLUMN_TITLE] == "47.4% of papers"
    assert out[0][pv.PATIENT_PREVALENCE_COLUMN_TITLE] == pv.EM_DASH
