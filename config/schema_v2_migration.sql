-- v2 migration: extends v1, does not drop any existing tables.
-- Run once in Supabase SQL editor.

alter table extractions
  add column if not exists extraction_confidence float,
  add column if not exists confidence_flags jsonb,
  add column if not exists calibrated_certainty text,
  add column if not exists calibrated_certainty_rationale text,
  add column if not exists uncertainty_sources jsonb,
  add column if not exists probabilistic_summary text,
  add column if not exists grade_certainty text,
  add column if not exists grade_rationale text,
  add column if not exists nos_score int,
  add column if not exists bias_audit jsonb,
  add column if not exists phenotype_mapping jsonb,
  add column if not exists pandemic_era text;

create table if not exists provenance (
  id serial primary key,
  paper_id text references papers(id) on delete cascade,
  extraction_level text not null,
  field_name text not null,
  claim text not null,
  quote text not null,
  section text,
  page int,
  confidence float,
  created_at timestamptz default now()
);

create index if not exists idx_provenance_paper on provenance (paper_id);
create index if not exists idx_provenance_field on provenance (field_name);

alter table contradictions
  add column if not exists contradiction_type text,
  add column if not exists likely_resolution text,
  add column if not exists detection_confidence float,
  add column if not exists provenance_a jsonb,
  add column if not exists provenance_b jsonb;
