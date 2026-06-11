# 08 — Verification Log (reproducible green state)

Captured this iteration in the evaluation environment (Python 3.12 venv, all
external APIs mocked). An evaluator can re-run each command and expect the same
shape of output. This is the observed evidence behind the "green state" claim —
not a promise, a transcript.

## Environment
- Python: 3.12 (`requires-python = ">=3.12,<3.13"`, `pyproject.toml`).
- Install: `uv venv -p 3.12 .venv && uv pip install -e ".[dev]"`.
- Optional Gemini reviewer: `uv pip install -e ".[gemini]"` (adds `google-genai`).

## Test suite
```
$ .venv/bin/python -m pytest -q
117 passed, 1 deselected, 2 warnings in 3.99s
```
- 117 non-live tests pass; 1 live test deselected by default
  (`addopts = -m 'not live'`, `pyproject.toml`).
- Runtime < 15s — no network, no real API calls (respx + in-process fakes).

## Coverage (utils + the pure-stats phase)
```
$ .venv/bin/python -m pytest --cov=utils --cov=pipeline.phase5_analyze
TOTAL    1820   488   73%
```
- 73% line coverage on the modules that carry the deterministic/statistical and
  resilience logic (the parts where correctness is verifiable without a model).

## Lint + format
```
$ .venv/bin/python -m ruff check .      → All checks passed!
$ .venv/bin/python -m ruff format --check .   → 59 files already formatted
```

## Lazy-import safety (Gemini off by default)
Confirms the Gemini integration adds no import-time cost or dependency when
disabled — the engine runs unchanged without `google-genai`:
```
$ python -c "import pipeline.phase3_extract, utils.gemini_client; \
             import sys; print('google.genai' in sys.modules)"
google.genai loaded after importing the pipeline?: False
gemini_available() without key: False        # → silent fallback to Anthropic reviewer
```

## New tests added this iteration (10)
- `tests/unit/test_gemini_client.py` (5): availability gate, request shape,
  submit/poll round-trip, partial-success handling, terminal-failure raises.
- `tests/unit/test_sources.py` (+4): Unpaywall outage feeds breaker & not cached;
  definitive "no OA" cached & skips network; unknown-DOI 404 is a definitive
  miss; URL cache persists to disk.
- `tests/integration/test_phase3_mocked.py` (+5): arbiter uses configured model &
  drops temperature for Opus; keeps it for Sonnet; reviewers stay on Sonnet with
  temperature diversity; arbiter with a Gemini Reviewer B; Gemini batch failure is
  recorded, not lost.

## Commits under review (this session)
```
a3d87dc  phase3: cross-model Reviewer B via Gemini Flash Batch API
3619fb3  phase3: Opus arbiter for two-step deep extraction
a74202a  unpaywall: fail-secure DOI lookups + persistent JsonFileCache
```
Branch: `claude/hopeful-gates-t7rm1n`. Each commit ships its code, tests, and
CHANGELOG entry together; CI (`.github/workflows/ci.yml`) runs ruff + pytest +
coverage on push.

## Not captured here (and why)
- **No live end-to-end run** (network allowlist blocks NCBI/OpenAlex/Crossref/
  UMLS; only Anthropic + Google reachable; plus API budget). See `05`.
- **No live Gemini batch** (needs `GEMINI_API_KEY` + cost). The SDK surface was
  verified by introspecting the installed `google-genai==2.8.0` (signatures,
  `JobState` enum, `InlinedRequest.metadata`, `response.text`), not by a job. See
  `07` §C3.
