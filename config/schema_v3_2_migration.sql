-- v3.2 migration (UPGRADE v3.2 — Methodological Hardening & Provenance Integrity)
-- Additive only: every change is `add column if not exists` / `create table if
-- not exists`. No existing rows are mutated. Run once in the Supabase SQL editor
-- AFTER schema.sql, schema_v2_migration.sql and schema_v3_migration.sql.
--
-- A run is NOT reproducible unless the run registry persists: the PRISMA flow
-- record, the outcome-dictionary version, the RoB instrument per paper, the
-- GRADE evidence bodies, the reconciliation report, and the output-ceiling tier
-- (spec §12).

-- ============================================================================
-- WP-1 — Extraction integrity: status + typed failure reason per extraction,
--        and conservation counts + PRISMA flow on the run record.
-- ============================================================================
alter table extractions
  add column if not exists extraction_status text default 'succeeded',  -- succeeded | failed | substituted
  add column if not exists failure_reason    text;                      -- truncation | schema_violation | api_error | timeout

alter table runs
  add column if not exists n_intended       int,
  add column if not exists n_extracted      int,
  add column if not exists n_failed         int,
  add column if not exists n_substituted    int,
  add column if not exists prisma_flow      jsonb,   -- N0..N8 + failures_by_reason
  add column if not exists substitutions    jsonb;   -- [{failed_id, replacement_id, failed_reason}]

-- ============================================================================
-- WP-9 — Canonical paper registry + shared, run-stable citation numbering.
-- ============================================================================
alter table papers
  add column if not exists canonical_id text;        -- PMCID preferred, DOI fallback

create index if not exists idx_papers_canonical on papers (canonical_id);

-- One citation number per (run, canonical_id), shared across ALL documents of
-- that run so report [1] == due-diligence [1].
create table if not exists run_citations (
  run_id          uuid references runs(id) on delete cascade,
  canonical_id    text not null,
  citation_number int  not null,
  created_at      timestamptz default now(),
  primary key (run_id, canonical_id),
  unique (run_id, citation_number)
);

-- Provenance integrity events: bibliography bleed, PMCID mismatch, quote drift.
create table if not exists provenance_errors (
  id           serial primary key,
  run_id       uuid references runs(id) on delete cascade,
  paper_id     text,
  error_type   text not null,   -- bibliography_bleed | pmcid_invalid | quote_drift | unresolved_citation
  detail       text,
  created_at   timestamptz default now()
);

-- ============================================================================
-- WP-7 — Case definition, canonicalised, per extraction (gates aggregation).
-- ============================================================================
alter table extractions
  add column if not exists case_definition_duration_weeks int,
  add column if not exists case_definition_source         text,   -- WHO | NICE | CDC | ICD-10-U09.9 | author-defined | none
  add column if not exists case_definition_functional_impact_required boolean;

-- ============================================================================
-- WP-2 — Outcome-level evidence bodies carry GRADE (never per paper).
-- ============================================================================
create table if not exists evidence_body (
  id                 serial primary key,
  run_id             uuid references runs(id) on delete cascade,
  outcome            text not null,    -- controlled vocabulary (WP-5/6)
  comparison         text,
  contributing_papers text[],
  study_designs      text[],
  starting_certainty text,             -- high | low  ([CALC])
  downgrades         jsonb,            -- {risk_of_bias, inconsistency, indirectness, imprecision, publication_bias}
  upgrades           jsonb,            -- {large_effect, dose_response, plausible_confounding}
  final_grade        text,            -- high | moderate | low | very_low  ([CALC])
  rationale          text,            -- [LLM] narrative grounded in the domain decisions
  created_at         timestamptz default now()
);

create index if not exists idx_evidence_body_run     on evidence_body (run_id);
create index if not exists idx_evidence_body_outcome on evidence_body (outcome);

-- ============================================================================
-- WP-4 — Symptom landscape: mention-frequency vs pooled patient prevalence,
--        kept as DISTINCT quantities so neither is rendered as the other.
-- ============================================================================
create table if not exists symptom_landscape (
  id                       serial primary key,
  run_id                   uuid references runs(id) on delete cascade,
  canonical_outcome        text not null,
  papers_mentioning        int,
  papers_any_symptom       int,
  mention_frequency_pct    numeric(5,1),   -- "% of symptom-reporting papers" (NOT prevalence)
  pooled_patient_prevalence numeric(5,1),  -- null unless defensible pooling exists
  prevalence_source_id     text,           -- attribution when carried from a single source ([LLM])
  created_at               timestamptz default now()
);

create index if not exists idx_symptom_landscape_run on symptom_landscape (run_id);

-- ============================================================================
-- WP-3 / WP-5/6 / WP-9 / WP-10 — Run-level reproducibility artifacts.
-- ============================================================================
alter table runs
  add column if not exists outcome_dictionary_version text,
  add column if not exists rob_instruments      jsonb,   -- {paper_id: instrument}
  add column if not exists evidence_bodies       jsonb,   -- snapshot of evidence_body rows
  add column if not exists reconciliation_report jsonb,   -- WP-9 prose-vs-calibrated diff
  add column if not exists output_ceiling_tier   text,    -- WP-10 max evidence tier
  add column if not exists normalisation_review  jsonb;   -- unmapped outcome labels
