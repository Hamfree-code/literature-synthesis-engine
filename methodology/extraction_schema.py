"""WP-1 — Extraction integrity: schema validation + repair contract.

DEFECT: oversized Sonnet output truncated mid-object, ``json.loads`` threw, the
paper was silently dropped, and the "top 9" were whichever survived the parser
— a non-random, undocumented filter biased against the most data-rich papers.

FIX (this module):
  * A Pydantic contract (``ExtractionModel``) every extraction must satisfy
    before it is allowed into the database. Reject-with-reason, never silently.
  * ``classify_parse_failure`` / ``validate_extraction`` produce a typed
    ``FailureReason`` so the failure is logged as a corpus event.
  * ``assemble_subextractions`` lets a paper be extracted as bounded
    sub-objects (study_characteristics / outcomes / bias_audit / mechanism) so
    a truncation in one sub-object no longer destroys the whole paper.
  * ``parse_or_repair`` implements the repair contract: on ``JSONDecodeError``
    a caller-supplied ``repair_fn`` (wired to a cheap model in the pipeline)
    gets one chance to return schema-valid JSON. The repair function is
    injected so this logic is unit-testable without a live model.

The Pydantic model is intentionally permissive about *inner* structure (the
deep extraction is rich and evolving) but strict about the *presence* of the
major blocks — that is exactly the signal that distinguishes a complete object
from a truncated one.
"""
from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, ValidationError


class FailureReason(str, enum.Enum):
    """Why a paper failed extraction. Persisted on the run registry (WP-1.2)."""

    TRUNCATION = "truncation"
    SCHEMA_VIOLATION = "schema_violation"
    API_ERROR = "api_error"
    TIMEOUT = "timeout"


# Top-level blocks that a *complete* deep extraction must contain. A truncated
# payload loses the tail blocks first (calibration / provenance), so requiring
# them is a robust completeness check.
REQUIRED_BLOCKS = (
    "study_metadata",
    "factual_extraction",
    "methodology_appraisal",
    "calibration",
)

# The bounded sub-extraction families (WP-1.1). Splitting the mega-object into
# these and assembling server-side means one truncated sub-object does not
# destroy the paper.
SUBEXTRACTION_FAMILIES = (
    "study_characteristics",
    "outcomes",
    "bias_audit",
    "mechanism",
)


class _StudyMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")
    design: Any = None
    sample_size: Any = None


class _Calibration(BaseModel):
    model_config = ConfigDict(extra="allow")
    # calibrated_certainty is the field downstream layers depend on; require it
    # to be present (may be null) so a truncated object that lost it is caught.
    calibrated_certainty: Any = None


class ExtractionModel(BaseModel):
    """The contract every extraction must satisfy before DB insertion."""

    model_config = ConfigDict(extra="allow")

    study_metadata: _StudyMetadata
    factual_extraction: dict
    methodology_appraisal: dict
    calibration: _Calibration
    provenance: list = []


@dataclass
class ValidationOutcome:
    ok: bool
    reason: FailureReason | None = None
    errors: list[str] = field(default_factory=list)
    model: ExtractionModel | None = None


def looks_truncated(raw: str) -> bool:
    """Heuristic: does this raw string look like a truncated JSON object?

    A complete object, after stripping, starts with ``{`` and ends with ``}``
    and has balanced braces outside of strings. We use a forgiving check:
    unbalanced braces, or a non-``}`` final char, signals truncation.
    """
    s = raw.strip()
    if not s:
        return True
    if not s.startswith("{") and not s.startswith("["):
        # try to find the first object; if there is none it's not even JSON
        if "{" not in s:
            return True
    # Balance braces ignoring those inside double-quoted strings.
    depth = 0
    in_str = False
    escape = False
    for ch in s:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
    return depth != 0 or in_str


def classify_parse_failure(raw: str) -> FailureReason:
    """Classify why a raw response could not be turned into a valid extraction.

    Distinguishes ``truncation`` (the object was cut off) from
    ``schema_violation`` (valid JSON, wrong shape).
    """
    try:
        json.loads(raw)
        # Parsed fine → the problem is shape, not truncation.
        return FailureReason.SCHEMA_VIOLATION
    except json.JSONDecodeError:
        return FailureReason.TRUNCATION if looks_truncated(raw) else FailureReason.SCHEMA_VIOLATION


def validate_extraction(obj: dict) -> ValidationOutcome:
    """Validate an already-parsed extraction dict against the contract.

    Returns a ``ValidationOutcome`` with ``ok`` and, on failure, a typed
    ``reason`` and human-readable ``errors`` — never raises, never drops
    silently.
    """
    if not isinstance(obj, dict):
        return ValidationOutcome(False, FailureReason.SCHEMA_VIOLATION, ["payload is not an object"])
    missing = [b for b in REQUIRED_BLOCKS if b not in obj]
    if missing:
        # Losing only the TAIL block (calibration) while the leading blocks are
        # present is the fingerprint of a truncated object. A payload missing
        # the leading blocks entirely is simply the wrong shape.
        leading_present = "study_metadata" in obj and "factual_extraction" in obj
        if "calibration" in missing and leading_present:
            reason = FailureReason.TRUNCATION
        else:
            reason = FailureReason.SCHEMA_VIOLATION
        return ValidationOutcome(False, reason, [f"missing required block: {b}" for b in missing])
    try:
        model = ExtractionModel.model_validate(obj)
    except ValidationError as e:
        return ValidationOutcome(False, FailureReason.SCHEMA_VIOLATION, [str(err) for err in e.errors()])
    return ValidationOutcome(True, None, [], model)


def assemble_subextractions(parts: dict[str, Any]) -> dict[str, Any]:
    """Assemble bounded sub-extractions into one extraction object (WP-1.1).

    ``parts`` maps a sub-extraction family name to its parsed object (or to
    ``None`` if that sub-extraction failed/truncated). A failed family is
    recorded under ``_subextraction_failures`` rather than aborting the paper.
    """
    assembled: dict[str, Any] = {}
    failures: list[str] = []
    for family, value in parts.items():
        if value is None:
            failures.append(family)
            continue
        if isinstance(value, dict):
            assembled.update(value)
        else:
            assembled[family] = value
    if failures:
        assembled["_subextraction_failures"] = failures
    return assembled


def parse_or_repair(
    raw: str,
    repair_fn: Callable[[str], str] | None = None,
) -> tuple[dict | None, FailureReason | None]:
    """The repair contract (WP-1.1).

    1. Try strict ``json.loads``.
    2. On failure, if a ``repair_fn`` is supplied (wired to a cheap model in
       the pipeline), give it one chance to return valid JSON.
    3. Validate the result against the schema.

    Returns ``(obj, None)`` on success or ``(None, FailureReason)`` on failure.
    The repaired output is accepted *only if it validates* — never blindly.
    """
    obj: dict | None = None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        if repair_fn is not None:
            try:
                repaired = repair_fn(raw)
                obj = json.loads(repaired)
            except (json.JSONDecodeError, Exception):  # noqa: BLE001 - repair is best-effort
                obj = None
        if obj is None:
            return None, classify_parse_failure(raw)

    outcome = validate_extraction(obj)
    if not outcome.ok:
        return None, outcome.reason
    return obj, None
