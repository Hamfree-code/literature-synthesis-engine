# 07 — Post-v3.1 Changes (current iteration, grounded)

Three deltas landed after the v3.1 dossier (00–06) was written. All three follow
the project's standing rules: **degrade loudly, never silently**; **never lose a
paper**; **the code is the source of truth**. Each is grounded at `file:symbol`
and covered by tests. Commits: `a74202a`, `3619fb3`, `a3d87dc`.

---

## C1 — Unpaywall: fail-secure DOI lookups + persistent cache (`a74202a`)

**Problem.** The OA-PDF lookup returned `None` both when Unpaywall said "no OA
exists" and when the Unpaywall API was *down*. The circuit breaker never saw the
outage, so a service degradation silently shrank full-text coverage — exactly the
class of silent failure the engine exists to prevent.

**Fix.** `pipeline/sources/unpaywall.py::_lookup_oa_pdf_url` now returns
`(url, definitive)`. `definitive=True` means Unpaywall answered authoritatively
(an OA url, or a confirmed "no OA" / unknown-DOI 404) and is safe to cache;
`definitive=False` means a 5xx/transport error. `fetch_fulltext_via_unpaywall`
treats a non-definitive answer as a breaker failure (`cb.record_failure()`) and
returns without caching — so an outage shows up in `degraded_services`, never as
a legitimate "no OA".

**Persistence.** DOI→URL lookups now survive across runs in
`data/raw/unpaywall_cache.json` via the existing `JsonFileCache` (same pattern as
the retraction and UMLS caches). Only **definitive** answers are cached.

**Deliberate non-change.** OpenAlex search results are *not* cached persistently:
literature freshness is part of the evidence contract (new papers must be able to
appear between runs), and the corpus is already persisted in `papers.jsonl`.

**Evidence.** `tests/unit/test_sources.py`:
`test_unpaywall_api_outage_feeds_breaker_and_is_not_cached`,
`test_unpaywall_definitive_no_oa_is_cached_and_skips_network`,
`test_unpaywall_unknown_doi_404_is_definitive_miss`,
`test_unpaywall_url_cache_persists_to_disk`.

---

## C2 — Opus arbiter for the two-step extraction (`3619fb3`)

**Rationale.** The arbiter reconciles the two reviewers; it should be the
strongest neutral reasoner. Moving it from a third Sonnet pass to **Opus** is a
clean quality upgrade that stays inside the Anthropic Batch stack (no new
provider). Reviewer A and (Anthropic) Reviewer B stay on Sonnet so their
**temperature** diversity (0.1 / 0.3) keeps working — Opus 4.7/4.8 reject
sampling params, so they could not host that lever.

**Implementation.** `config/settings.py::ANTHROPIC_ARBITER_MODEL`
(default `claude-opus-4-8`, set to `claude-sonnet-4-6` to restore v3.1 behaviour).
`phase3_extract.py::build_arbiter_request` reads it and **only sends
`temperature` when the model accepts it** — gated by `_accepts_temperature`,
which excludes `opus-4-7`, `opus-4-8`, `fable-5`, `mythos-5`. This is the
concrete gotcha that would otherwise 400 on the first Opus run: a model swap here
is *not* just a string change.

**Cost.** The arbiter is 1 of 3 deep calls per paper; Opus ≈ 1.67× Sonnet at
batch rates, so total deep-extraction cost rises ≈ 20% — modest, for stronger
adjudication of contested fields (GRADE rationale, mechanism classification)
where the literal-quote guardrail does not anchor the output.

**Evidence.** `tests/integration/test_phase3_mocked.py`:
`test_arbiter_uses_configured_model_and_drops_temperature_for_opus`,
`test_arbiter_keeps_temperature_for_sonnet`,
`test_reviewers_stay_on_sonnet_with_temperature_diversity`.

---

## C3 — Cross-model Reviewer B on Gemini Flash (`a3d87dc`)

**Rationale.** The v3.1 "diversity" between reviewers was *temperature only* —
two Sonnet calls share the same training and therefore the same systematic blind
spots. Genuine **cross-model** diversity (a Sonnet reviewer and a Gemini
reviewer) decorrelates extraction errors, giving the Opus arbiter independent
evidence. This is the reviewer layer, which is where extraction errors originate
— the highest-value place to diversify.

**Where it lives.**
- `utils/gemini_client.py` mirrors only the slice of `utils/claude_client` the
  pipeline needs: `build_inlined_request`, `submit_gemini_batch`,
  `poll_gemini_batch` over Google's Batch API (`google-genai`, 50% discount, up
  to 24h, `JOB_STATE_*` enum). `google.genai` is imported **only inside
  `_get_client`**, so the engine imports and runs unchanged with Gemini off, and
  every other function is pure dict manipulation (unit-testable with a fake
  client, no SDK or network).
- `phase3_extract.py::_extract_b_via_gemini` runs Reviewer B and returns the same
  `(parsed_by_pid, attempts, failures)` triple as the Anthropic path, so
  `_run_arbiter_pass` is provider-agnostic. It is selected in `_run_arbiter_pass`
  when `REVIEWER_B_PROVIDER=gemini` **and** `gemini_available()`.

**No brittle schema translation.** The extraction schema uses union types
(`["boolean","string"]`, `[...,"null"]`) that Gemini's OpenAPI-subset
`response_schema` can't express. So Reviewer B asks for
`response_mime_type="application/json"` plus the schema-as-instructions prompt
(`_gemini_reviewer_system_instruction`) and normalises the result through the
**same Haiku repair pass** the Anthropic reviewers use. Reviewer B's output is
therefore shaped identically to a Sonnet extraction.

**Fail-secure.** No key or missing SDK → `gemini_available()` is False → silent
fallback to the Anthropic reviewer. A degraded Gemini batch trips the `gemini`
circuit breaker (`failure_threshold=1`, surfaced in `degraded_services`) and
records the affected papers as failures — which then **proceed on Reviewer A
alone** through the existing `arbiter_skipped` path, so no paper is lost.
Reconciled records carry `reviewer_b_provider` for traceability.

**Evidence.** `tests/unit/test_gemini_client.py` (submit/poll round-trip,
partial-success, terminal-failure raises, availability gate);
`tests/integration/test_phase3_mocked.py::test_arbiter_with_gemini_reviewer_b`,
`::test_gemini_batch_failure_is_recorded_not_lost`.

**What is NOT yet done (be precise when scoring).** No live Gemini batch has run
— the SDK surface was verified by introspection, not by a real job. The
cross-model *quality* gain is a **design hypothesis**, not a measurement. The
infrastructure for measuring it already exists: every reconciled record stores
`reviewer_a_raw` and `reviewer_b_raw`, so an A/B of Sonnet-B vs Gemini-B over a
30–50 paper sample (does the Opus arbiter reconcile differently?) is
straightforward once a key is available. Recommended evaluation step.

---

## How the three fit the existing ethos
| Change | Rule it serves |
|---|---|
| Unpaywall fail-secure | "degrade loudly, never silently" (outage ≠ absence of evidence) |
| Opus arbiter | strongest neutral adjudicator; correctness over cost is the user's call |
| Gemini Reviewer B | decorrelate errors; fail-secure; never lose a paper; the code is the source of truth |
