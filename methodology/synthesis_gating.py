"""WP-6 — Honest synthesis: no meta-analysis theatre.

DEFECT: statistical routines (DerSimonian-Laird pooling, Egger's) were claimed
as [CALC] though 0 papers qualified; a pooled "30% brain fog (95% CI 28-32%)"
was reported — a CI borrowed from one included review — and Egger on <10
heterogeneous studies is uninterpretable.

FIX: gate every statistical method behind its preconditions and emit a
structured refusal (with the precondition message) when they fail. Quarantine
externally-reported statistics: a CI/estimate lifted from an included paper is
rendered "as reported by source [n]", tagged [LLM], never [CALC].
"""
from __future__ import annotations

from dataclasses import dataclass, field

from methodology.case_definition import CaseDefinition, are_commensurable

# Minimum studies for a small-study-effects assessment (Egger / funnel).
MIN_STUDIES_SMALL_STUDY_EFFECTS = 10
# Minimum studies for any pooling at all.
MIN_STUDIES_POOLING = 2

# Metrics that share an underlying scale (the extraction converts these to an
# r-equivalent). Proportions are a separate, non-commensurable family.
_CORRELATION_LIKE = {"r", "or", "rr", "hr", "aor", "md", "smd", "beta"}
_PROPORTION_LIKE = {"prevalence", "proportion", "percentage", "rate"}


@dataclass
class MethodResult:
    """The outcome of attempting a gated statistical method.

    ``performed=False`` is a *structured refusal*: it carries the precondition
    message in ``reason`` and never carries a spurious statistic.
    """

    method: str
    performed: bool
    provenance_tag: str            # "[CALC]" when performed, "" when refused
    reason: str | None = None
    value: dict | None = None

    def is_refusal(self) -> bool:
        return not self.performed


def _metric_family(metric: str) -> str | None:
    m = (metric or "").strip().lower()
    if m in _CORRELATION_LIKE:
        return "correlation"
    if m in _PROPORTION_LIKE:
        return "proportion"
    return None


def metrics_commensurable(metrics: list[str]) -> bool:
    fams = {_metric_family(m) for m in metrics}
    return len(fams) == 1 and None not in fams


def can_pool(
    outcomes: list[str],
    metrics: list[str],
    case_definitions: list[CaseDefinition],
) -> tuple[bool, str | None]:
    """Random-effects pooling precondition (§7.1): ≥2 studies reporting the
    SAME canonical outcome on a COMMENSURABLE metric sharing a COMPATIBLE case
    definition. Returns ``(ok, reason_if_not)``."""
    n = len(outcomes)
    if n < MIN_STUDIES_POOLING:
        return False, f"only {n} study reports this outcome (need ≥{MIN_STUDIES_POOLING})"
    if len(set(outcomes)) != 1:
        return False, "studies report different canonical outcomes (not the same outcome)"
    if not metrics_commensurable(metrics):
        return False, "effect metrics are not commensurable (cannot be pooled on one scale)"
    first = case_definitions[0]
    if not all(are_commensurable(first, cd) for cd in case_definitions[1:]):
        return False, "studies use incommensurable case definitions (different ascertainment)"
    return True, None


def random_effects_pool(
    outcomes: list[str],
    metrics: list[str],
    case_definitions: list[CaseDefinition],
    pooler=None,
) -> MethodResult:
    """Run random-effects pooling only if preconditions hold; else a structured
    refusal stating why. ``pooler`` is the deterministic numeric routine (kept
    injectable so the gate is unit-testable without numpy)."""
    ok, reason = can_pool(outcomes, metrics, case_definitions)
    if not ok:
        return MethodResult(
            method="random-effects pooling (DerSimonian-Laird)",
            performed=False,
            provenance_tag="",
            reason=f"no quantitative pooling was performed for this outcome because {reason}",
        )
    value = pooler() if pooler else {"n_studies": len(outcomes)}
    return MethodResult(
        method="random-effects pooling (DerSimonian-Laird)",
        performed=True,
        provenance_tag="[CALC]",
        value=value,
    )


def can_assess_small_study_effects(n_studies: int) -> bool:
    return n_studies >= MIN_STUDIES_SMALL_STUDY_EFFECTS


def egger_test(n_studies: int, tester=None) -> MethodResult:
    """Egger's test / funnel plot — only when ≥10 studies contribute to a single
    pooled outcome. Below that, a structured refusal (no p-value).

    Acceptance §7: requesting Egger's on 9 studies returns a structured refusal
    with the precondition message, not a p-value.
    """
    if not can_assess_small_study_effects(n_studies):
        return MethodResult(
            method="Egger's test / funnel plot",
            performed=False,
            provenance_tag="",
            reason=(
                f"insufficient studies for small-study-effect assessment "
                f"(n={n_studies} < {MIN_STUDIES_SMALL_STUDY_EFFECTS})"
            ),
        )
    value = tester() if tester else {"n_studies": n_studies}
    return MethodResult(
        method="Egger's test / funnel plot",
        performed=True,
        provenance_tag="[CALC]",
        value=value,
    )


@dataclass
class QuarantinedStatistic:
    """An externally-reported statistic, kept OUT of the [CALC] lane (§7.2)."""

    statistic: str
    source_id: str
    provenance_tag: str = "[LLM]"          # extracted, never [CALC]
    attribution: str = ""
    flags: list[str] = field(default_factory=list)

    def render(self) -> str:
        base = f"{self.statistic} (as reported by source {self.source_id})"
        if self.flags:
            base += " — " + "; ".join(self.flags)
        return base


def quarantine_external_stat(
    statistic: str,
    source_id: str,
    *,
    source_method_described: bool = True,
) -> QuarantinedStatistic:
    """Wrap a CI/estimate lifted from an included paper. It is attributed to the
    source, tagged [LLM], and never placed in a [CALC] column. If the source's
    own method is undescribed, append the verifiability flag."""
    flags: list[str] = []
    if not source_method_described:
        flags.append("source pooling method not described; not independently verifiable")
    return QuarantinedStatistic(
        statistic=statistic,
        source_id=source_id,
        attribution=f"as reported by source {source_id}",
        flags=flags,
    )


def siciliano_claim(any_outcome_qualified: bool) -> str:
    """The 'emulates the analytical standard of Siciliano et al.' line may only
    remain if gated methods actually executed on a qualifying outcome (§7.3)."""
    if any_outcome_qualified:
        return ("This run applied the same gated quantitative methods used by "
                "Siciliano et al. on the outcome(s) that met preconditions.")
    return "No outcome in this corpus met preconditions for quantitative synthesis."
