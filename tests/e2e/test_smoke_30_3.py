"""E2E smoke — 30 papers / 3 deep against the real APIs.

Marked ``live``: excluded from CI and the default run. Execute manually with real
credentials in the environment:

    uv run pytest -m live tests/e2e/test_smoke_30_3.py -s

It exercises the full pipeline (ingest → triage → enrich → deep → store →
analyze → report) and asserts the enterprise artefacts were produced.
"""

from __future__ import annotations

import pytest


@pytest.mark.live
def test_smoke_30_3(tmp_path, monkeypatch):
    import asyncio

    from pipeline import phase1_ingest, phase3_extract, phase4_store, phase5_analyze, phase6_report

    monkeypatch.setattr(phase1_ingest, "app_data", lambda rel: tmp_path / rel, raising=False)

    asyncio.run(phase1_ingest.run(max_papers=30, topic="narcolepsy"))
    phase3_extract.run_triage(max_papers=30)
    ids = phase3_extract.select_for_deep_analysis(top_n=3)
    assert ids, "triage produced no eligible papers"
    asyncio.run(phase1_ingest.enrich_with_fulltext(ids))
    phase3_extract.run_deep(ids)
    phase4_store.run()
    phase5_analyze.run()
    phase6_report.run()

    reports = list((tmp_path / "reports").glob("research_*"))
    assert reports, "no report artefacts generated"
    assert list((tmp_path / "reports").glob("supplement_*.zip")), "no supplement ZIP"
