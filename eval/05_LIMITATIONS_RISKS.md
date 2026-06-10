# 05 — Limitations, Risks, and What Is NOT Done

Stated plainly so an evaluator does not have to reverse-engineer the gaps. The
project's ethos is to declare these, not hide them.

## Unverified (no live run yet)
- **No end-to-end live run** has executed in this environment (network allowlist
  blocks NCBI/OpenAlex/Crossref/UMLS; only Anthropic is reachable; plus API
  budget). All validation is unit/integration with mocked providers.
- Therefore headline numbers in v3.0 docs (e.g. "~94% deep success",
  "% CUIs verified") are **targets/historical, not re-measured under v3.1**.
  Judge engineering + methodology; treat yield/coverage as to-be-confirmed.

## Methodological limits (declared in-report)
- **Not a registered systematic review** — no protocol pre-registration, no
  human dual screening. PRISMA-conformant *reporting* only.
- **OR/RR/HR → r-equivalent pooling** is an approximation (Pearson-r variance on
  converted effects); the report says so. Proper would be metric-native log-OR.
- **Factor grouping is by LLM free-text factor name** — risk of under-pooling
  (same construct named differently) or over-pooling (different constructs same
  name). UMLS canonicalisation exists but is NOT wired into pooling yet.
- **Trim-and-fill is in-house** (crude: above/below median count), test-validated.
- **QUADAS/GRADE** are double-LLM + arbiter, not trained human reviewers.

## Engineering limits / known gaps
- **Supabase is a hard dependency for Phase 4** (upserts not wrapped). A full run
  needs Supabase configured + the 4 migrations applied. No local-only mode.
- **Circuit breakers are per-run, in-process** (the worker is single-process).
  Not distributed; state resets each run. Caches (`JsonFileCache`) persist across
  runs but are single-file JSON, not concurrent-safe.
- **PDF parsing** uses `sort=True` reading order (mitigates two-column
  interleaving) but complex tables/figures/multi-column-with-callouts can still
  degrade flat-PDF extraction; flagged `chunking_mode=flat_pdf` for traceability.
- **Cost**: upfront estimate is a static floor (`max_deep × 0.45`); real cost is
  measured in the manifest. Free APIs (UMLS/OpenAlex/Unpaywall) add $0 API cost.
- **Windows-first `.exe`**; POSIX runtime degrades gracefully but the shipped
  artifact is Windows.

## Security / distribution
- `bundled_credentials.py` (gitignored) can bake keys into the `.exe`. For
  commercial distribution prefer BYO-key (`.env`) or a proxy; see `PUBLISHING.md`.
- Service-role Supabase key + Anthropic key are sensitive; rotate after shared runs.

## Failure-mode summary (does an outage corrupt a run?)
| Service down | Behaviour | Safe? |
|---|---|---|
| Anthropic | retries; phase fails loudly if persistent | yes (no silent loss) |
| NCBI/PMC | tenacity retry + resume from `papers.jsonl` | yes |
| Crossref (retraction) | 3-state; run marked INCOMPLETE | yes (fail-secure) |
| UMLS | breaker + cache; `cui_verified=false` | yes (degrade) |
| OpenAlex | breaker; fewer sources; surfaced | yes (degrade) |
| Unpaywall | breaker; abstract fallback; surfaced | yes (degrade) |
| Supabase | Phase 4 errors (hard dep) | **NO — run fails** |

The one non-graceful dependency is Supabase. Everything else degrades loudly.
