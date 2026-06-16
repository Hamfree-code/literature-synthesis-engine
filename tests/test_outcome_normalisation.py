"""WP-5/6 — outcome normalisation.

Acceptance (spec §6):
  * The three cognitive labels collapse into one cognitive_function evidence
    body for the Long COVID run.
  * Unmapped outcome labels appear in a normalisation_review log, never
    discarded.
"""
from __future__ import annotations

from methodology import outcome_dictionary as od


def test_dictionary_loads():
    d = od.load_dictionary("long covid")
    assert d.condition == "long_covid"
    assert d.is_canonical("cognitive_function")
    assert d.patient_important("fatigue")


def test_three_cognitive_labels_collapse():
    d = od.load_dictionary("long_covid")
    for label in ("brain fog", "cognitive dysfunction", "cognitive impairment"):
        assert d.normalize(label) == "cognitive_function"


def test_normalize_batch_groups_by_canonical():
    d = od.load_dictionary("long_covid")
    labels = ["Brain fog", "cognitive dysfunction", "Cognitive impairment", "fatigue", "exhaustion"]
    res = od.normalize_outcomes(labels, d)
    # three cognitive labels → one canonical group
    assert sorted(res.by_canonical["cognitive_function"]) == [
        "Brain fog", "Cognitive impairment", "cognitive dysfunction"
    ]
    assert set(res.by_canonical["fatigue"]) == {"fatigue", "exhaustion"}
    assert res.unmapped == []


def test_unmapped_labels_logged_not_dropped():
    d = od.load_dictionary("long_covid")
    labels = ["fatigue", "left big toe tingling", "brain fog", "quantum derealization"]
    res = od.normalize_outcomes(labels, d)
    assert "left big toe tingling" in res.unmapped
    assert "quantum derealization" in res.unmapped
    # mapped ones still mapped
    assert res.mapping["fatigue"] == "fatigue"
    assert res.mapping["brain fog"] == "cognitive_function"
    # nothing silently lost: every input is either mapped or in the review log
    assert len(res.mapping) + len(res.unmapped) == len(labels)


def test_canonical_id_normalises_to_itself():
    d = od.load_dictionary("long_covid")
    assert d.normalize("cognitive_function") == "cognitive_function"
    assert d.normalize("Cognitive function") == "cognitive_function"  # by label


def test_case_and_punctuation_insensitive():
    d = od.load_dictionary("long_covid")
    assert d.normalize("  Post-Exertional Malaise. ") == "post_exertional_malaise"
    assert d.normalize("PEM") == "post_exertional_malaise"


def test_unknown_condition_falls_back():
    d = od.load_dictionary("some_unseeded_condition")
    # falls back to long_covid dictionary rather than crashing
    assert d.is_canonical("fatigue")
