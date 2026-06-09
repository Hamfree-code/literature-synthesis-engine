"""Retraction screening via Crossref (UPGRADE v3.1 — P2.2).

A paper whose thesis is rigour must not synthesise retracted evidence. Two
defences combine:
  - Phase 1 excludes ``Retracted Publication[pt]`` from the PubMed query by
    default (``INCLUDE_RETRACTED=false``).
  - Phase 4 cross-checks every DOI against Crossref and records retractions so
    Phase 5 can exclude them from the cross-analysis and list them in methods.
"""

from __future__ import annotations

import httpx

_CROSSREF = "https://api.crossref.org/works"
_UA = {"User-Agent": "LitSynthEngine/3.1 (mailto:research@example.com)"}


def _looks_retracted_title(title: str) -> bool:
    t = (title or "").lower()
    return t.startswith("retracted") or t.startswith("retraction") or "(retracted article)" in t


def check_crossref_retraction(doi: str, client: httpx.Client | None = None) -> dict | None:
    """Return retraction metadata for a DOI, or None if not retracted / unknown.

    Output: {"is_retracted": True, "retraction_doi": str|None, "retraction_date": str|None}.
    """
    if not doi:
        return None
    owns_client = client is None
    client = client or httpx.Client(headers=_UA, timeout=15)
    try:
        r = client.get(f"{_CROSSREF}/{doi}")
        if r.status_code != 200:
            return None
        msg = r.json().get("message") or {}
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        if owns_client:
            client.close()

    return parse_crossref_message(msg)


def parse_crossref_message(msg: dict) -> dict | None:
    """Pure function (unit-testable): detect a retraction in a Crossref work."""
    if not msg:
        return None

    # 1. The work itself is a retraction notice / flagged retracted.
    title = (msg.get("title") or [""])[0] if msg.get("title") else ""
    if msg.get("type") == "retraction" or _looks_retracted_title(title):
        return {"is_retracted": True, "retraction_doi": msg.get("DOI"), "retraction_date": _date(msg)}

    # 2. relation.is-retracted-by → another DOI retracts this one.
    relation = msg.get("relation") or {}
    retracted_by = relation.get("is-retracted-by") or relation.get("is-retraction-of")
    if retracted_by:
        first = retracted_by[0] if isinstance(retracted_by, list) else retracted_by
        rdoi = first.get("id") if isinstance(first, dict) else None
        return {"is_retracted": True, "retraction_doi": rdoi, "retraction_date": _date(msg)}

    # 3. update-to with a retraction label (notice pointing back).
    for upd in msg.get("update-to") or []:
        if "retract" in str(upd.get("type", "")).lower() or "retract" in str(upd.get("label", "")).lower():
            return {"is_retracted": True, "retraction_doi": upd.get("DOI"), "retraction_date": _date(msg)}

    return None


def _date(msg: dict) -> str | None:
    parts = ((msg.get("published") or msg.get("issued") or {}).get("date-parts") or [[None]])[0]
    if parts and parts[0]:
        return "-".join(str(p) for p in parts if p is not None)
    return None
