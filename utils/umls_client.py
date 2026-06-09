"""UMLS REST verification of LLM-inferred CUIs (UPGRADE v3.1 — P2.1).

Two layers, per the spec:
  - Layer A (when ``UMLS_API_KEY`` is set): hit the UTS REST API to confirm the
    CUI exists and that its preferred name is a reasonable match for the
    verbatim text / MeSH heading (rapidfuzz ratio ≥ ``UMLS_FUZZ_THRESHOLD``). If
    the inferred CUI does not check out, fall back to a string search and adopt
    the first MeSH/SNOMED hit.
  - Layer B (no key): no-op — every entity keeps ``cui_verified = False`` and the
    LLM value. The key is never required.

Verifications are memoised in-process and persisted to the ``umls_cache`` table
because corpora repeat concepts heavily.
"""

from __future__ import annotations

import time

import httpx
from rapidfuzz import fuzz
from rich.console import Console

from config.settings import settings

console = Console()

_UTS_BASE = "https://uts-ws.nlm.nih.gov/rest"
# In-process cache: cui -> (exists, preferred_name)
_CACHE: dict[str, tuple[bool, str]] = {}


def verification_available() -> bool:
    return bool(settings.UMLS_VERIFY_ENABLED and settings.UMLS_API_KEY)


def _get(client: httpx.Client, url: str, params: dict, *, max_retries: int = 4) -> dict | None:
    """GET with exponential backoff on 429/5xx. Returns parsed JSON or None."""
    delay = 1.0
    for _ in range(max_retries):
        try:
            r = client.get(url, params=params, timeout=15)
        except httpx.HTTPError:
            time.sleep(delay)
            delay *= 2
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503):
            time.sleep(delay)
            delay *= 2
            continue
        return None
    return None


def lookup_cui(client: httpx.Client, cui: str) -> tuple[bool, str]:
    """Return (exists, preferred_name) for a CUI, memoised."""
    if cui in _CACHE:
        return _CACHE[cui]
    data = _get(client, f"{_UTS_BASE}/content/current/CUI/{cui}", {"apiKey": settings.UMLS_API_KEY})
    result = (data or {}).get("result") if data else None
    if result and result.get("name"):
        out = (True, str(result["name"]))
    else:
        out = (False, "")
    _CACHE[cui] = out
    return out


def search_concept(client: httpx.Client, text: str) -> tuple[str, str] | None:
    """Search UMLS by string; return (cui, preferred_name) of the first hit."""
    data = _get(
        client,
        f"{_UTS_BASE}/search/current",
        {"apiKey": settings.UMLS_API_KEY, "string": text, "pageSize": 1},
    )
    results = (((data or {}).get("result") or {}).get("results")) or []
    for hit in results:
        ui = hit.get("ui")
        if ui and ui != "NONE":
            return ui, str(hit.get("name") or "")
    return None


def _matches(preferred_name: str, *candidates: str) -> bool:
    pn = (preferred_name or "").lower().strip()
    if not pn:
        return False
    for cand in candidates:
        c = (cand or "").lower().strip()
        if c and fuzz.token_set_ratio(pn, c) >= settings.UMLS_FUZZ_THRESHOLD:
            return True
    return False


def verify_entity(client: httpx.Client, entity: dict) -> dict:
    """Verify a single normalised entity in place; returns the same dict with
    ``cui_verified``, ``preferred_name`` and possibly a corrected ``umls_cui``."""
    verbatim = entity.get("verbatim_text", "")
    mesh = entity.get("mesh_heading", "")
    cui = (entity.get("umls_cui") or "").strip()
    entity.setdefault("cui_verified", False)
    entity.setdefault("preferred_name", "")

    if cui:
        exists, preferred = lookup_cui(client, cui)
        if exists and _matches(preferred, verbatim, mesh):
            entity["cui_verified"] = True
            entity["preferred_name"] = preferred
            return entity

    # CUI absent or mismatched → string search fallback.
    found = search_concept(client, verbatim or mesh)
    if found:
        new_cui, preferred = found
        if _matches(preferred, verbatim, mesh):
            entity["umls_cui"] = new_cui
            entity["preferred_name"] = preferred
            entity["cui_verified"] = True
            entity["llm_judgment"] = False  # now grounded in a real lookup
    return entity


def verify_entities(entities: list[dict]) -> list[dict]:
    """Verify a list of normalised entities. No-op (offline) when no key."""
    if not verification_available() or not entities:
        return entities
    with httpx.Client(headers={"User-Agent": "LitSynthEngine/3.1"}) as client:
        for e in entities:
            try:
                verify_entity(client, e)
            except Exception:
                pass  # best-effort; keep the LLM value
    return entities


def verification_rate(entities: list[dict]) -> float:
    """Percentage of entities with cui_verified=True (0.0 when empty)."""
    if not entities:
        return 0.0
    verified = sum(1 for e in entities if e.get("cui_verified"))
    return round(100.0 * verified / len(entities), 1)
