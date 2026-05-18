"""Per-run metadata (topic, mesh_terms) shared across phases.

Phase 1 writes the context at ingest time. Phases 3, 5, 6 read it to
parameterise prompts and report titles. Survives across runs as long as
the same checkpoint dir is reused (or until a fresh ingestion overwrites).
"""
from __future__ import annotations

import json
from pathlib import Path

from app_paths import app_data

CONTEXT_PATH = app_data("data/raw/run_meta.json")


def save_run_context(topic: str | None, mesh_terms: str | None = None) -> dict:
    ctx = {
        "topic": (topic or "long covid").strip() or "long covid",
        "mesh_terms": (mesh_terms or "").strip() or None,
    }
    CONTEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTEXT_PATH.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8")
    return ctx


def get_run_context() -> dict:
    if CONTEXT_PATH.exists():
        try:
            return json.loads(CONTEXT_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"topic": "long covid", "mesh_terms": None}


def topic_lower() -> str:
    return (get_run_context().get("topic") or "long covid").strip().lower()


def topic_title() -> str:
    """A presentation-cased version of the topic, e.g. 'long covid' → 'Long COVID',
    'parkinson disease' → 'Parkinson Disease'."""
    raw = (get_run_context().get("topic") or "long covid").strip()
    if not raw:
        return "Long COVID"
    # Special-case acronyms / known names
    known = {
        "long covid": "Long COVID",
        "covid": "COVID-19",
        "covid-19": "COVID-19",
        "pasc": "PASC",
        "long-covid": "Long COVID",
    }
    if raw.lower() in known:
        return known[raw.lower()]
    return raw.title()


def topic_slug() -> str:
    """Filesystem-safe lowercase slug."""
    raw = (get_run_context().get("topic") or "long covid").strip().lower()
    out = "".join(c if c.isalnum() else "_" for c in raw).strip("_")
    return out or "research"


def clear_stale_state_if_topic_changed(new_topic: str | None, new_mesh: str | None = None) -> tuple[bool, str | None]:
    """Wipe checkpoints + raw + filtered when the incoming topic or MeSH differs
    from the previously-stored run context. Same-topic re-runs resume from the
    existing checkpoints; different-topic runs start clean.

    Returns (wiped, previous_topic). previous_topic is None on first run.
    """
    if not CONTEXT_PATH.exists():
        return False, None
    try:
        prev = json.loads(CONTEXT_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        prev = None
    if not prev:
        return False, None

    new_topic_norm = (new_topic or "long covid").strip().lower()
    new_mesh_norm = (new_mesh or "").strip() or None
    prev_topic_norm = (prev.get("topic") or "long covid").strip().lower()
    prev_mesh_norm = prev.get("mesh_terms")

    if new_topic_norm == prev_topic_norm and new_mesh_norm == prev_mesh_norm:
        return False, prev.get("topic")

    for subdir in ("data/checkpoints", "data/raw", "data/filtered"):
        path = app_data(subdir)
        if not path.exists():
            continue
        for f in path.iterdir():
            if f.is_file():
                try:
                    f.unlink()
                except OSError:
                    pass
    return True, prev.get("topic")
