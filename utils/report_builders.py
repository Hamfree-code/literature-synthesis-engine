"""Enterprise report builders (UPGRADE v3.1 — P6).

Pure, unit-testable functions that turn run aggregates into the artefacts a
pharma/consulting client recognises:
  - PRISMA 2020 flow diagram (hand-templated SVG — no heavy deps),
  - GRADE Summary-of-Findings table (Markdown),
  - QA sheet / run quality certificate (Markdown),
  - Executive one-pager (Markdown).

All honest-framing strings live here so every deliverable says the same thing.
"""

from __future__ import annotations

HONEST_FRAMING = (
    "PRISMA-conformant reporting of an AI-assisted evidence mapping. "
    "This is not a registered systematic review (no protocol pre-registration, "
    "no human dual screening)."
)

LEGAL_NOTICE = (
    "This document is an AI-assisted evidence synthesis and does not constitute "
    "medical, clinical, regulatory or investment advice."
)


def prisma_flow_svg(counts: dict) -> str:
    """Render a PRISMA 2020 flow diagram as a standalone SVG string.

    Expected keys (missing → 0): identified_pmc, identified_openalex,
    identified_medrxiv, duplicates_removed, screened, excluded_screen,
    eligible, included_deep, excluded_retracted.
    """
    g = lambda k: int(counts.get(k, 0) or 0)  # noqa: E731
    id_pmc, id_oa, id_mr = g("identified_pmc"), g("identified_openalex"), g("identified_medrxiv")
    total_id = id_pmc + id_oa + id_mr
    dups = g("duplicates_removed")
    screened = g("screened")
    excl_screen = g("excluded_screen")
    eligible = g("eligible")
    included = g("included_deep")
    retracted = g("excluded_retracted")

    def box(x, y, w, h, lines, fill="#f7f7f5"):
        text = "".join(
            f'<text x="{x + 10}" y="{y + 22 + i * 16}" font-size="12" font-family="sans-serif">{ln}</text>'
            for i, ln in enumerate(lines)
        )
        return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="{fill}" stroke="#888"/>{text}'

    def arrow(x1, y1, x2, y2):
        return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#555" marker-end="url(#a)"/>'

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="560" viewBox="0 0 640 560">',
        '<defs><marker id="a" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">'
        '<path d="M0,0 L0,6 L9,3 z" fill="#555"/></marker></defs>',
        '<text x="20" y="24" font-size="15" font-weight="bold" font-family="sans-serif">'
        "PRISMA 2020 flow (AI-assisted evidence mapping)</text>",
        box(
            40,
            50,
            560,
            78,
            [
                f"Records identified (n = {total_id}):",
                f"PMC = {id_pmc}  ·  OpenAlex = {id_oa}  ·  medRxiv = {id_mr}",
                f"Duplicates removed: {dups}",
            ],
            fill="#eef2ff",
        ),
        box(40, 170, 560, 40, [f"Records screened (triage): n = {screened}"]),
        box(40, 250, 560, 40, [f"Excluded at screening: n = {excl_screen}"], fill="#fef2f2"),
        box(40, 330, 560, 40, [f"Eligible for deep extraction: n = {eligible}"]),
        box(40, 410, 560, 40, [f"Excluded — retracted: n = {retracted}"], fill="#fef2f2"),
        box(40, 490, 560, 44, [f"Included in synthesis (deep-extracted): n = {included}"], fill="#ecfdf5"),
        arrow(320, 128, 320, 170),
        arrow(320, 210, 320, 330),
        arrow(320, 290, 320, 330),
        arrow(320, 370, 320, 490),
        arrow(320, 450, 320, 490),
        "</svg>",
    ]
    return "".join(parts)


def grade_sof_table(meta_by_factor: dict, min_studies: int = 2) -> str:
    """GRADE Summary-of-Findings as a Markdown table. One row per factor with
    ≥ min_studies. Effect/CI/I² carry the [CALC] badge."""
    rows = []
    for factor, res in (meta_by_factor or {}).items():
        n = res.get("n_studies") or 0
        if n < min_studies:
            continue
        pooled = res.get("pooled_r")
        ci = res.get("ci") or [None, None]
        i2 = res.get("i_squared")
        pb = (res.get("publication_bias") or {}).get("publication_bias_risk", "n/a")
        downgrades = []
        if i2 is not None and i2 >= 75:
            downgrades.append("inconsistency")
        if pb == "high":
            downgrades.append("publication bias")
        if n < 5:
            downgrades.append("imprecision")
        certainty = "High"
        if len(downgrades) == 1:
            certainty = "Moderate"
        elif len(downgrades) == 2:
            certainty = "Low"
        elif len(downgrades) >= 3:
            certainty = "Very Low"
        pooled_s = f"{pooled:.3f}" if isinstance(pooled, (int, float)) else "n/a"
        ci_s = f"[{ci[0]:.2f}, {ci[1]:.2f}]" if ci and ci[0] is not None and ci[1] is not None else "n/a"
        i2_s = f"{i2:.0f}%" if isinstance(i2, (int, float)) else "n/a"
        reasons = ", ".join(downgrades) if downgrades else "none"
        rows.append(f"| {factor} | {n} | {pooled_s} | {ci_s} | {i2_s} | {certainty} | {reasons} |")

    if not rows:
        return "_No outcome had ≥2 pooled studies; a GRADE Summary of Findings table is not reported._"

    header = (
        "| Outcome / factor | Studies | Pooled r <sup>[CALC]</sup> | 95% CI <sup>[CALC]</sup> "
        "| I² <sup>[CALC]</sup> | GRADE certainty | Downgrade reasons |\n"
        "|---|---|---|---|---|---|---|"
    )
    return header + "\n" + "\n".join(rows)


