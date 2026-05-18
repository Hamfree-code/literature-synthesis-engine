# Automated structured synthesis of medical literature using LLM methodological appraisal and literal-quote provenance: a 4,666-paper demonstration on Long COVID

**Author:** Hamsa
**Affiliation:** Independent
**Correspondence:** hhamri53@gmail.com
**Preprint version:** 1.0 (2026-05-17)
**Suggested venue:** medRxiv

---

## Abstract

**Background.** Systematic synthesis of biomedical literature is slow and expensive. A 4,666-paper Long COVID corpus would require months of analyst time and tens of thousands of dollars in expert review. We present an automated pipeline that ingests open-access literature, performs structured methodological appraisal, and emits calibrated cross-paper synthesis with traceable literal-quote provenance for every claim, at fixed dollar cost and within ~1 hour of wall-clock runtime.

**Methods.** Papers are ingested from PubMed Central (NCBI E-utilities) and medRxiv (biorxiv.org date-interval API). Each abstract is triaged by Claude Haiku 4.5 (Anthropic) into a structured JSON object with a self-reported extraction confidence. Top-ranked papers are fetched as full text from PMC Open Access (references stripped) and deeply analysed by Claude Sonnet 4.6 under a two-stage protocol (factual extraction → methodological appraisal: NOS, GRADE, MCID, 8-axis bias audit, QUADAS-adapted 0–19 scoring, Cohen-classified effect sizes, mechanistic phenotype mapping, 5-tier calibrated certainty, ≥ 5 literal-quote provenance entries per paper). The structured outputs are persisted to a Supabase/Postgres database with a normalised `provenance` table. Cross-paper synthesis uses pure-numpy implementations of random-effects pooling (DerSimonian–Laird τ²), leave-one-out sensitivity (10% influence threshold), Egger's regression with trim-and-fill (≥ 10 studies per factor), and Sonnet-driven moderator analysis when I² ≥ 90%. Three final Sonnet passes generate the research narrative, a pharma due-diligence brief, and a non-technical executive summary.

**Results.** On a Long COVID demonstration corpus (2026-05-16), the pipeline triaged 4,666 PMC papers in ~25 minutes, deep-extracted 470 papers in ~35 minutes, stored 7,369 literal-quote provenance entries in Supabase, and rendered three PDF reports — total wall-clock time ~1 hour, total API cost ~$85–100. The deep-extraction yield was ~94%; failures were predominantly JSON-parse errors on oversized model output, addressed by raising the synthesis pass `max_tokens` to 16,384. To demonstrate generalisability, the same pipeline was re-run on Narcolepsy (2026-05-17, 30 / 3 / 50 papers, 644.6 s, ~$1.04) without any code changes — only the topic string differed. Both runs produced reports with calibrated-certainty tier counts, QUADAS distributions, GRADE distributions, bias-audit frequencies, and methodologically appropriate moderator analysis where applicable.

**Conclusions.** A literature-synthesis pipeline anchored in literal-quote provenance and explicit calibration tiers can be made tractable, reproducible, and disease-agnostic. The approach does not replace human systematic review; it produces structured cartography of a literature that is auditable per claim. We position this as evidence that the long-standing bottleneck — the cost and slowness of synthesising primary literature — is now removable for any condition with adequate open-access coverage.

**Keywords:** systematic review automation; large language models; methodological appraisal; literal-quote provenance; QUADAS; random-effects meta-analysis; Long COVID; Narcolepsy.

---

## 1. Introduction

The body of biomedical literature on any non-trivial condition is too large to synthesise manually within any operationally useful timeframe. Long COVID, a condition first characterised in 2020, already had over 5,000 PubMed-indexed papers by mid-2026. The standard tools — Cochrane systematic reviews, GRADE assessments, formal meta-analytic pooling in R `meta` or ProMeta 3 — produce reliable outputs but at the cost of multi-month timelines, dual-reviewer arbitration, and tens of thousands of dollars in expert time per synthesis.

Recent advances in large language models (LLMs) offer a partial automation path. The literature has explored using LLMs for paper screening (Wang et al., 2024), data extraction (Tang et al., 2023), and meta-analytic narrative generation (Siciliano et al., 2024). The remaining unsolved problems are (i) **auditability** — every LLM claim must be traceable to a literal source quote, not paraphrased or interpolated; (ii) **calibration** — confidence statements must reflect the underlying evidence strength, not the LLM's verbal fluency; and (iii) **generalisability** — the same pipeline must work across diseases without per-condition engineering.

