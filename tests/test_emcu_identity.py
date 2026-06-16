"""WP-0 / §1 — EMCU product identity + self-reference lint.

Acceptance (spec §1): grep of all rendered templates returns no self-referential
"systematic review" string; every report contains the EMCU disclaimer block.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from methodology import emcu

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "templates"


def test_affirmative_self_reference_is_flagged():
    text = "In this systematic review we screened 21 papers."
    violations = emcu.lint_self_reference(text)
    assert len(violations) == 1
    assert violations[0].rule == "emcu_self_reference"


def test_negated_disclaimer_is_allowed():
    # The EMCU disclaimer itself contains "systematic review" in negated form.
    assert emcu.lint_self_reference(emcu.EMCU_DISCLAIMER) == []
    assert emcu.lint_self_reference("This is not a systematic review.") == []
    assert emcu.lint_self_reference(
        "It is a scoping instrument, not a substitute for an intervention systematic review."
    ) == []


def test_multiple_affirmative_forms_flagged():
    text = (
        "Our systematic review found X.\n"
        "We conducted a systematic review of the corpus.\n"
        "This report is a systematic review of Long COVID.\n"
    )
    violations = emcu.lint_self_reference(text)
    assert len(violations) == 3
    # line numbers are 1-indexed and distinct
    assert sorted(v.line for v in violations) == [1, 2, 3]


def test_assert_clean_raises_on_dirty_text(tmp_path):
    bad = tmp_path / "bad.md.j2"
    bad.write_text("In this systematic review we did things.", encoding="utf-8")
    with pytest.raises(emcu.EMCUSelfReferenceError):
        emcu.assert_clean([bad])


def test_assert_clean_passes_on_disclaimer(tmp_path):
    good = tmp_path / "good.md.j2"
    good.write_text(emcu.EMCU_DISCLAIMER, encoding="utf-8")
    emcu.assert_clean([good])  # must not raise


def test_disclaimer_is_nonempty_and_mentions_evidence_map():
    assert "evidence map" in emcu.EMCU_DISCLAIMER.lower()
    assert "not a systematic review" in emcu.EMCU_DISCLAIMER.lower()


def test_shipped_templates_have_no_affirmative_self_reference():
    """The real templates must pass the build gate (acceptance §1)."""
    template_files = list(TEMPLATES.glob("*.j2"))
    assert template_files, "expected at least one Jinja template"
    emcu.assert_clean(template_files)
