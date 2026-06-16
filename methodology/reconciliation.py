"""WP-9 — Layer reconciliation (narrative vs calibrated certainty).

DEFECT: the prose bucketed findings as "Probable" while the calibrated tally
said Established 0 / Probable 0 / Possible 0 / Speculative 47. The narrative and
calibrated layers contradicted each other.

FIX: the ``calibrated_consensus`` layer is authoritative for the certainty tier.
The narrative may only assign a finding to a tier that matches its calibrated
tier. A reconciliation step asserts, for every finding mentioned in prose with a
certainty word, that the word equals the calibrated tier — mismatch fails the
build with a diff report.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Ordered weakest → strongest. "contradicted" is handled as its own bucket.
TIER_ORDER = ("speculative", "possible", "probable", "established")
_TIER_RANK = {t: i for i, t in enumerate(TIER_ORDER)}
CERTAINTY_WORDS = set(TIER_ORDER) | {"contradicted"}

_CERTAINTY_RE = re.compile(r"\b(established|probable|possible|speculative|contradicted)\b", re.IGNORECASE)


def normalize_tier(tier: str) -> str:
    return (tier or "").strip().lower()


class ReconciliationError(AssertionError):
    """Raised when prose certainty contradicts the calibrated tier."""


@dataclass
class Mismatch:
    finding_key: str
    prose_tier: str
    calibrated_tier: str

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"{self.finding_key!r}: prose says {self.prose_tier!r} but calibrated is {self.calibrated_tier!r}"


@dataclass
class ReconciliationReport:
    consistent: bool
    mismatches: list[Mismatch] = field(default_factory=list)
    checked: int = 0

    def diff(self) -> str:
        if self.consistent:
            return "OK: all prose certainty words match calibrated tiers."
        return "Certainty-layer mismatches:\n" + "\n".join(f"  - {m}" for m in self.mismatches)


def reconcile_findings(
    prose_findings: list[tuple[str, str]],
    calibrated_tiers: dict[str, str],
) -> ReconciliationReport:
    """Compare prose-asserted tiers against the authoritative calibrated tiers.

    ``prose_findings`` is a list of ``(finding_key, asserted_tier)`` pairs;
    ``calibrated_tiers`` maps ``finding_key -> calibrated tier`` (authoritative).
    A finding whose prose tier differs from its calibrated tier is a mismatch.
    """
    mismatches: list[Mismatch] = []
    checked = 0
    for key, prose_tier in prose_findings:
        cal = calibrated_tiers.get(key)
        if cal is None:
            continue
        checked += 1
        if normalize_tier(prose_tier) != normalize_tier(cal):
            mismatches.append(Mismatch(key, normalize_tier(prose_tier), normalize_tier(cal)))
    return ReconciliationReport(consistent=not mismatches, mismatches=mismatches, checked=checked)


def assert_consistent(
    prose_findings: list[tuple[str, str]],
    calibrated_tiers: dict[str, str],
) -> ReconciliationReport:
    """Fail the build (raise) if any prose tier contradicts the calibrated tier."""
    report = reconcile_findings(prose_findings, calibrated_tiers)
    if not report.consistent:
        raise ReconciliationError(report.diff())
    return report


def find_certainty_words(text: str) -> list[str]:
    return [m.group(1).lower() for m in _CERTAINTY_RE.finditer(text or "")]


def assert_prose_within_ceiling(text: str, max_tier: str) -> None:
    """Fail if prose uses a certainty word STRONGER than ``max_tier``.

    If the whole corpus is speculative, the prose may not contain
    "probable"/"possible"/"established" finding-claims.
    """
    ceiling = _TIER_RANK.get(normalize_tier(max_tier))
    if ceiling is None:
        return
    offenders = [w for w in find_certainty_words(text)
                 if w in _TIER_RANK and _TIER_RANK[w] > ceiling]
    if offenders:
        raise ReconciliationError(
            f"prose uses certainty language above the calibrated ceiling "
            f"({max_tier!r}): {sorted(set(offenders))}"
        )
