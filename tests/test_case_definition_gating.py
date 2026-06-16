"""WP-7 — case-definition gating.

Acceptance (spec §8):
  * The prevalence section renders as a definition-stratified table; no single
    "prevalence range" spanning incommensurable definitions is presented as a
    unified figure.
  * An aggregation mixing a 4-week and a 12-week cohort into one pooled
    prevalence raises IncommensurableDefinitions.
"""
from __future__ import annotations

import pytest

from methodology import case_definition as cd
from methodology.case_definition import (
    CaseDefinition,
    DefinitionSource,
    IncommensurableDefinitions,
)


def _cdef(weeks, source=DefinitionSource.WHO, fir=False):
    return CaseDefinition(duration_threshold_weeks=weeks, definition_source=source, functional_impact_required=fir)


def test_canonicalize_from_extraction():
    ext = {"factual_extraction": {
        "long_covid_definition_weeks": 12,
        "definition_source": "WHO_2021",
        "long_covid_definition": "symptoms >= 12 weeks with functional impact",
    }}
    c = cd.canonicalize_case_definition(ext)
    assert c.duration_threshold_weeks == 12
    assert c.definition_source == DefinitionSource.WHO
    assert c.functional_impact_required is True


def test_icd10_source_canonicalises():
    c = cd.canonicalize_case_definition({"factual_extraction": {"definition_source": "ICD10_U099"}})
    assert c.definition_source == DefinitionSource.ICD10_U099


def test_commensurable_same_stratum():
    assert cd.are_commensurable(_cdef(12), _cdef(12))


def test_incommensurable_different_duration():
    assert not cd.are_commensurable(_cdef(4), _cdef(12))


def test_incommensurable_same_duration_different_source():
    # administrative ICD-10 vs self-report WHO clinical cohort, both 12 weeks
    assert not cd.are_commensurable(
        _cdef(12, DefinitionSource.WHO), _cdef(12, DefinitionSource.ICD10_U099)
    )


def test_pooling_4wk_and_12wk_raises():
    rows = [
        {"value": 0.70, "case_definition": _cdef(4, DefinitionSource.AUTHOR_DEFINED)},
        {"value": 0.20, "case_definition": _cdef(12, DefinitionSource.WHO)},
    ]
    with pytest.raises(IncommensurableDefinitions):
        cd.aggregate_within_stratum(rows)


def test_within_stratum_aggregation_ok():
    rows = [
        {"value": 0.30, "case_definition": _cdef(12, DefinitionSource.WHO)},
        {"value": 0.45, "case_definition": _cdef(12, DefinitionSource.WHO)},
    ]
    agg = cd.aggregate_within_stratum(rows)
    assert agg["n"] == 2
    assert agg["min"] == 0.30 and agg["max"] == 0.45


def test_stratified_prevalence_table_never_unifies_range():
    # the 3.89%-70% spread: administrative ICD-10 (3.89%) vs self-report (70%)
    rows = [
        {"value": 3.89, "case_definition": _cdef(4, DefinitionSource.ICD10_U099)},
        {"value": 70.0, "case_definition": _cdef(12, DefinitionSource.AUTHOR_DEFINED)},
        {"value": 65.0, "case_definition": _cdef(12, DefinitionSource.AUTHOR_DEFINED)},
    ]
    table = cd.stratified_prevalence_table(rows)
    assert table["n_strata"] == 2
    # no stratum claims to be poolable with the others
    assert all(s["poolable_with_others"] is False for s in table["strata"])
    # the administrative stratum is its own row, not merged with self-report
    admin = [s for s in table["strata"] if "ICD-10" in s["stratum"]][0]
    assert admin["range"] == [3.89, 3.89]
    assert "ascertainment" in table["ascertainment_note"].lower()
