-- v3 migration (Master Improvement Spec v3.0)
-- Adds:
--   1. Two-step arbiter fields on extractions (reconciliation_triggered, llm_judgment)
--   2. UMLS / MeSH normalisation table (extracted_phenotypes)
--   3. Human ratings table for Cohen's Kappa validation engine
--   4. Runs registry table (per Priority 4.1)
--   5. Conservative DD field (confidence_in_recommendation) stored in extractions.calibration JSONB

-- ============================================================================
-- 1. Arbiter / two-step extraction fields
-- ============================================================================
alter table extractions
  add column if not exists reconciliation_triggered boolean default false,
  add column if not exists reviewer_a_raw jsonb,
  add column if not exists reviewer_b_raw jsonb,
  add column if not exists arbiter_notes text,
  add column if not exists llm_judgment_flags jsonb;
  -- llm_judgment_flags is a {field_path: bool} map per the spec.
  -- true = inference; false = deterministic / direct quote.

-- ============================================================================
-- 2. UMLS / MeSH normalised phenotype storage
-- ============================================================================
create table if not exists extracted_phenotypes (
  id            serial primary key,
  paper_id      text references papers(id) on delete cascade,
  verbatim_text text not null,
  umls_cui      text,
  mesh_heading  text,
  entity_type   text,   -- 'phenotype' | 'mechanism' | 'biomarker' | 'risk_factor'
  llm_judgment  boolean default true,   -- the CUI itself is LLM-inferred unless UMLS API used
  created_at    timestamptz default now()
);

create index if not exists idx_extracted_phenotypes_paper on extracted_phenotypes (paper_id);
create index if not exists idx_extracted_phenotypes_cui   on extracted_phenotypes (umls_cui);
create index if not exists idx_extracted_phenotypes_mesh  on extracted_phenotypes (mesh_heading);

-- ============================================================================
-- 3. Human ratings for Kappa validation
-- ============================================================================
create table if not exists human_ratings (
  id             serial primary key,
  paper_id       text references papers(id) on delete cascade,
  rater_id       text not null,         -- anonymised id of the human rater
  field_name     text not null,         -- e.g. 'grade_certainty' | 'quadas_total' | 'surveillance_bias'
  field_kind     text not null,         -- 'discrete' | 'continuous' | 'boolean'
  rating_value   text not null,         -- stored as text; parsed by validation engine per field_kind
  rated_at       timestamptz default now(),
  unique (paper_id, rater_id, field_name)
);

create index if not exists idx_human_ratings_paper on human_ratings (paper_id);
create index if not exists idx_human_ratings_field on human_ratings (field_name);

-- ============================================================================
-- 4. Runs registry (Priority 4.1; the migration adds the table even though the
--    UI integration is deferred, so future runs can start populating it.)
-- ============================================================================
create table if not exists runs (
  id                       uuid primary key default gen_random_uuid(),
  topic                    text not null,
  mesh_terms               text,
  run_date                 date not null default current_date,
  sources                  text[] default '{}',
  n_papers_ingested        int,
  n_papers_triaged         int,
  n_papers_deep            int,
  n_provenance_entries     int,
  n_reconciliations        int,
  api_cost_usd             numeric(10,2),
  runtime_seconds          int,
  grade_distribution       jsonb,
  top_biases               jsonb,
  created_at               timestamptz default now(),
  unique (topic, run_date)
);

create index if not exists idx_runs_topic on runs (topic);
create index if not exists idx_runs_date  on runs (run_date desc);

alter table papers add column if not exists run_id uuid references runs(id);
alter table extractions add column if not exists run_id uuid references runs(id);

-- ============================================================================
-- 5. Conservative DD support: the confidence_in_recommendation field is stored
--    inside analysis.due_diligence JSON in Phase 5 output. No DB column added;
--    Phase 5 writes it through the synthesis path. This block is informational.
-- ============================================================================
