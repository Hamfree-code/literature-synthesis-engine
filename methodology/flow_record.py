"""WP-1 — PRISMA-style flow record, selection-before-extraction, substitution.

DEFECT: the "top 9" were selected *after* extraction succeeded, so failures
silently reshuffled the ranking; failed papers vanished.

FIX:
  * Compute the deep-analysis ranking BEFORE extraction → the *intended* set.
  * If an intended paper fails extraction, attempt the next-ranked replacement
    and record the substitution explicitly.
  * Emit a first-class PRISMA-style flow record (N0..N8) for every run, stored
    as structured JSON in the run registry and rendered as a diagram.
  * Conservation invariant: no paper disappears silently —
        n_intended == n_extracted + n_failed_without_substitution
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from methodology.extraction_schema import FailureReason


@dataclass
class Substitution:
    """One intended paper that failed, backfilled by a ranked replacement."""

    failed_id: str
    replacement_id: str
    failed_reason: str


@dataclass
class SelectionOutcome:
    """The result of reconciling an *intended* deep set against extraction
    outcomes, with explicit substitution accounting."""

    n_intended: int
    n_extracted: int
    n_failed: int                       # intended papers that failed extraction
    n_substituted: int                  # failed slots successfully backfilled
    intended_ids: list[str]
    final_corpus_ids: list[str]         # slots filled by a successful extraction
    failed_without_substitution: list[str]
    substitutions: list[Substitution]
    failure_reasons: dict[str, str]     # paper_id -> FailureReason value

    @property
    def n_failed_without_substitution(self) -> int:
        return len(self.failed_without_substitution)

    def assert_conservation(self) -> None:
        """No paper disappears silently (acceptance §2.4)."""
        assert self.n_intended == self.n_extracted + self.n_failed_without_substitution, (
            f"conservation violated: n_intended={self.n_intended} != "
            f"n_extracted={self.n_extracted} + "
            f"n_failed_without_substitution={self.n_failed_without_substitution}"
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["n_failed_without_substitution"] = self.n_failed_without_substitution
        return d


def reconcile_selection(
    intended_ids: list[str],
    succeeded_ids: set[str],
    replacement_pool: list[str] | None = None,
    failure_reasons: dict[str, str] | None = None,
) -> SelectionOutcome:
    """Reconcile the intended deep set against which extractions succeeded.

    Args:
        intended_ids: the deep set chosen BEFORE extraction, in rank order.
        succeeded_ids: every paper id (intended or replacement) whose
            extraction validated successfully.
        replacement_pool: ranked candidate replacements (next-best papers).
        failure_reasons: optional map of paper_id -> FailureReason value.

    Each intended slot is filled by the intended paper if it succeeded; else by
    the first ranked replacement that succeeded and is not already used; else it
    remains empty (failed-without-substitution).
    """
    replacement_pool = list(replacement_pool or [])
    failure_reasons = dict(failure_reasons or {})

    final_corpus: list[str] = []
    failed_without_sub: list[str] = []
    substitutions: list[Substitution] = []
    used_replacements: set[str] = set()
    n_failed = 0

    def next_replacement() -> str | None:
        for cand in replacement_pool:
            if cand in used_replacements:
                continue
            if cand in intended_ids:
                continue
            if cand in succeeded_ids:
                used_replacements.add(cand)
                return cand
        return None

    for pid in intended_ids:
        if pid in succeeded_ids:
            final_corpus.append(pid)
            continue
        # intended paper failed
        n_failed += 1
        reason = failure_reasons.get(pid, FailureReason.SCHEMA_VIOLATION.value)
        repl = next_replacement()
        if repl is not None:
            substitutions.append(Substitution(failed_id=pid, replacement_id=repl, failed_reason=reason))
            final_corpus.append(repl)
        else:
            failed_without_sub.append(pid)

    return SelectionOutcome(
        n_intended=len(intended_ids),
        n_extracted=len(final_corpus),
        n_failed=n_failed,
        n_substituted=len(substitutions),
        intended_ids=list(intended_ids),
        final_corpus_ids=final_corpus,
        failed_without_substitution=failed_without_sub,
        substitutions=substitutions,
        failure_reasons=failure_reasons,
    )


@dataclass
class FlowRecord:
    """PRISMA-style flow (WP-1.4). N0..N8 plus a failure breakdown by reason."""

    identified: int          # N0 — PMC search hits
    triaged: int             # N1 — abstract pass (Haiku)
    eligible: int            # N2 — eligible after triage
    selected_deep: int       # N3 — intended deep set
    extraction_attempted: int  # N4
    extraction_succeeded: int  # N5
    extraction_failed: int     # N6
    substitutions: int         # N7
    final_corpus: int          # N8 == N5
    failures_by_reason: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def as_text_diagram(self) -> str:
        """A plain-text flow diagram for the report (and a structured JSON twin
        is stored in the run registry)."""
        fr = self.failures_by_reason or {}
        fail_detail = ", ".join(f"{k}:{v}" for k, v in sorted(fr.items())) or "none"
        rows = [
            ("identified (PMC search hits)", self.identified),
            ("triaged (abstract, Haiku)", self.triaged),
            ("eligible after triage", self.eligible),
            ("selected for deep extraction (intended)", self.selected_deep),
            ("extraction attempted", self.extraction_attempted),
            ("extraction succeeded", self.extraction_succeeded),
            ("extraction failed", self.extraction_failed),
            ("substitutions performed", self.substitutions),
            ("final synthesised corpus", self.final_corpus),
        ]
        width = max(len(label) for label, _ in rows)
        lines = ["EVIDENCE FLOW (PRISMA-style)", "=" * (width + 12)]
        for label, n in rows:
            lines.append(f"{label:<{width}}  →  {n}")
        lines.append("-" * (width + 12))
        lines.append(f"extraction failures by reason: {fail_detail}")
        return "\n".join(lines)


def build_flow_record(
    *,
    identified: int,
    triaged: int,
    eligible: int,
    selection: SelectionOutcome,
    failures_by_reason: dict[str, int] | None = None,
) -> FlowRecord:
    """Assemble a :class:`FlowRecord` from upstream counts + the reconciled
    selection. N8 (final corpus) equals N5 (extraction succeeded) by design."""
    selection.assert_conservation()
    return FlowRecord(
        identified=identified,
        triaged=triaged,
        eligible=eligible,
        selected_deep=selection.n_intended,
        extraction_attempted=selection.n_intended + selection.n_substituted,
        extraction_succeeded=selection.n_extracted,
        extraction_failed=selection.n_failed,
        substitutions=selection.n_substituted,
        final_corpus=selection.n_extracted,
        failures_by_reason=dict(failures_by_reason or {}),
    )
