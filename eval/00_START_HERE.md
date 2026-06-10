# Evaluation Dossier — Literature Synthesis Engine v3.1

> Purpose: let an LLM evaluator understand and judge this project **without
> crawling the repo**. Self-contained. Read in order; each doc is standalone but
> non-redundant (facts live in one place and are cross-referenced).

## What this is (one sentence)
A disease-agnostic pipeline that ingests open-access biomedical literature,
performs dual-reviewer + arbiter LLM extraction with literal-quote provenance,
runs reference meta-analysis, and emits auditable, PRISMA/GRADE-formatted
consulting deliverables.

## Reading order (token budget ~ in parens)
1. `01_OVERVIEW.md` — problem, value, users, status (~900t)
2. `02_ARCHITECTURE.md` — phases, data flow, process model, file map (~1500t)
3. `03_METHODOLOGY.md` — scientific/statistical methods, standards, labeling (~1400t)
4. `04_V31_CHANGES.md` — what v3.1 added (P0–P7 + hardening), grounded (~1500t)
5. `05_LIMITATIONS_RISKS.md` — honest gaps, failure modes, what is NOT done (~1200t)
6. `06_EVAL_RUBRIC.md` — questions to score + where to verify each claim (~1200t)

## Hard facts (verifiable)
- Language: Python 3.12 (pinned). Core LOC ≈ 6,900 across `pipeline/`, `utils/`, `config/`.
- Tests: **99 non-live + 1 live** (`pytest -m "not live"`), run < 25s, mocked APIs (respx).
  Coverage on `utils/` + `pipeline/phase5_analyze.py` ≈ 72%.
- CI: GitHub Actions — ruff lint + format + pytest + coverage (`.github/workflows/ci.yml`).
- Models: Claude Haiku (triage), Claude Sonnet (extraction/arbiter/synthesis). Anthropic Batch API.
- External services: PubMed/PMC (NCBI), OpenAlex, Unpaywall, Crossref, UMLS REST, Supabase (pgvector).
- License: MIT. Brand: "Hams & Co. Research Division".

## How to evaluate fairly (important caveats)
- **No live end-to-end run has been executed in this environment** (network
  allowlist + API budget). All validation is unit/integration with mocked
  providers. Judge the *engineering and methodology*, and treat live yield/%
  claims as targets, not measurements. See `05` §"Unverified".
- The code is the source of truth. Where a claim cites `path:symbol`, it is
  checkable in the repo. If repo and dossier disagree, the repo wins.
- This is **PRISMA-conformant reporting of an AI-assisted evidence map**, NOT a
  registered systematic review. It says so in its own outputs. Do not grade it
  as if it claimed to be a Cochrane review.

## Repo entry points (if the evaluator does open the repo)
- Orchestration: `pipeline/runner.py::execute_industrial_pipeline`
- Server/UI: `app_server.py` (Flask + SSE)
- Phases: `pipeline/phase{1,3,4,5,6}_*.py` (phase 2 removed in v3.1)
- Plan & rationale: `docs/V31_MASTER_PLAN.md`; deviations: `docs/SPEC_V31_DISCREPANCIES.md`
