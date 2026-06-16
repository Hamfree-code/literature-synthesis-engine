"""Tests for the pre-run guardrails: cost estimation/gating, config preflight,
the persistent batch registry, the Supabase-enabled toggle, and actual-spend
counting. All offline — no API calls."""
from __future__ import annotations

import json

import pytest

from config.settings import settings
from utils import preflight as pf


# ── Cost estimation + budget gate ────────────────────────────────────────

def test_estimate_cost_arbiter_dearer_than_single():
    single = pf.estimate_cost(100, 20, arbiter_enabled=False)
    arbiter = pf.estimate_cost(100, 20, arbiter_enabled=True)
    assert arbiter > single
    # arbiter: 100*0.002 + 20*0.70 + 0.50 = 14.70
    assert arbiter == pytest.approx(14.70)
    # single: 100*0.002 + 20*0.15 + 0.50 = 3.70
    assert single == pytest.approx(3.70)


def test_arbiter_cost_reflects_three_models():
    # Sonnet + Gemini Pro + Opus must cost materially more than Sonnet alone.
    assert pf.COST_PER_DEEP_ARBITER > pf.COST_PER_DEEP_SINGLE * 2


def test_budget_exceeded_respects_cap():
    assert pf.budget_exceeded(100.0, 50.0) is True
    assert pf.budget_exceeded(10.0, 50.0) is False
    assert pf.budget_exceeded(9999.0, 0.0) is False     # cap 0 disables the gate


# ── Config + prompt-file checks ──────────────────────────────────────────

def test_check_prompt_files_all_present():
    assert pf.check_prompt_files() == []


def test_check_config_flags_empty_anthropic_key(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    errors, _ = pf.check_config()
    assert any("ANTHROPIC_API_KEY" in e for e in errors)


def test_check_config_flags_empty_gemini_key(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "present")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    errors, _ = pf.check_config()
    assert any("GEMINI_API_KEY" in e for e in errors)


def test_check_config_warns_when_supabase_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "present")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "present")
    monkeypatch.setattr(settings, "SUPABASE_URL", "")
    monkeypatch.setattr(settings, "SUPABASE_KEY", "")
    errors, warnings = pf.check_config()
    assert errors == []
    assert any("Supabase" in w for w in warnings)


# ── run_preflight aggregation ────────────────────────────────────────────

def test_run_preflight_blocks_when_over_budget(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "present")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "present")
    monkeypatch.setattr(settings, "ARBITER_ENABLED", True)
    monkeypatch.setattr(settings, "MAX_SPEND_USD", 5.0)
    report = pf.run_preflight(max_papers=1000, max_deep=500)  # huge
    assert report.ok is False
    assert any("exceeds MAX_SPEND_USD" in e for e in report.errors)


def test_run_preflight_passes_small_run_no_cap(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "present")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "present")
    monkeypatch.setattr(settings, "MAX_SPEND_USD", 0.0)   # disabled
    report = pf.run_preflight(max_papers=50, max_deep=10)
    assert report.ok is True
    assert report.estimate_usd > 0


# ── Supabase-enabled property ────────────────────────────────────────────

def test_supabase_enabled_requires_both_creds(monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(settings, "SUPABASE_KEY", "")
    assert settings.supabase_enabled is False
    monkeypatch.setattr(settings, "SUPABASE_KEY", "secret")
    assert settings.supabase_enabled is True


# ── Persistent batch registry ────────────────────────────────────────────

def test_batch_registry_remember_and_forget(monkeypatch, tmp_path):
    from utils import claude_client as cc
    reg_file = tmp_path / "batches.json"
    monkeypatch.setattr(cc, "_registry_path", lambda: reg_file)

    assert cc.remembered_batch("triage_0") is None

    cc._save_registry({"triage_0": "msgbatch_abc"})
    assert cc.remembered_batch("triage_0") == "msgbatch_abc"

    cc.forget_batch("triage_0")
    assert cc.remembered_batch("triage_0") is None


def test_batch_registry_survives_corrupt_file(monkeypatch, tmp_path):
    from utils import claude_client as cc
    reg_file = tmp_path / "batches.json"
    reg_file.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(cc, "_registry_path", lambda: reg_file)
    assert cc._load_registry() == {}              # degrades gracefully
    assert cc.remembered_batch("anything") is None


# ── Actual-spend line counting ───────────────────────────────────────────

def test_count_jsonl_counts_nonblank_lines(monkeypatch, tmp_path):
    import app_paths
    from pipeline import runner

    f = tmp_path / "triage_results.jsonl"
    f.write_text(
        json.dumps({"paper_id": "p1"}) + "\n\n" + json.dumps({"paper_id": "p2"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(app_paths, "app_data", lambda rel: f)
    assert runner._count_jsonl("data/filtered/triage_results.jsonl") == 2


def test_count_jsonl_missing_file_is_zero(monkeypatch, tmp_path):
    import app_paths
    from pipeline import runner
    monkeypatch.setattr(app_paths, "app_data", lambda rel: tmp_path / "absent.jsonl")
    assert runner._count_jsonl("data/filtered/absent.jsonl") == 0