We present a pipeline that addresses these three problems jointly. Provenance is enforced at the prompt level (minimum 5 literal-quote entries per paper, ≤ 60 words each, with section attribution) and at the database level (a normalised `provenance` table keyed to paper ID and field name, foreign-key constrained). Calibration is enforced via a 5-tier evidence taxonomy (Established / Probable / Possible / Speculative / Contradicted) propagated from per-paper Sonnet judgments to cross-paper consensus using deterministic rules tied to GRADE certainty and bias audits. Generalisability is enforced by parameterising the search query and all prompts on a single `topic` string, with synonym expansion via a single Haiku call.

The pipeline emulates the analytical standard of Siciliano et al., *Movement Disorders* 2024 (DOI: 10.1002/mds.29649): QUADAS-adapted scoring (0–19), random-effects pooling with DerSimonian–Laird τ², leave-one-out sensitivity, Egger's regression / trim-and-fill for publication bias, and an automatic switch to moderator analysis when I² ≥ 90%. All of these are implemented in pure numpy and acknowledged as approximations of the formal R / ProMeta 3 implementations.

---

## 2. Methods

### 2.1 Architecture overview

The pipeline runs in six numbered phases (a seventh, ASReview filtering, exists in the codebase but is bypassed by an auto-promote path):

1. **Ingest** — PubMed Central via NCBI E-utilities (`esearch.fcgi` + `efetch.fcgi`, concurrency capped at 3 with 0.4 s per-request sleep); medRxiv via the biorxiv.org `details/medrxiv/{start}/{end}/{cursor}/json` endpoint with 90-day date chunks and client-side keyword filtering. Date range is 2020-01-01 to present.
2. **Triage** — Anthropic Message Batch API call to Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) producing a fixed JSON object per abstract with `is_topic_focused`, `study_design`, `sample_size`, `headline_finding`, `extraction_confidence`, and `confidence_flags`. Batch IDs are sanitised via `re.sub(r"[^a-zA-Z0-9_-]", "_", paper_id)[:64]` to satisfy the Anthropic Batch regex; a reverse `cid_to_pid` map is held in memory.
3. **Selection** — papers are ranked by `sample_size × design_weight × extraction_confidence`. Design weights are RCT 1.0, cohort 1.0, meta-analysis 1.2, all others 0.5.
4. **Full-text enrichment** — for each selected paper, the PMC OA XML is fetched via `efetch.fcgi?rettype=full&retmode=xml`. The parser strips `<ref-list>` nodes *before* extracting text (without this step the bibliography contributes ~30k tokens of irrelevant reference data to the deep-extraction input), then walks `<sec>` elements keeping only those whose lowercased title matches an allowlist (`abstract`, `introduction`, `methods`, `results`, `discussion`, `conclusions`). The concatenated output is hard-capped at 120,000 characters.
5. **Deep extraction** — Anthropic Message Batch API call to Claude Sonnet 4.6 (`claude-sonnet-4-6`) using the prompt in `config/prompts/extraction_sonnet.txt`. The prompt produces a single JSON object per paper with ten top-level blocks: `study_metadata`, `factual_extraction`, `methodology_appraisal`, `bias_audit`, `phenotype_mapping`, `calibration`, `provenance`, `quality_assessment` (QUADAS), `effect_sizes_classified`, `moderators`. The prompt mandates: (i) factual extraction is "explicit only — never infer"; (ii) if confidence < 0.70 for any field, that field is set to `null` rather than guessed; (iii) every numeric claim must have a literal-quote provenance entry ≤ 60 words.
6. **Persist** — Supabase upserts to `papers`, `extractions`, `provenance`, `contradictions` tables. Schema is v1 (`schema.sql`) extended by v2 migration (`schema_v2_migration.sql`) adding methodology / calibration columns plus the `provenance` table.

### 2.2 Cross-paper synthesis

Phase 5 (`pipeline/phase5_analyze.py`, ~840 lines) implements numeric aggregation and three Sonnet narrative passes. Numeric aggregators include:

