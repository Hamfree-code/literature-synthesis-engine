"""WP-5/6 — Controlled outcome vocabulary + normalisation.

DEFECT: no pre-specified outcome set. Free-text labels ("brain fog",
"cognitive dysfunction", "cognitive impairment") were treated as distinct rows
though they overlap — preventing coherent evidence bodies and making
cross-paper aggregation noisy.

FIX: a per-condition outcome dictionary (``config/outcome_dictionary/<cond>.json``)
maps free-text labels to canonical, patient-important outcomes via a synonym
table. After extraction, every reported symptom/outcome is normalised; unmapped
labels are *logged for human review*, never silently dropped. Evidence bodies
(WP-2) are built per canonical outcome.

The dictionary is configuration, consumed during normalisation only — it does
not feed the synthesis as data (no circularity).
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


def _dict_dir() -> Path:
    """Locate config/outcome_dictionary/ in both dev and PyInstaller bundles
    without importing app_paths (keeps these engines side-effect free)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "config" / "outcome_dictionary"
    return Path(__file__).resolve().parent.parent / "config" / "outcome_dictionary"


_DICT_DIR = _dict_dir()


def _norm_label(label: str) -> str:
    """Normalise a free-text label for lookup: lowercase, collapse whitespace,
    strip surrounding punctuation."""
    s = (label or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = s.strip(" .,:;()[]{}\"'")
    return s


@dataclass
class OutcomeDictionary:
    condition: str
    version: str
    canonical: dict[str, dict]              # id -> {label, patient_important}
    synonyms: dict[str, str]                # normalised label -> canonical id

    def is_canonical(self, outcome_id: str) -> bool:
        return outcome_id in self.canonical

    def patient_important(self, outcome_id: str) -> bool:
        return bool(self.canonical.get(outcome_id, {}).get("patient_important"))

    def normalize(self, label: str) -> str | None:
        """Map one free-text label to a canonical outcome id, or ``None`` if
        unmapped (caller must log it for review — never drop silently)."""
        key = _norm_label(label)
        if not key:
            return None
        if key in self.synonyms:
            return self.synonyms[key]
        # a label that is already a canonical id or canonical label
        if key in self.canonical:
            return key
        for cid, meta in self.canonical.items():
            if _norm_label(meta.get("label", "")) == key:
                return cid
        return None


@dataclass
class NormalisationResult:
    """Outcome of normalising a batch of raw labels."""

    mapping: dict[str, str] = field(default_factory=dict)      # raw label -> canonical id
    unmapped: list[str] = field(default_factory=list)          # the normalisation_review log
    by_canonical: dict[str, list[str]] = field(default_factory=dict)  # canonical -> raw labels


@lru_cache(maxsize=8)
def load_dictionary(condition: str) -> OutcomeDictionary:
    """Load the outcome dictionary for a condition.

    Falls back to ``long_covid`` if the requested condition has no dictionary
    yet (the engine still standardises against the closest available config).
    """
    slug = re.sub(r"[^a-z0-9]+", "_", (condition or "long_covid").strip().lower()).strip("_") or "long_covid"
    path = _DICT_DIR / f"{slug}.json"
    if not path.exists():
        path = _DICT_DIR / "long_covid.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    canonical = {c["id"]: {"label": c.get("label", c["id"]), "patient_important": c.get("patient_important", True)}
                 for c in data.get("canonical_outcomes", [])}
    synonyms = {_norm_label(k): v for k, v in (data.get("synonyms") or {}).items()}
    return OutcomeDictionary(
        condition=data.get("condition", slug),
        version=str(data.get("version", "0")),
        canonical=canonical,
        synonyms=synonyms,
    )


def normalize_outcomes(labels, dictionary: OutcomeDictionary) -> NormalisationResult:
    """Normalise an iterable of raw outcome labels to canonical outcomes.

    Unmapped labels accumulate in ``unmapped`` (the ``normalisation_review``
    log) — they are never discarded.
    """
    result = NormalisationResult()
    for raw in labels:
        canonical = dictionary.normalize(raw)
        if canonical is None:
            if raw not in result.unmapped:
                result.unmapped.append(raw)
            continue
        result.mapping[raw] = canonical
        result.by_canonical.setdefault(canonical, []).append(raw)
    return result
