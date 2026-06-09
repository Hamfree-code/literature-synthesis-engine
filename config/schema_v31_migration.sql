-- v3.1 migration (UPGRADE v3.1) — run AFTER schema.sql, schema_v2_migration.sql,
-- schema_v3_migration.sql. Idempotent (IF NOT EXISTS / additive only).
-- Touches: P1 (extraction_attempts), P2 (CUI verification, retraction, umls_cache),
-- P5 (runs extension + run_id FKs), P7 (legacy field rename via views).

-- ============================================================================
-- P1 — Deep-extraction attempt log (yield diagnostics + QA sheet source)
-- ============================================================================
create table if not exists extraction_attempts (
  id            bigserial primary key,
  paper_id      text,
  run_id        uuid,
  reviewer      text,                       -- 'a' | 'b' | 'arb' | 'single'
  attempt       int  not null default 1,
  stop_reason   text,                       -- 'end_turn' | 'max_tokens' | 'tool_use' | ...
  tokens_out    int,
  parse_ok      boolean default false,
  detail        text,
  created_at    timestamptz default now()
);
create index if not exists idx_extraction_attempts_paper on extraction_attempts (paper_id);
create index if not exists idx_extraction_attempts_run   on extraction_attempts (run_id);

-- ============================================================================
-- P2 — UMLS verification on normalised entities + offline cache
-- ============================================================================
alter table extracted_phenotypes
  add column if not exists cui_verified  boolean default false,
  add column if not exists preferred_name text,
  add column if not exists verified_at   timestamptz;

create table if not exists umls_cache (
  cui            text primary key,
  preferred_name text,
  exists_in_umls boolean default false,
  verified_at    timestamptz default now()
);

-- ============================================================================
-- P2 — Retraction screening fields on papers
-- ============================================================================
alter table papers
  add column if not exists is_retracted    boolean default false,
  add column if not exists retraction_doi  text,
  add column if not exists retraction_date date;

-- ============================================================================
-- P5 — Extend the runs registry (schema_v3 created it; here we add the columns
-- the product/UI needs and were missing) and link rows to a run.
-- ============================================================================
alter table runs
  add column if not exists deep_success_rate numeric(5,2),
  add column if not exists kappa_summary     jsonb,
  add column if not exists sources_breakdown jsonb,
  add column if not exists n_retracted_excluded int default 0,
  add column if not exists cui_verified_pct  numeric(5,2),
  add column if not exists fulltext_coverage_pct numeric(5,2),
  add column if not exists manifest_sha256   text,
  add column if not exists engine_version    text;

-- run_id FK on papers/extractions (nullable → legacy rows untouched).
alter table papers      add column if not exists run_id uuid references runs(id);
alter table extractions add column if not exists run_id uuid references runs(id);
create index if not exists idx_papers_run on papers (run_id);
create index if not exists idx_extractions_run on extractions (run_id);

-- ============================================================================
-- P7 — Legacy field rename with temporary compatibility views.
-- The triage/deep JSON still emits is_long_covid_focused / *_definition_weeks
-- for one version; the canonical column names become topic-neutral. We expose
-- a view so existing dashboards keep working while code migrates.
-- ============================================================================
alter table extractions
  add column if not exists is_topic_focused        boolean,
  add column if not exists definition_threshold_weeks int;

create or replace view extractions_compat as
  select *,
         is_topic_focused        as is_long_covid_focused,
         definition_threshold_weeks as long_covid_definition_weeks
  from extractions;
