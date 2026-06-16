"""WP-0 / §1 — Product reframing: Evidence Mapping with Calibrated Uncertainty.

DEFECT: deliverables were implicitly framed as competing with a systematic
review (SR). They are not SRs and cannot survive being judged as one.

FIX: adopt and enforce a single product identity — EMCU — across all templates
and prose. This module provides:

  * EMCU_DISCLAIMER — the standing disclaimer block every report must carry.
  * lint_self_reference() — fails the build if a template *affirmatively*
    describes itself as a "systematic review".

The lint is deliberately narrow: it flags affirmative self-description
("this systematic review", "the present systematic review", "we conducted a
systematic review") but allows *negations* and *disclaimers* ("this is not a
systematic review", "not a substitute for an intervention systematic review"),
because the EMCU disclaimer itself contains the phrase in a negated form.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# The mandatory standing disclaimer (verbatim from the v3.2 spec §1.1).
EMCU_DISCLAIMER = (
    "This is a structured evidence map, not a systematic review. It does not "
    "perform protocol-registered dual-reviewer screening, design-based trial "
    "inclusion, or network meta-analysis. It maps what the open-access "
    "literature asserts and rates the certainty of those assertions. It is a "
    "scoping and triage instrument, not a substitute for an intervention "
    "systematic review."
)

# Affirmative self-description patterns. Each captures a phrase that claims the
# deliverable *is* / *was produced as* a systematic review. Negated forms are
# excluded by the negation guard below.
_AFFIRMATIVE_PATTERNS = (
    re.compile(r"\bthis\s+systematic\s+review\b", re.IGNORECASE),
    re.compile(r"\bthe\s+present\s+systematic\s+review\b", re.IGNORECASE),
    re.compile(r"\bthis\s+(?:report|document|analysis|review)\s+is\s+a\s+systematic\s+review\b", re.IGNORECASE),
    re.compile(r"\b(?:we|our team)\s+(?:conducted|performed|present)\s+(?:a|this|our)\s+systematic\s+review\b", re.IGNORECASE),
    re.compile(r"\bour\s+systematic\s+review\b", re.IGNORECASE),
    re.compile(r"\bin\s+this\s+systematic\s+review\b", re.IGNORECASE),
)

# Negation guard: if the phrase is preceded (within a short window) by a
# negator, it is a disclaimer, not a self-description.
_NEGATION = re.compile(
    r"\b(not|isn't|is not|aren't|are not|never|rather than|instead of|substitute for)\b[\sA-Za-z,]{0,40}$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LintViolation:
    """One self-referential 'systematic review' hit found by the linter."""

    line: int
    column: int
    text: str
    rule: str = "emcu_self_reference"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"L{self.line}:C{self.column} [{self.rule}] {self.text!r}"


def _is_negated(haystack: str, match_start: int) -> bool:
    """True if the match is preceded by a negator within a short window."""
    window = haystack[max(0, match_start - 45): match_start]
    return bool(_NEGATION.search(window))


def lint_self_reference(text: str) -> list[LintViolation]:
    """Return all *affirmative* 'systematic review' self-references in ``text``.

    Disclaimers and negations ("this is not a systematic review") are allowed
    and do NOT produce violations. An empty list means the text is clean.
    """
    violations: list[LintViolation] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        # Collect every non-negated match span, then merge overlaps so that an
        # affirmative phrase matched by several patterns ("In this systematic
        # review" hits both the 'this …' and 'in this …' rules) counts once.
        spans: list[tuple[int, int]] = []
        for pat in _AFFIRMATIVE_PATTERNS:
            for m in pat.finditer(line):
                if _is_negated(line, m.start()):
                    continue
                spans.append((m.start(), m.end()))
        if not spans:
            continue
        spans.sort()
        merged: list[tuple[int, int]] = [spans[0]]
        for start, end in spans[1:]:
            last_start, last_end = merged[-1]
            if start <= last_end:  # overlapping or adjacent
                merged[-1] = (last_start, max(last_end, end))
            else:
                merged.append((start, end))
        for start, end in merged:
            violations.append(
                LintViolation(line=lineno, column=start + 1, text=line[start:end])
            )
    return violations


def lint_paths(paths) -> dict[str, list[LintViolation]]:
    """Lint a collection of template/markdown files.

    Returns a mapping of ``path -> violations`` containing only files that have
    at least one violation. An empty mapping means the build may proceed.
    """
    from pathlib import Path

    findings: dict[str, list[LintViolation]] = {}
    for p in paths:
        path = Path(p)
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = lint_self_reference(text)
        if hits:
            findings[str(path)] = hits
    return findings


def assert_clean(paths) -> None:
    """Raise ``EMCUSelfReferenceError`` if any file affirmatively self-describes
    as a systematic review. Used as a build gate (WP-8/§1.2)."""
    findings = lint_paths(paths)
    if findings:
        lines = [
            f"  {path}: {v}"
            for path, hits in findings.items()
            for v in hits
        ]
        raise EMCUSelfReferenceError(
            "Templates affirmatively self-describe as a 'systematic review' "
            "(forbidden under EMCU framing, v3.2 §1):\n" + "\n".join(lines)
        )


class EMCUSelfReferenceError(AssertionError):
    """Raised by the build gate when a template claims to be an SR."""
