"""Unpaywall full-text fallback (UPGRADE v3.1 — P4.2).

For papers selected for deep extraction that have no PMC OA full text, ask
Unpaywall for a legally-OA PDF and extract its plain text. We only ever follow
URLs Unpaywall itself declares OA — never scrape a publisher. PDF text has no
sec-type structure, so the extraction is tagged ``chunking_mode='flat_pdf'`` for
traceability (finding F11: medRxiv/non-PMC papers previously fell back to the
abstract silently).
"""

from __future__ import annotations

import httpx
from rich.console import Console

from config.settings import settings

console = Console()

_UNPAYWALL = "https://api.unpaywall.org/v2"
_MAX_PDF_CHARS = 120_000  # parity with the legacy flat cap


def best_oa_pdf_url(doi: str, *, email: str = "", client: httpx.Client | None = None) -> str | None:
    """Return the best OA PDF url Unpaywall declares for a DOI, or None."""
    if not doi:
        return None
    email = email or settings.UNPAYWALL_EMAIL or settings.NCBI_EMAIL
    if not email:
        return None
    owns = client is None
    client = client or httpx.Client(timeout=20)
    try:
        r = client.get(f"{_UNPAYWALL}/{doi}", params={"email": email})
        if r.status_code != 200:
            return None
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        if owns:
            client.close()
    loc = data.get("best_oa_location") or {}
    return loc.get("url_for_pdf") or None


def extract_pdf_text(pdf_bytes: bytes) -> str | None:
    """Extract plain text from PDF bytes via pymupdf. None on failure.

    Uses ``get_text("text", sort=True)`` so two-column biomedical papers are
    serialised in natural reading order (top-to-bottom within each column)
    instead of being interleaved horizontally — interleaving corrupts sentences
    and breaks the literal-quote provenance match (Gemini sprint P3)."""
    try:
        import fitz  # pymupdf
    except Exception:
        return None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return None
    parts: list[str] = []
    for page in doc:
        try:
            parts.append(page.get_text("text", sort=True))
        except TypeError:
            parts.append(page.get_text())  # older pymupdf without sort kwarg
        if sum(len(p) for p in parts) > _MAX_PDF_CHARS:
            break
    doc.close()
    text = "\n".join(parts).strip()
    if len(text) > _MAX_PDF_CHARS:
        text = text[:_MAX_PDF_CHARS] + "\n[TRUNCATED]"
    return text or None


def fetch_fulltext_via_unpaywall(
    doi: str, *, email: str = "", client: httpx.Client | None = None
) -> str | None:
    """End-to-end: resolve the OA PDF and return its extracted text, or None.

    Guarded by a circuit breaker (Gemini sprint P2): once Unpaywall has failed
    repeatedly the breaker trips and remaining papers skip the lookup instead of
    hammering a degraded service; the degradation surfaces in the QA sheet via
    ``degraded_services``."""
    if not settings.UNPAYWALL_ENABLED:
        return None
    from utils.resilience import breaker

    cb = breaker("unpaywall", failure_threshold=5)
    if not cb.allow():
        return None
    url = best_oa_pdf_url(doi, email=email, client=client)
    if not url:
        return None
    owns = client is None
    client = client or httpx.Client(timeout=40, follow_redirects=True)
    try:
        r = client.get(url)
        if r.status_code != 200 or not r.content:
            cb.record_failure()
            return None
        cb.record_success()
        return extract_pdf_text(r.content)
    except httpx.HTTPError:
        cb.record_failure()
        return None
    finally:
        if owns:
            client.close()