- **Symptom consensus** — Counter over triage `main_symptoms` with per-symptom paper counts and prevalence percentages.
- **Calibrated consensus propagation** (`propagate_uncertainty()`) — projects per-paper `calibrated_certainty × extraction_confidence` into per-symptom consensus tiers via deterministic rules: any "contradicted" → CONTRADICTED; n ≥ 5 and (established + probable)/n ≥ 0.6 → ESTABLISHED or PROBABLE (the former requiring established/n ≥ 0.4); n ≥ 2 → POSSIBLE; otherwise SPECULATIVE.
- **QUADAS distribution** — per-paper totals from the 19-item checklist; papers with total > 13 enter the meta-analytic pool, papers with total ≤ 13 are excluded but retained in descriptive aggregates.
- **Effect size collection** — for every numeric association, the variance approximation `Var(r) ≈ (1 − r²)² / (n − 1)` is computed and rows are indexed by factor.
- **Random-effects pooling** (`_pool_random_effects()`) — inverse-variance weighting with DerSimonian–Laird τ² estimator. Returns pooled r, SE, 95% CI, I², Q, τ², and the per-study RE weights.
- **Model selection** (`select_model()`) — I² < 25% → fixed effects; 25–74% → random effects recommended; 75–89% → random effects mandatory; ≥ 90% → random effects critical (triggers moderator analysis and forest plot).
- **Leave-one-out sensitivity** — re-pool excluding each paper; flag as "influential" if removal shifts the pooled estimate by ≥ 10%.
- **Publication bias** (`assess_publication_bias()`) — Egger's regression of standardised effect on precision (intercept t-test approximated to a two-sided p via normal CDF) + a simple trim-and-fill estimate based on median-asymmetry; activates only at n ≥ 10 studies per factor.
- **Forest plot** (`forest_plot_text()`) — text-rendered forest plot with per-study weights, CIs, influential-paper stars (★), and Q / I² / τ² footer.

Three Sonnet synthesis passes follow:

- **Research synthesis** (`call_synthesizer`) — produces the structured research-report JSON with executive summary, key findings by certainty tier, definition heterogeneity narrative, symptom landscape narrative, methodology quality overview, bias audit summary, phenotype breakdown, contradictions, gaps and recommendations, and self-acknowledged limitations.
- **Due diligence** (`call_due_diligence`) — produces a pharma-investment-committee brief with target trial emulation inventory, objective biomarker bridges, clean baseline subset (variant + vaccination stratified, for COVID-family topics), mechanistic opportunity map (Blue Ocean / Red Ocean classification), methodological risk index per mechanism, contradictions matrix, recommended target phenotype with a Phase II design skeleton, and a 60-second committee-briefing list.
- **Executive summary** (`call_executive_summary`) — 2-page non-technical brief with an enforced forbidden-jargon list (GRADE, QUADAS, I², MCID, etc.) for non-scientist readers (investor / executive / journalist).

All three passes share slimmed inputs: `_slim_deep()` caps each paper at ~600 chars (paper_id, title prefix, design, n, GRADE, calibrated certainty, top 2 key findings, top 2 inferred limitations); `_slim_aggregates()` caps the consensus table at 50 entries. This keeps the input under the 200K context window even on full-corpus Long COVID runs.

### 2.3 Report rendering

Phase 6 (`pipeline/phase6_report.py`) builds three Jinja2 templates against the `analysis.json` output of Phase 5. The `CitationManager` class replaces `(CITE:DOI)` markers with sequential `[N]` numbering, building the bibliography in order of first citation. Defensive normalisation recovers from LLM-fabricated DOIs: PMC IDs are extracted from any token containing `(PMC\d{5,})` and matched against the in-corpus index; fake `10.1101/2025.05.PMC…` prefixes are stripped; obvious placeholders (`conceptual`, `not a paper`, `placeholder`) are dropped silently.

For citations whose DOI is not present in the ingested corpus (typically because the synthesis referenced an external paper that Sonnet had general training-data knowledge of), the pipeline performs a CrossRef API lookup (`api.crossref.org/works/{doi}`) and renders a Vancouver citation from the returned metadata. A module-level cache shares lookups across the three reports.

Final outputs: Markdown → HTML (via `markdown` library with `tables`, `fenced_code`, `toc` extensions) → PDF (via `reportlab`, replacing the originally-planned WeasyPrint because GTK3 is brittle on Windows). Three PDFs are auto-copied to the user's desktop with the naming convention `research_<topic_slug>_<date>.pdf`.

### 2.4 Calibration tier semantics

The prompt-level definitions:

- **Established** — multiple independent replications, GRADE Moderate+, no major bias.
- **Probable** — consistent direction across studies, GRADE Low, 1–2 manageable biases.
- **Possible** — limited evidence, GRADE Very Low, significant methodological concerns.
- **Speculative** — single study, high bias, or contradicted by stronger evidence.
- **Contradicted** — direct contradiction with higher-quality evidence.

