"""UMLS / MeSH normalisation via Anthropic tool calling.

Master Improvement Spec v3.0 — Priority 2.2.

For each deep extraction, we collect the free-text biomedical entities the
reviewer / arbiter pulled out (phenotypes, mechanisms, biomarkers, risk
factors) and ask Haiku to attach a UMLS CUI and a MeSH heading to each.

NOTE on accuracy (declared limitation): we do not call the real UMLS REST
API — that would require a separate API key not bundled with this build.
The CUIs returned here come from Haiku's training-data knowledge of the
UMLS Metathesaurus. They are correct for high-frequency concepts and
unverified for rare ones. Every CUI is flagged llm_judgment=true in the
database so downstream consumers know.
"""

from __future__ import annotations

import json

import anthropic

from config.settings import settings

_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


NORMALIZE_TOOL_SCHEMA = {
    "name": "normalize_biomedical_entities",
    "description": (
        "Attach the canonical UMLS Concept Unique Identifier (CUI) and MeSH "
        "heading to each free-text biomedical entity. If a CUI is unknown, "
        "return an empty string for that field rather than fabricating."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "normalized_entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "verbatim_text": {"type": "string"},
                        "entity_type": {
                            "type": "string",
                            "enum": ["phenotype", "mechanism", "biomarker", "risk_factor"],
                        },
                        "umls_cui": {"type": "string"},
                        "mesh_heading": {"type": "string"},
                    },
                    "required": ["verbatim_text", "entity_type", "umls_cui", "mesh_heading"],
                },
            }
        },
        "required": ["normalized_entities"],
    },
}


def _collect_entities(extraction: dict) -> list[dict]:
    """Pull free-text biomedical entities out of a deep extraction record."""
    out: list[dict] = []
    fx = extraction.get("factual_extraction") or {}
    for sym in (fx.get("symptoms_prevalence") or {}).keys():
        out.append({"verbatim_text": sym, "entity_type": "phenotype"})
    for bio in (fx.get("biomarker_findings") or {}).keys():
        out.append({"verbatim_text": bio, "entity_type": "biomarker"})
    for rf in fx.get("risk_factors_quantified") or []:
        if isinstance(rf, dict) and rf.get("factor"):
            out.append({"verbatim_text": rf["factor"], "entity_type": "risk_factor"})

    pm = extraction.get("phenotype_mapping") or {}
    if pm.get("primary_mechanism"):
        out.append({"verbatim_text": pm["primary_mechanism"], "entity_type": "mechanism"})
    for sec in pm.get("secondary_mechanisms") or []:
        if isinstance(sec, str):
            out.append({"verbatim_text": sec, "entity_type": "mechanism"})
        elif isinstance(sec, dict):
            key = sec.get("phenotype") or sec.get("mechanism") or sec.get("name")
            if key:
                out.append({"verbatim_text": key, "entity_type": "mechanism"})

    # Dedupe (case-insensitive on verbatim_text within the same type)
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for e in out:
        key = (e["entity_type"], e["verbatim_text"].strip().lower())
        if key in seen or not e["verbatim_text"].strip():
            continue
        seen.add(key)
        unique.append(e)
    return unique


def normalize_extraction(extraction: dict, *, max_entities: int = 30) -> list[dict]:
    """Run a single Haiku tool call to attach CUI / MeSH to extracted entities.

    Returns a list of {verbatim_text, entity_type, umls_cui, mesh_heading,
    llm_judgment: True} dicts. Returns an empty list on any failure (the deep
    extraction still proceeds; normalisation is best-effort).
    """
    entities = _collect_entities(extraction)[:max_entities]
    if not entities:
        return []

    prompt = (
        "You are a biomedical ontology normalizer. For each free-text entity "
        "below, return its canonical UMLS Concept Unique Identifier (CUI, e.g. "
        "C0033578) and its official MeSH heading. If you do not know the CUI "
        "with high confidence, return an empty string for that field — do NOT "
        "fabricate. Use the normalize_biomedical_entities tool.\n\n"
        f"Entities:\n{json.dumps(entities, ensure_ascii=False, indent=2)}"
    )

    try:
        response = _client.messages.create(
            model=settings.ANTHROPIC_HAIKU_MODEL,
            max_tokens=2048,
            tools=[NORMALIZE_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "normalize_biomedical_entities"},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:
        return []

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "normalize_biomedical_entities":
            normalized = (block.input or {}).get("normalized_entities") or []
            for n in normalized:
                n["llm_judgment"] = True
            return normalized
    return []
