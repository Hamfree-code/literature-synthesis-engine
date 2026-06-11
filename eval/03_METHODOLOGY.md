# 03 — Research & Statistical Methodology

## Mapping to recognised standards
| Pipeline act | Standard emulated | Implementation |
|---|---|---|
| Search strategy | PRISMA-S | Multi-source query frozen in manifest; retraction exclusion |
| Screening | PRISMA screening box | Haiku triage (structured, tool-use) |
| Dual extraction + adjudication | Dual-reviewer SR w/ arbitration | A=Sonnet(0.1) + B=Sonnet(0.3) **or Gemini Flash** → **Opus** arbiter |
| Quality appraisal | NOS, GRADE, QUADAS-2 | Per-paper in deep extraction schema |
| Effect sizes | Cohen's r classification | OR/RR/HR → r-equivalent + magnitude |
| Pooling | DerSimonian–Laird RE | PyMARE; legacy numpy fallback (diff < 1%) |
| Heterogeneity | I², τ², Q | Closed-form; forest plot when I² ≥ 90% |
| Publication bias | Egger's regression, trim-and-fill | statsmodels OLS; in-house trim-and-fill |
| Certainty | GRADE SoF | Auto-downgrade for inconsistency/imprecision/bias |
| Reporting | PRISMA 2020 flow | Auto SVG with per-source counts |

## Two independent epistemic axes (important for evaluators)
1. **Quantitative pooling** (`meta_analyze_by_factor`): groups effect sizes by
   factor and pools. Gated by `MIN_STUDIES_POOLING` (default **3**) — below it,
   individual effects are surfaced, NOT a fragile pooled estimate. τ²/Q/I² need
   ≥2 df; pooling 2 studies is not reported.
2. **Calibrated certainty** (`propagate_uncertainty`): per-symptom consensus tier
   (established/probable/possible/speculative/contradicted) from per-paper
   calibration + replication count. This is where thin corpora land — the system
   degrades to "evidence map with individual effects", not invented statistics.

## Effect-size handling (and its honest limit)
OR/RR/HR are converted to a Pearson r-equivalent for cross-metric comparison;
variance is approximated as `(1−r²)²/(n−1)`. This is documented in-report as an
approximation: the pooled r is a **signal indicator**, not a substitute for a
metric-native (log-OR) meta-analysis. Effect sizes require n ≥ 5 to enter pooling.

## Provenance discipline
Prompt forces ≥ 5 literal quotes/paper (10–15 if full text), `null` rather than
guess when confidence < 0.70, `estimated_from_figure` and
`abstract_vs_results_conflict` flags. Provenance is stored per paper and linked
from every `[LLM]`/`[CONSENSUS]` claim.

## Validation against humans
`utils/validation_engine.py` computes Cohen's Kappa (Landis & Koch bands) for
categorical fields, RMSE/Pearson for continuous, per variable. Requires human
ratings in `human_ratings` (UI form provided). Kappa is shown when ratings
exist; it is honestly absent otherwise (not faked).

## What is deterministic vs inferred
`[CALC]` values (pooling, I², Egger p, Kappa, hashes) are reproducible from the
same inputs. `[LLM]`/`[CONSENSUS]` values are model outputs — flagged, quoted,
and (for CUIs) optionally `[VERIFIED]` against UMLS REST.

## Methodological caveats the system declares about itself
- Not a registered SR (no pre-registration, no human dual screening).
- QUADAS/GRADE are double-LLM-scored + arbiter, not trained human reviewers.
- Open-access coverage bias reduced (OpenAlex/Unpaywall) but not eliminated.
- Trim-and-fill is an in-house implementation, validated by test, declared as such.
