"""WP-7 — Case-definition gating.

DEFECT: the report correctly identified six duration thresholds and said
cross-study pooling is impossible — then still presented cross-study prevalence
ranges as if cataloguing them were meaningful. The insight was stated but not
enforced.

FIX: case definition is extracted, canonicalised, and used as a *gate* on
aggregation. Any aggregation (counts, ranges, pooled estimates) is computed
within a case-definition stratum, never across incommensurable strata. The
3.89%-70% prevalence spread must be rendered as a definition-stratified table,
not a single unified range. Mixing strata raises ``IncommensurableDefinitions``.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass


class DefinitionSource(str, enum.Enum):
    WHO = "WHO"
    NICE = "NICE"
    CDC = "CDC"
    ICD10_U099 = "ICD-10-U09.9"
    AUTHOR_DEFINED = "author-defined"
    NONE = "none"


_SOURCE_ALIASES = {
    "who": DefinitionSource.WHO,
    "who_2021": DefinitionSource.WHO,
    "nice": DefinitionSource.NICE,
    "cdc": DefinitionSource.CDC,
    "icd10_u099": DefinitionSource.ICD10_U099,
    "icd-10-u09.9": DefinitionSource.ICD10_U099,
    "icd10": DefinitionSource.ICD10_U099,
    "u09.9": DefinitionSource.ICD10_U099,
    "study_specific": DefinitionSource.AUTHOR_DEFINED,
    "author-defined": DefinitionSource.AUTHOR_DEFINED,
    "author_defined": DefinitionSource.AUTHOR_DEFINED,
    "not_specified": DefinitionSource.NONE,
    "none": DefinitionSource.NONE,
}


class IncommensurableDefinitions(ValueError):
    """Raised when aggregation mixes incommensurable case-definition strata."""


@dataclass(frozen=True)
class CaseDefinition:
    duration_threshold_weeks: int | None
    definition_source: DefinitionSource
    functional_impact_required: bool

    def stratum_key(self) -> tuple:
        """The key two definitions must share to be poolable. Keyed on duration
        threshold AND source: a 12-week WHO definition and a 12-week ICD-10
        administrative definition ascertain different populations."""
        return (self.duration_threshold_weeks, self.definition_source.value)

    def label(self) -> str:
        wk = f"≥{self.duration_threshold_weeks}wk" if self.duration_threshold_weeks is not None else "no-threshold"
        return f"{self.definition_source.value} / {wk}"


def normalize_source(raw: str | None) -> DefinitionSource:
    if not raw:
        return DefinitionSource.NONE
    return _SOURCE_ALIASES.get(str(raw).strip().lower(), DefinitionSource.AUTHOR_DEFINED)


def canonicalize_case_definition(extraction: dict) -> CaseDefinition:
    """Build a :class:`CaseDefinition` from a deep-extraction's factual block."""
    fx = extraction.get("factual_extraction") or extraction
    weeks = fx.get("long_covid_definition_weeks")
    try:
        weeks = int(weeks) if weeks is not None else None
    except (TypeError, ValueError):
        weeks = None
    source = normalize_source(fx.get("definition_source"))
    fir = fx.get("functional_impact_required")
    if fir is None:
        text = (fx.get("long_covid_definition") or "").lower()
        fir = ("functional" in text) or ("impact" in text) or ("daily activities" in text)
    return CaseDefinition(
        duration_threshold_weeks=weeks,
        definition_source=source,
        functional_impact_required=bool(fir),
    )


def are_commensurable(a: CaseDefinition, b: CaseDefinition) -> bool:
    """Two definitions may be pooled only if they share a stratum key."""
    return a.stratum_key() == b.stratum_key()


def stratify(rows: list[dict], *, case_def_key: str = "case_definition") -> dict[tuple, list[dict]]:
    """Group rows by case-definition stratum for side-by-side display."""
    strata: dict[tuple, list[dict]] = {}
    for r in rows:
        cd: CaseDefinition = r[case_def_key]
        strata.setdefault(cd.stratum_key(), []).append(r)
    return strata


def assert_poolable(case_definitions: list[CaseDefinition]) -> None:
    """Raise IncommensurableDefinitions unless every definition shares a stratum.

    Acceptance §8: an aggregation mixing a 4-week and a 12-week cohort raises.
    """
    keys = {cd.stratum_key() for cd in case_definitions}
    if len(keys) > 1:
        raise IncommensurableDefinitions(
            "cannot aggregate across incommensurable case definitions: "
            + ", ".join(sorted(str(k) for k in keys))
        )


def aggregate_within_stratum(rows: list[dict], *, value_key: str = "value", case_def_key: str = "case_definition") -> dict:
    """Aggregate (range) prevalence values WITHIN one stratum.

    Raises IncommensurableDefinitions if ``rows`` mix strata — pooling across
    definitions is forbidden.
    """
    if not rows:
        return {"n": 0, "min": None, "max": None}
    assert_poolable([r[case_def_key] for r in rows])
    values = [r[value_key] for r in rows if r.get(value_key) is not None]
    return {
        "n": len(rows),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "stratum": rows[0][case_def_key].label(),
    }


def stratified_prevalence_table(rows: list[dict], *, value_key: str = "value", case_def_key: str = "case_definition") -> dict:
    """Render prevalence as a definition-stratified table (never one unified
    range). Each stratum carries the 'not poolable' label; the overall spread is
    annotated as an ascertainment artifact, not biological variation."""
    strata = stratify(rows, case_def_key=case_def_key)
    table = []
    for key, srows in strata.items():
        agg = aggregate_within_stratum(srows, value_key=value_key, case_def_key=case_def_key)
        table.append({
            "stratum": srows[0][case_def_key].label(),
            "stratum_key": key,
            "n_studies": agg["n"],
            "range": [agg["min"], agg["max"]],
            "poolable_with_others": False,
            "note": "not poolable — different case definitions",
        })
    return {
        "strata": table,
        "n_strata": len(table),
        "ascertainment_note": (
            "The cross-stratum spread reflects ascertainment differences "
            "(case-definition / data source), not biological variation. It is "
            "NOT presented as a single pooled prevalence range."
        ),
    }