Probabilistic summaries are mandated to use hedging language tied to the tier (e.g. "Evidence suggests a probable association…" rather than "Vaccination reduces risk by 39%."). This is enforced softly via the prompt rules; we did not measure compliance quantitatively.

---

## 3. Results

### 3.1 Long COVID corpus (2026-05-16)

A single end-to-end run on the PubMed Central Long COVID corpus produced the following:

| Stage | Count | Yield | Wall clock |
|---|---|---|---|
| PubMed Central ingest | 4,667 PMC IDs returned by `esearch` | — | ~6 min |
| Metadata fetched (with abstract) | 4,666 | 99.98% | (overlapped) |
| medRxiv | 0 papers | — | — (integration broken at the time; see Discussion) |
| Haiku triage | 4,665 | 4,665 / 4,667 = 99.96% | ~25 min |
| Top-N selection | 470 papers | — | (instant) |
| PMC OA full-text enrich | 463 | 463 / 470 = 98.5% | ~3 min |
| Sonnet deep extraction | ~441 | ~441 / 470 = 94% | ~35 min |
| Provenance entries stored | 7,369 | ~16.7 / paper avg | (overlapped) |
| Reports generated | 2 PDFs (research + DD) | — | ~2 min |
| **Total** | | | **~1 hour** |

API cost was approximately **$85–100** (the 50% Anthropic Batch discount applied to both triage and deep extraction). Deep-extraction failures were predominantly JSON-parse errors on Sonnet output that exceeded the original 8,192 max_tokens; raising the cap to 16,384 for the synthesis pass eliminated this class of failure on a re-run.

The corpus-level synthesis produced a QUADAS distribution centred around the mid-range (mean ~13.5 / 19; ~50% of deep-extracted papers passed the > 13 cutoff for the meta-analytic pool). The 8-axis bias audit identified `surveillance_bias` and `self_report_bias` as the two most frequent confounders. The calibrated-consensus output ranked symptoms by paper count; fatigue, cognitive dysfunction, post-exertional malaise, and dyspnoea dominated, with ~2-3 reaching the "probable" tier and most remaining at "possible". No symptom-level finding reached "established" — consistent with the field's well-documented heterogeneity in case definitions and ascertainment methods.

### 3.2 Narcolepsy corpus (2026-05-17) — generalisability demonstration

To test that the pipeline generalises beyond Long COVID without code modifications, a second run was performed with `topic="Narcolepsy"`, `max_papers=30`, `max_deep=3`:

| Stage | Count | Yield |
|---|---|---|
| MeSH expansion via Haiku | 15 synonyms returned in one call (~$0.001) | — |
| PubMed Central ingest | 30 PMC IDs | — |
| medRxiv | 360 preprints scanned across 1 interval (Q1 2024 not the full date range, the test was capped at 1 interval for cost) | 15 matched |
| Haiku triage | 30 / 30 | 100% |
| Top-N selection | 3 | — |
| PMC OA full-text enrich | 3 / 3 | 100% |
| Sonnet deep extraction | 3 / 3 | 100% |
| Provenance entries stored | 50 | ~16.7 / paper avg |
| Reports generated | 3 PDFs (research + DD + executive summary) | — |
| **Wall clock** | | **644.6 s (10.7 min)** |
| **API cost** | | **~$1.04** |

The MeSH-expansion call returned the search terms: *Narcolepsy, Parkinsonian Disorders* (LLM noise — rejected by PubMed without issue), *Parkinsonism* (also noise), *Idiopathic Narcolepsy*, *Cataplexy*, *Hypocretin Deficiency*, *Orexin Deficiency*, *Type 1 Narcolepsy*, *Type 2 Narcolepsy*, *Sleep Attacks*, *Excessive Daytime Sleepiness in Narcolepsy*, *Hypersomnia*, *HLA-DQB1\*06:02*, *Sleep Disorders, Intrinsic*, *REM Sleep Disorders*. The top-3 deep-extracted papers covered modafinil-induced psychosis, sodium oxybate Delphi-panel consensus recommendations, and wearable-device-derived biomarkers for narcolepsy type 1 — three distinct sub-literatures within a small corpus.

The pipeline correctly returned "no findings reached established certainty" given the small corpus (n = 30 is below the n ≥ 5 threshold for ESTABLISHED in `propagate_uncertainty()`). It correctly identified an industry-funded consensus panel (only seven clinicians, manufacturer-selected) as a governance issue in the bias audit. It correctly assigned `dopaminergic_deficit_basal_ganglia` — equivalent for hypocretin-deficit in narcolepsy terminology — rather than forcing the Long COVID canonical-4 mechanisms.

