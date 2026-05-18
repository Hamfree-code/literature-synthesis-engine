-- Run this once in Supabase SQL editor

create extension if not exists vector;

create table if not exists papers (
  id              text primary key,
  source          text not null,
  title           text not null,
  authors         text[],
  year            int,
  journal         text,
  abstract        text,
  full_text       text,
  url             text,
  ingested_at     timestamptz default now()
);

create table if not exists extractions (
  paper_id              text references papers(id) on delete cascade,
  extraction_level      text not null,
  long_covid_definition text,
  sample_size           int,
  population            text,
  study_design          text,
  symptoms              jsonb,
  biomarkers            jsonb,
  risk_factors          jsonb,
  limitations           text[],
  authors_conclusions   text,
  methodology_quality   int,
  extracted_at          timestamptz default now(),
  primary key (paper_id, extraction_level)
);

create table if not exists embeddings (
  paper_id    text references papers(id) on delete cascade primary key,
  embedding   vector(1024)
);

create table if not exists contradictions (
  id                serial primary key,
  topic             text not null,
  paper_a           text references papers(id),
  paper_b           text references papers(id),
  claim_a           text,
  claim_b           text,
  likely_cause      text,
  detected_at       timestamptz default now()
);

create index on embeddings using ivfflat (embedding vector_cosine_ops);
create index on papers (year);
create index on extractions (extraction_level);
