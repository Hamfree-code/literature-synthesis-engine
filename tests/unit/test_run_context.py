"""P0 — run context: topic casing, slug, and topic-change auto-wipe."""

from __future__ import annotations


import pytest


@pytest.fixture
def rc(tmp_path, monkeypatch):
    import utils.run_context as run_context

    ctx_path = tmp_path / "run_meta.json"
    monkeypatch.setattr(run_context, "CONTEXT_PATH", ctx_path, raising=False)
    return run_context


def test_topic_title_known_acronyms(rc):
    rc.save_run_context("long covid")
    assert rc.topic_title() == "Long COVID"
    rc.save_run_context("pasc")
    assert rc.topic_title() == "PASC"


def test_topic_title_generic(rc):
    rc.save_run_context("fibromyalgia")
    assert rc.topic_title() == "Fibromyalgia"


def test_topic_slug_filesystem_safe(rc):
    rc.save_run_context("Long COVID / PASC")
    slug = rc.topic_slug()
    assert "/" not in slug and " " not in slug
    assert slug == "long_covid___pasc"


def test_same_topic_does_not_wipe(rc):
    rc.save_run_context("fibromyalgia")
    wiped, prev = rc.clear_stale_state_if_topic_changed("fibromyalgia")
    assert wiped is False


def test_topic_change_reports_previous(rc):
    rc.save_run_context("fibromyalgia")
    wiped, prev = rc.clear_stale_state_if_topic_changed("narcolepsy")
    # wiped depends on data dirs existing; previous topic is always reported
    assert prev == "fibromyalgia"