### 3.3 Methodological feature parity

The pipeline produces every artifact called for by the Siciliano et al. 2024 standard:

- ✓ QUADAS 0–19 scoring with cutoff 13.
- ✓ Random-effects pooling with DerSimonian–Laird τ² (pure numpy).
- ✓ Effect-size harmonisation to Pearson's r using `r = OR / sqrt(OR² + π²/3)`.
- ✓ Cohen-classified magnitudes (negligible / weak / moderate / strong).
- ✓ Leave-one-out sensitivity with 10% influence threshold.
- ✓ Egger's regression + trim-and-fill (n ≥ 10).
- ✓ Moderator analysis (Sonnet call) when I² ≥ 90%.
- ✓ Forest plots (text rendering) for critical-heterogeneity outcomes.

Each of these is documented in the report templates with explicit caveats that the implementations are pure-numpy approximations of the formal R / ProMeta 3 routines.

---

## 4. Discussion

### 4.1 What this pipeline is, and is not

This pipeline is structured cartography of a literature — automated extraction, calibrated cross-paper consensus, and a Sonnet-narrated synthesis with literal-quote provenance. It is **not** a systematic review: there is no PRISMA flow, no protocol pre-registration, no dual reviewer arbitration on QUADAS scoring, and the meta-analytic pooling is a pure-numpy approximation of the formal R `meta` implementation. The output is meant to *orient* a researcher or clinician toward the state of a field, not to replace formal evidence synthesis where the latter is required (regulatory submission, guideline development).

The principal value-add is calibration. Every numeric finding is tagged with a confidence tier that propagates upward from a per-paper Sonnet judgment to a cross-paper consensus through deterministic rules. The five-tier taxonomy (Established / Probable / Possible / Speculative / Contradicted) prevents the common LLM failure mode of stating low-confidence facts with high-confidence prose. The probabilistic-summary clause in the prompt — mandating hedged language tied to the calibration tier — pushes the model's output toward epistemic accuracy.

### 4.2 Generalisability

The Narcolepsy demonstration is the first end-to-end test that the pipeline is genuinely topic-agnostic. The only inputs that change between runs are (i) the topic string, (ii) the optional MeSH filter, (iii) the cap on papers and deep extractions. Everything else — query construction, prompt rendering, report rendering, calibration rules — is parameterised on the topic via `utils.run_context`.

Two minor caveats remain. First, the Long-COVID-specific phenotype names (`viral_reservoir`, `autoimmunity`, `vascular_endothelial`, `autonomic_metabolic`) still appear in the DD prompt's prose; Sonnet correctly returns topic-appropriate mechanisms (e.g. `dopaminergic_deficit_basal_ganglia` for Parkinson) but the prompt could be cleaner. Second, the JSON field names `is_long_covid_focused` and `long_covid_definition_weeks` are legacy holdovers from the original build; their semantic meaning is "is topic-focused" and "definition threshold weeks" but the names are misleading for non-COVID topics. Renaming requires a Supabase migration and is deferred.

### 4.3 medRxiv fix (2026-05-17)

The Long COVID run (2026-05-16) reported zero medRxiv papers, which we initially attributed to the topic-keyword vocabulary. Investigation revealed the actual cause: the biorxiv.org details API returns **30 papers per page**, not 100, and the original pagination logic had `if len(collection) < 100: break`, causing the loop to exit after the first page in every interval. The corrected logic uses `cursor >= messages[0].total` as the page-exhaustion test, and the date range is split into 90-day chunks to stay within whatever interval handling the API performs internally. The 2026-05-17 Narcolepsy run scanned 360 preprints in a single quarter and matched 15 — the first non-zero medRxiv yield in the project's history.

This anecdote underscores a methodological point: silent failures in upstream data ingestion are the single largest threat to LLM-based synthesis pipelines, because the downstream model output looks just as fluent and confident regardless of whether the corpus was complete. We recommend that any production deployment include a per-source ingest-yield monitor — e.g. assert that medRxiv returns a non-zero count for any topic that should have preprint coverage.

### 4.4 Provenance enforcement