def qa_sheet_markdown(qa: dict) -> str:
    """Run quality certificate (the formalised 'Methodology at a Glance')."""

    def row(label, value):
        return f"| {label} | {value} |"

    lines = [
        "## Run Quality Certificate <sup>[CALC]</sup>",
        "",
        f"**Run ID:** `{qa.get('run_id', 'n/a')}`  ·  **Engine:** {qa.get('engine_version', 'n/a')}",
        "",
        "| Metric | Value |",
        "|---|---|",
        row("Deep extraction success rate", f"{qa.get('deep_success_rate', 'n/a')}%"),
        row("CUIs verified (UMLS)", f"{qa.get('cui_verified_pct', 'n/a')}%"),
        row("Arbiter reconciliations triggered", qa.get("reconciliations", "n/a")),
        row("Kappa vs human (if any)", qa.get("kappa_summary", "not yet rated")),
        row("Retracted papers excluded", qa.get("n_retracted_excluded", 0)),
        row("Retraction screen", _retraction_label(qa)),
        row("Full-text coverage", f"{qa.get('fulltext_coverage_pct', 'n/a')}%"),
        row("Sources breakdown", qa.get("sources_breakdown", "n/a")),
        row("Degraded services", ", ".join(qa.get("degraded_services") or []) or "none"),
        row("API cost (measured)", f"${qa.get('api_cost_usd', 'n/a')}"),
        row("Runtime", f"{qa.get('runtime_seconds', 'n/a')} s"),
        "",
        f"_{HONEST_FRAMING}_",
    ]
    return "\n".join(lines)


def _retraction_label(qa: dict) -> str:
    complete = qa.get("retraction_screen_complete")
    if complete is True:
        return "complete (all DOIs checked)"
    if complete is False:
        n = qa.get("retraction_checks_failed", 0)
        return f"⚠ INCOMPLETE — {n} DOI(s) could not be verified (treat as a gap)"
    return "not run"


def one_pager_markdown(topic_title: str, exec_data: dict, qa: dict, search_date: str) -> str:
    """C-level one-pager: headline, 3 evidence bullets w/ GRADE, 1 caveat, mini-QA."""
    headline = exec_data.get("headline") or exec_data.get("main_finding") or "See full report."
    bullets = exec_data.get("key_points") or exec_data.get("evidence_bullets") or []
    caveat = (
        exec_data.get("limitation")
        or exec_data.get("caveat")
        or ("Evidence is observational; interpret associations cautiously.")
    )
    lines = [
        f"# {topic_title} — Executive One-Pager",
        f"*Evidence current as of {search_date}. Run ID `{qa.get('run_id', 'n/a')}`.*",
        "",
        "## Principal finding",
        str(headline),
        "",
        "## Evidence at a glance",
    ]
    for b in (bullets or ["See full report for detailed findings."])[:3]:
        lines.append(f"- {b}")
    lines += [
        "",
        "## Key limitation",
        str(caveat),
        "",
        "## Quality snapshot",
        f"- Deep success: {qa.get('deep_success_rate', 'n/a')}% · "
        f"CUIs verified: {qa.get('cui_verified_pct', 'n/a')}% · "
        f"Retracted excluded: {qa.get('n_retracted_excluded', 0)}",
        "",
        f"> {LEGAL_NOTICE}",
    ]
    return "\n".join(lines)


def legal_page_markdown(topic_title: str, search_date: str, run_id: str) -> str:
    return "\n".join(
        [
            "## Scope of Use & Legal Notice",
            "",
            LEGAL_NOTICE,
            "",
            f"**Method:** {HONEST_FRAMING}",
            "",
            f"**Evidence current as of {search_date}.** Re-run to refresh as new literature is published.",
            "",
            f"**Run ID (verifiable):** `{run_id}`",
            "",
            "© Hams & Co. Research Division. Confidential — for the intended recipient only.",
        ]
    )
