# 01 — Overview

## Problem
Systematic literature review is the gold standard for evidence synthesis but
costs weeks of expert time per question. Naive "ask an LLM to summarise papers"
fails on three axes a domain expert will reject instantly: (1) hallucinated or
unverifiable claims, (2) no methodological appraisal (bias, GRADE, effect
sizes), (3) no reproducibility or auditability. This engine targets exactly
those three failure modes.

## What it produces
For a medical condition (any term queryable in PubMed/PMC), one run emits:
- **Research synthesis** (cited, Vancouver, provenance-linked).
- **Pharma due-diligence brief** (conservative; Phase-II gating rules).
- **Executive summary** + **C-level one-pager** (non-technical).
- **Machine-readable supplement** (ZIP): manifest, PRISMA SVG, extractions.csv,
  provenance.csv, evidence_table.xlsx, references.ris/.bib.
- **Editable DOCX** + **QA certificate** + verifiable **Run ID**.

## Core value proposition (what makes it not-a-toy)
1. **Literal-quote provenance**: every deep-extraction claim is bound to a
   verbatim quote stored per-paper. Verifying any claim takes < 1 min without DB
   access (anchor links in HTML / page links in PDF).
2. **Dual-reviewer + arbiter extraction**: each paper is extracted twice and
   reconciled by an independent arbiter, mirroring dual-reviewer SR with
   adjudication. Reviewer A is Sonnet (temp 0.1); Reviewer B is Sonnet (temp 0.3)
   or — for genuine cross-model diversity that decorrelates shared blind spots —
   **Gemini Flash**. The arbiter is **Opus** (the strongest neutral adjudicator).
   Disagreements are surfaced (`reconciliation_triggered`); the reviewer-B
   provider is recorded (`reviewer_b_provider`). See `07` for the rationale.
3. **Reference statistics**: PyMARE DerSimonian–Laird pooling + statsmodels
   Egger, not hand-rolled numpy (legacy kept as a checked fallback).
4. **Fail-secure provenance of evidence**: retracted papers excluded; if the
   retraction service can't be reached the run is marked INCOMPLETE rather than
   silently "clean".
5. **Reproducibility as a feature**: a SHA-256 manifest (prompt hashes, model
   strings, queries, flags, real cost) is the Run ID printed on the cover.

## Audience
- Pharma medical-affairs / BD&L (GRADE SoF, DD brief).
- Research groups doing scoping/evidence maps.
- Anyone needing an auditable first-pass synthesis to hand to human reviewers.

## Honest framing (baked into every output)
"PRISMA-conformant reporting of an AI-assisted evidence mapping. Not a
registered systematic review (no protocol pre-registration, no human dual
screening)." The system formats to the standard without pretending to be the
human process.

## Status (2026-06)
v3.1 implemented (P0–P7 + resilience hardening) plus three post-v3.1 deltas
(Unpaywall fail-secure + cache, Opus arbiter, cross-model Gemini reviewer — see
`07`). **117 non-live tests green**, CI configured. **No live end-to-end run
executed yet** in this environment (network allowlist + budget); the Gemini
reviewer path is unit-tested with fakes but not yet exercised against the real
Gemini Batch API — see `05` and `07`.
