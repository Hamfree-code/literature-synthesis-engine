# Hams & Co. Research Division — Literature Synthesis Engine

*Executive Brief · 2026-05-17 · 2 pages · for non-technical readers*

---

## What this is

The Hams & Co. Research Division has built and validated an automated pipeline that does what a team of research analysts would do — read scientific papers, extract their structured content, weigh their methodological strength, and write a calibrated synthesis — but at a fraction of the cost and time. The system is **generic**: the same code, with the same prompts, runs over any medical condition available in PubMed Central. Long COVID is the primary demonstration. Narcolepsy is a second, independent demonstration that the same system works unchanged on a fundamentally different therapeutic area.

The output is three documents per condition: a full **research report** with calibrated certainty tiers and citations, a **pharma due-diligence brief** designed for investment-committee consumption, and a **non-technical executive summary** for journalists, executives, and policymakers. Every quantitative claim in the deep-extraction layer is anchored to a literal quote from the source paper, stored in a database and verifiable by any reader.

## What it found on Long COVID

On the 2026-05-16 Long COVID demonstration run, the pipeline processed the entire PubMed Central corpus on the condition:

- **4,666 papers triaged** by Claude Haiku 4.5 with self-reported extraction confidence
- **470 papers deep-analysed** by Claude Sonnet 4.6 with structured methodological appraisal
- **7,369 literal-quote provenance entries** stored in the database
- **Runtime: approximately one hour**
- **API cost: approximately $85–100**

The synthesis identified the field's well-documented patterns: fatigue, cognitive dysfunction, post-exertional malaise, and dyspnoea as the dominant symptoms; surveillance bias and self-report bias as the two most common methodological confounders; case-definition fragmentation (the WHO 12-week threshold versus the NICE 4-week threshold versus registry-based ICD-10 U09.9 coding) as the principal obstacle to meta-analytic pooling; and a striking shortage of studies that stratify cleanly by variant era and vaccination status — meaning the "clean baseline" subset suitable for protocol design is only ~5–10% of the corpus. The pipeline rated zero symptom-level findings as *established*, a small handful as *probable*, and most as *possible* or *speculative* — an honest reflection of a field whose evidence base is broader than it is deep.

## Why this matters

The bottleneck in evidence-based medicine has historically been the cost and slowness of manually synthesising primary literature. A formal Cochrane-style systematic review of a 4,666-paper corpus typically takes a multi-author team several months and tens of thousands of dollars in expert time. This pipeline produces a structurally comparable output — with literal-quote provenance enabling spot-check verification per claim — in approximately one hour for the API cost of a restaurant dinner.

For **researchers**, that means a literature map can be regenerated on demand whenever a relevant new paper appears, rather than once every multi-year review cycle.

For **clinicians**, it means treatment guidelines can be cross-checked against the actual evidence base — including the methodological-quality distribution and bias audit — before a recommendation is made.

For **pharma investors and operators**, the due-diligence brief identifies the mechanisms with the highest signal-to-noise ratio, the candidate objective endpoints with the most cross-paper support, and the methodological risks that could blindside a scientific advisory board. The Long COVID DD brief surfaces specific Phase II design choices that the underlying evidence supports.

For **policymakers and regulators**, the system identifies the governance-relevant signals (industry-funded consensus panels, study designs with circular case definitions) and flags them explicitly.

## Why the Narcolepsy run matters

On 2026-05-17, the same pipeline was re-run with `topic="Narcolepsy"` — no code changes, no per-condition engineering. The run completed in 10.7 minutes at a cost of approximately $1.04 and produced all three reports. The pipeline correctly performed a single AI-assisted call to expand the search terms (MeSH headings, synonyms, abbreviations); correctly retrieved 30 PubMed papers and 15 medRxiv preprints (the medRxiv integration had been silently broken on the Long COVID run, and was diagnosed and fixed during the Narcolepsy run); correctly fetched and analysed three full-text papers; correctly flagged a manufacturer-controlled consensus panel as a governance issue; and correctly assigned narcolepsy-appropriate biological mechanisms rather than forcing the Long COVID mechanisms. The same code that produces a 4,666-paper Long COVID synthesis can now be aimed at any other condition with adequate open-access coverage, on a one-click basis.

## Honest limitations

- The extraction is performed by an AI system, not by human reviewers. Every output should be spot-checked against the underlying provenance quotes before being used for clinical or regulatory decisions. The pipeline is designed to make that spot-checking easy, not to make it unnecessary.
- The pipeline only sees open-access papers. A substantial portion of high-impact recent research sits behind subscription paywalls and was not included. Findings may therefore skew toward whatever the open-access slice over-represents.
- The methodological-quality scoring (QUADAS), random-effects pooling, and publication-bias estimation are simplified approximations of the formal R / ProMeta 3 implementations. They are adequate for orienting a researcher; they are not adequate for regulatory submission.
- This is not a systematic review. There is no PRISMA flow, no protocol pre-registration, and no second-reviewer arbitration. The output is structured cartography of a literature, not a meta-analytic verdict.
- The pipeline has not yet been benchmarked against a manual systematic review of the same corpus. Partial alignment is expected; quantitative divergence is unmeasured.

---

*Generated by Hams & Co. Research Division — Literature Synthesis Engine. For the underlying technical detail, see the accompanying scientific preprint and technical documentation. For the full Long COVID and Narcolepsy research and due-diligence reports, see the accompanying PDFs.*
