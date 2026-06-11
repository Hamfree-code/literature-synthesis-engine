"""Gemini Batch API wrapper — submit/poll round trip, availability, failure modes.

No network and no `google.genai` import: the wrapper only imports the SDK inside
`_get_client`, which these tests monkeypatch with a fake batches client.
"""

from __future__ import annotations

import pytest

from utils import gemini_client as gc
from utils import resilience


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeBatches:
    def __init__(self, job):
        self._job = job
        self.created: dict | None = None

    def create(self, *, model, src, config=None):
        self.created = {"model": model, "src": src}
        return _Obj(name="batches/abc")

    def get(self, *, name, config=None):
        return self._job


def _item(pid, text=None, error=None):
    resp = _Obj(text=text) if text is not None else None
    return _Obj(metadata={"paper_id": pid}, response=resp, error=error)


def _job(state, items=None):
    return _Obj(state=_Obj(name=state), dest=_Obj(inlined_responses=items or []), error=None)


@pytest.fixture(autouse=True)
def _reset():
    resilience.reset_all()
    gc._client = None
    yield
    resilience.reset_all()
    gc._client = None


def test_available_false_without_key(monkeypatch):
    monkeypatch.setattr(gc.settings, "GEMINI_API_KEY", "", raising=False)
    assert gc.gemini_available() is False


def test_build_inlined_request_shape():
    req = gc.build_inlined_request(
        paper_id="PMC1", system_instruction="SCHEMA", content="body", max_output_tokens=100, temperature=0.3
    )
    assert req["metadata"]["paper_id"] == "PMC1"
    assert req["config"]["response_mime_type"] == "application/json"
    assert req["config"]["temperature"] == 0.3
    assert req["contents"] == "body"


def test_submit_and_poll_roundtrip(monkeypatch):
    fake = _FakeBatches(_job("JOB_STATE_SUCCEEDED", [_item("PMC1", '{"a":1}'), _item("PMC2", '{"b":2}')]))
    monkeypatch.setattr(gc, "_get_client", lambda: _Obj(batches=fake))

    reqs = [
        gc.build_inlined_request(
            paper_id=p, system_instruction="S", content="t", max_output_tokens=100, temperature=0.3
        )
        for p in ("PMC1", "PMC2")
    ]
    name = gc.submit_gemini_batch(reqs, model="gemini-2.5-flash")
    assert name == "batches/abc"
    assert fake.created["model"] == "gemini-2.5-flash"
    assert fake.created["src"][0]["metadata"]["paper_id"] == "PMC1"

    out = gc.poll_gemini_batch(name, interval_sec=0)
    assert {o["paper_id"] for o in out} == {"PMC1", "PMC2"}
    assert {o["paper_id"]: o["text"] for o in out}["PMC1"] == '{"a":1}'
    assert all(o["error"] is None for o in out)


def test_partial_success_is_not_a_failure(monkeypatch):
    job = _job("JOB_STATE_PARTIALLY_SUCCEEDED", [_item("PMC1", '{"a":1}'), _item("PMC2", error="quota")])
    monkeypatch.setattr(gc, "_get_client", lambda: _Obj(batches=_FakeBatches(job)))
    out = gc.poll_gemini_batch("batches/x", interval_sec=0)
    by_pid = {o["paper_id"]: o for o in out}
    assert by_pid["PMC1"]["text"] == '{"a":1}'
    assert by_pid["PMC2"]["text"] is None
    assert "quota" in by_pid["PMC2"]["error"]


def test_failed_job_raises(monkeypatch):
    monkeypatch.setattr(gc, "_get_client", lambda: _Obj(batches=_FakeBatches(_job("JOB_STATE_FAILED"))))
    with pytest.raises(gc.GeminiBatchError):
        gc.poll_gemini_batch("batches/x", interval_sec=0)
