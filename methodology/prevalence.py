"""WP-4 — "Prevalence" mislabel and frequency-vs-prevalence confusion.

DEFECT: the symptom table labelled a column "Prevalence" with values like
"fatigue 47.4%". That figure is the percentage of *papers that mention* the
symptom, not the percentage of *patients* with it. A clinician reads it as
clinical prevalence — an active mislabel.

FIX: separate the two quantities at the type level.
  * ``MentionFrequency``        = (papers naming symptom) / (papers naming any
    symptom); unit "% of papers". Column title is explicit and never "Prevalence".
  * ``PooledPatientPrevalence`` = only when a defensible pooling is possible
    (WP-6); otherwise ``None`` → rendered as an em-dash, never a borrowed number.

A render guard rejects any payload that places a mention-frequency under a
prevalence-typed column.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

EM_DASH = "—"
MENTION_FREQUENCY_COLUMN_TITLE = "Paper-mention frequency (% of symptom-reporting papers)"
PATIENT_PREVALENCE_COLUMN_TITLE = "Pooled patient prevalence"


class ColumnType(enum.Enum):
    MENTION_FREQUENCY = "mention_frequency"
    PATIENT_PREVALENCE = "patient_prevalence"


class PrevalenceMislabelError(TypeError):
    """Raised when a mention-frequency is placed under a prevalence column."""


@dataclass(frozen=True)
class MentionFrequency:
    """Share of symptom-reporting papers that name this symptom."""

    papers_mentioning: int
    papers_any_symptom: int
    column_type: ColumnType = ColumnType.MENTION_FREQUENCY

    @property
    def value_pct(self) -> float:
        if not self.papers_any_symptom:
            return 0.0
        return round(100.0 * self.papers_mentioning / self.papers_any_symptom, 1)

    def render(self) -> str:
        return f"{self.value_pct}% of papers"


@dataclass(frozen=True)
class PooledPatientPrevalence:
    """Patient-level prevalence. ``value`` is ``None`` unless a defensible
    pooling exists. A figure carried from a single source is attributed inline
    and tagged [LLM] — never aggregated into a corpus-level prevalence."""

    value: float | None = None
    source_id: str | None = None
    column_type: ColumnType = ColumnType.PATIENT_PREVALENCE

    @property
    def provenance_tag(self) -> str:
        return "[LLM]" if (self.value is not None and self.source_id) else ""

    def render(self) -> str:
        if self.value is None:
            return EM_DASH
        if self.source_id:
            return f"{self.value}% (as reported by source {self.source_id})"
        return f"{self.value}%"


def compute_mention_frequency(papers_mentioning: int, papers_any_symptom: int) -> MentionFrequency:
    return MentionFrequency(papers_mentioning=papers_mentioning, papers_any_symptom=papers_any_symptom)


def assert_cell_type(value, expected: ColumnType) -> None:
    """Reject a cell whose semantic type does not match its column type."""
    actual = getattr(value, "column_type", None)
    if actual is not expected:
        raise PrevalenceMislabelError(
            f"value of type {actual} cannot be rendered under a {expected} column "
            f"(mention-frequency is NOT patient prevalence)"
        )


def render_cell(value) -> str:
    return value.render()


def render_symptom_table(rows: list[dict], column_spec: list[tuple[str, ColumnType]]) -> list[dict]:
    """Validate and render a symptom table.

    ``column_spec`` is a list of ``(column_title, ColumnType)``. Each row maps
    column titles to typed values. Raises :class:`PrevalenceMislabelError` if a
    value's type does not match its column — e.g. a mention-frequency placed
    under a prevalence column. Also forbids a literal "Prevalence" title on a
    mention-frequency column.
    """
    for title, ctype in column_spec:
        if ctype is ColumnType.MENTION_FREQUENCY and title.strip().lower() == "prevalence":
            raise PrevalenceMislabelError(
                "a mention-frequency column may not be titled 'Prevalence'"
            )
    rendered: list[dict] = []
    for row in rows:
        out: dict = {}
        for title, ctype in column_spec:
            if title not in row:
                continue
            assert_cell_type(row[title], ctype)
            out[title] = render_cell(row[title])
        rendered.append(out)
    return rendered