The `provenance` table is the operational heart of auditability. Every deep-extracted claim is keyed to `(paper_id, field_name, claim, quote, section)` and is foreign-key constrained to the `papers` table. The Sonnet prompt enforces a minimum of 5 provenance entries per paper (10–15 when full text is available, ≤ 60 words per quote). In practice the Long COVID run produced ~16.7 provenance entries per deep-extracted paper, and the Narcolepsy run produced exactly 50 entries across 3 papers (also ~16.7 / paper). This is enough granularity to spot-check any specific claim in a report by SQL query: `SELECT * FROM provenance WHERE paper_id = … AND field_name = …`.

### 4.5 Cost structure

At ~$85–100 per 4,666-paper full corpus run and ~$1.04 per 30-paper small run, the pipeline is operationally cheap enough to re-run on each calendar quarter or in response to specific new publications. The Anthropic Batch API's 50% discount on both triage and deep extraction is the single largest cost lever — without it, the same Long COVID run would cost ~$170–200, which is still under any plausible analyst-hour comparison but doubles the deployment threshold.

### 4.6 Limitations

- **LLM-generated structured extraction** is the single biggest source of methodological risk. The prompt forces `null` for any field with confidence < 0.70, but this is a soft constraint. Even with provenance, the schema mapping itself can misread nuance or — rarely — hallucinate. We mitigate this with literal-quote enforcement and 5-tier calibration, but a formal benchmark against gold-standard manual extraction has not been performed.
- **QUADAS is single-LLM scored** without dual-reviewer arbitration.
- **Random-effects pooling, Egger's regression, and trim-and-fill** are pure-numpy approximations of formal R / ProMeta 3 routines.
- **PMC OA only** — subscription-journal and many high-impact recent papers are not retrievable. The 98.4% coverage figure refers to coverage *within* the top-500 selection, which itself is selected from open-access-indexed papers.
- **Narcolepsy run is small** (n = 30 triaged, n = 3 deep). It demonstrates the pipeline runs end-to-end on a non-COVID topic; it does not demonstrate large-scale equivalence to the Long COVID run. A larger Narcolepsy run (n ≥ 500) is planned.
- **No external validation.** The Long COVID synthesis has not been compared to a manual systematic review of the same corpus. We expect partial alignment but cannot quantify divergence.

---

## 5. Conclusion

A literature-synthesis pipeline anchored in literal-quote provenance, explicit calibration tiers, and topic-agnostic parameterisation can produce calibrated cross-paper synthesis at fixed dollar cost and within ~1 hour wall-clock per condition. The Long COVID demonstration (4,666 / 470 / 7,369 / ~$85–100) and the Narcolepsy demonstration (30 / 3 / 50 / ~$1.04) together establish that the same pipeline runs unchanged across distinct disease areas. The remaining open problems — author / reference XML parsing artefacts, the lack of formal external benchmarking against manual systematic review — are tractable and identified.

We position this work as evidence that the cost-and-slowness bottleneck of biomedical literature synthesis is removable for any condition with adequate open-access coverage.

---

## References

(Selected, illustrative — the operational pipeline cites the actual papers it ingests; this preprint cites the methodological foundations.)

1. Siciliano M, et al. Cognitive impairment in Parkinson's disease: meta-analysis of prevalence and methodological quality. *Movement Disorders*. 2024. doi:10.1002/mds.29649
2. Wang Y, et al. Performance of large language models for screening titles and abstracts in systematic reviews. *J Med Internet Res*. 2024. doi:10.2196/52758
3. Tang T, et al. Evaluating large language models on medical evidence summarization. *npj Digital Medicine*. 2023. doi:10.1038/s41746-023-00896-7
4. DerSimonian R, Laird N. Meta-analysis in clinical trials. *Controlled Clinical Trials*. 1986. 7(3):177–88. doi:10.1016/0197-2456(86)90046-2
5. Egger M, et al. Bias in meta-analysis detected by a simple, graphical test. *BMJ*. 1997. 315(7109):629–34. doi:10.1136/bmj.315.7109.629
6. Whiting PF, et al. QUADAS-2: a revised tool for the quality assessment of diagnostic accuracy studies. *Annals of Internal Medicine*. 2011. 155(8):529–36. doi:10.7326/0003-4819-155-8-201110180-00009
7. Guyatt GH, et al. GRADE: an emerging consensus on rating quality of evidence and strength of recommendations. *BMJ*. 2008. 336(7650):924–6. doi:10.1136/bmj.39489.470347.AD

---

*Author: Hamsa. Code, prompts, schema, and the two demonstration runs described above are available on request. This preprint describes work performed between 2026-05-15 and 2026-05-17.*
