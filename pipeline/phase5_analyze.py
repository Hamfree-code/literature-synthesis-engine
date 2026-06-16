"""Phase 5: Cross-analysis — calibration layer + consensus + Sonnet synthesis (v2)."""
from __future__ import annotations
# __APP_PATHS_INSTALLED__
from app_paths import app_data, resource

import json
from collections import Counter, defaultdict
from enum import Enum
from pathlib import Path

import anthropic
import numpy as np
from rich.console import Console

from config.settings import settings
from methodology import integration as v32
from methodology import output_ceiling as oc
from methodology import reconciliation as rc
from utils.checkpointing import Checkpoint

console = Console()
client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

SYNTHESIS_PROMPT = resource("config/prompts/synthesis_sonnet.txt").read_text(encoding="utf-8")
DUE_DILIGENCE_PROMPT = resource("config/prompts/due_diligence_sonnet.txt").read_text(encoding="utf-8")
EXECUTIVE_SUMMARY_PROMPT = resource("config/prompts/executive_summary_sonnet.txt").read_text(encoding="utf-8")
HETEROGENEITY_PROMPT = resource("config/prompts/heterogeneity_analysis.txt").read_text(encoding="utf-8")


# ============================================================
# Methodological synthesis — emulating Siciliano et al. 2024
# (QUADAS filter, random-effects pooling, leave-one-out, publication bias)
# ============================================================

def _fisher_z(r: float) -> float:
    """Fisher z transform of a correlation coefficient."""
    r = max(-0.9999, min(0.9999, r))
    return 0.5 * np.log((1 + r) / (1 - r))


def _inverse_z(z: float) -> float:
    e2z = np.exp(2 * z)
    return (e2z - 1) / (e2z + 1)


def _pool_random_effects(effects: list[float], variances: list[float]) -> dict:
    """Random-effects pooling via inverse-variance weighting with DerSimonian–Laird tau²."""
    if not effects:
        return {"pooled": None, "se": None, "ci_low": None, "ci_high": None, "i_squared": None, "q": None, "tau_squared": None, "n_studies": 0}
    eff = np.array(effects, dtype=float)
    var = np.array(variances, dtype=float)
    var = np.where(var <= 0, 1e-6, var)
    w_fe = 1.0 / var
    pooled_fe = np.sum(w_fe * eff) / np.sum(w_fe)
    q = float(np.sum(w_fe * (eff - pooled_fe) ** 2))
    df = len(eff) - 1
    if df < 1:
        tau2 = 0.0
        i2 = 0.0
    else:
        c = np.sum(w_fe) - np.sum(w_fe ** 2) / np.sum(w_fe)
        tau2 = max(0.0, (q - df) / c) if c > 0 else 0.0
        i2 = max(0.0, (q - df) / q) * 100.0 if q > 0 else 0.0
    w_re = 1.0 / (var + tau2)
    pooled = float(np.sum(w_re * eff) / np.sum(w_re))
    se = float(np.sqrt(1.0 / np.sum(w_re)))
    return {
        "pooled": pooled,
        "se": se,
        "ci_low": pooled - 1.96 * se,
        "ci_high": pooled + 1.96 * se,
        "i_squared": float(i2),
        "q": q,
        "tau_squared": float(tau2),
        "n_studies": len(eff),
        "weights_re": w_re.tolist(),
    }


def select_model(i_squared: float | None) -> str:
    """Mirror of the spec: thresholds drive which model is appropriate."""
    if i_squared is None:
        return "insufficient_data"
    if i_squared >= settings.HETEROGENEITY_CRITICAL_THRESHOLD:
        return "random_effects_critical"
    if i_squared >= settings.HETEROGENEITY_HIGH_THRESHOLD:
        return "random_effects_mandatory"
    if i_squared >= settings.HETEROGENEITY_LOW_THRESHOLD:
        return "random_effects_recommended"
    return "fixed_effects"


def leave_one_out_analysis(effects: list[float], variances: list[float], paper_ids: list[str]) -> dict:
    """For each study, re-pool excluding it. Flag papers that shift the pooled estimate ≥ threshold."""
    full = _pool_random_effects(effects, variances)
    if full["pooled"] is None or len(effects) < 3:
        return {"stable": True, "influential_papers": [], "range_without_influential": None, "full_pooled": full["pooled"]}
    pooled = full["pooled"]
    threshold = settings.LEAVE_ONE_OUT_INFLUENCE_THRESHOLD
    influential: list[str] = []
    excluded_pooled_values: list[float] = []
    for i, pid in enumerate(paper_ids):
        sub_eff = effects[:i] + effects[i + 1:]
        sub_var = variances[:i] + variances[i + 1:]
        res = _pool_random_effects(sub_eff, sub_var)
        if res["pooled"] is None:
            continue
        excluded_pooled_values.append(res["pooled"])
        if abs(res["pooled"] - pooled) / max(abs(pooled), 1e-6) >= threshold:
            influential.append(pid)
    non_influential_mask = [pid not in influential for pid in paper_ids]
    if any(non_influential_mask):
        keep_eff = [e for e, k in zip(effects, non_influential_mask) if k]
        keep_var = [v for v, k in zip(variances, non_influential_mask) if k]
        adjusted = _pool_random_effects(keep_eff, keep_var) if keep_eff else None
    else:
        adjusted = None
    return {
        "stable": len(influential) == 0,
        "influential_papers": influential,
        "range_without_influential": [min(excluded_pooled_values), max(excluded_pooled_values)] if excluded_pooled_values else None,
        "full_pooled": pooled,
        "adjusted_pooled": adjusted["pooled"] if adjusted else None,
    }


def assess_publication_bias(effects: list[float], variances: list[float]) -> dict:
    """Approximate funnel symmetry + Egger's regression + trim-and-fill."""
    n = len(effects)
    if n < settings.MIN_STUDIES_PUBLICATION_BIAS:
        return {
            "funnel_symmetry": "insufficient_data",
            "egger_p": None,
            "estimated_missing_studies": 0,
            "adjusted_effect_size": None,
            "publication_bias_risk": "insufficient_data",
            "n_studies": n,
        }
    eff = np.array(effects, dtype=float)
    var = np.array(variances, dtype=float)
    se = np.sqrt(var)
    # Egger's regression: regress standardized effect on precision (1/SE) — approximate
    precision = 1.0 / np.where(se <= 0, 1e-6, se)
    standardized = eff / np.where(se <= 0, 1e-6, se)
    # Simple OLS slope + intercept
    x_mean = float(np.mean(precision))
    y_mean = float(np.mean(standardized))
    cov_xy = float(np.mean((precision - x_mean) * (standardized - y_mean)))
    var_x = float(np.mean((precision - x_mean) ** 2))
    slope = cov_xy / var_x if var_x > 0 else 0.0
    intercept = y_mean - slope * x_mean
    # Egger's p approximated from t-stat on intercept
    residuals = standardized - (slope * precision + intercept)
    s2 = float(np.sum(residuals ** 2) / max(1, n - 2))
    se_intercept = float(np.sqrt(s2 * (1.0 / n + (x_mean ** 2) / (n * var_x)))) if var_x > 0 else float("inf")
    t_stat = intercept / se_intercept if se_intercept > 0 else 0.0
    # Two-sided p from normal approximation (n usually ≥ 10)
    from math import erf, sqrt
    p_value = 2.0 * (1.0 - 0.5 * (1.0 + erf(abs(t_stat) / sqrt(2))))
    # Funnel symmetry classification
    if p_value < 0.05 and abs(intercept) > 0.5:
        symmetry = "asymmetric"
    else:
        symmetry = "symmetric"
    # Trim-and-fill: estimate missing studies as the count of the longest run of one-sided outliers
    median_eff = float(np.median(eff))
    above = int(np.sum(eff > median_eff))
    below = int(np.sum(eff < median_eff))
    missing = max(0, abs(above - below))
    # Adjusted effect: mean of effects assuming the "missing" side is mirrored from the larger side
    if missing > 0 and symmetry == "asymmetric":
        if above > below:
            mirrored = list(eff) + [(2 * median_eff - e) for e in sorted(eff)[-missing:]]
        else:
            mirrored = list(eff) + [(2 * median_eff - e) for e in sorted(eff)[:missing]]
        adjusted = float(np.mean(mirrored))
    else:
        adjusted = float(np.mean(eff))
    if symmetry == "asymmetric" and missing >= 3:
        risk = "high"
    elif symmetry == "asymmetric":
        risk = "moderate"
    else:
        risk = "low"
    return {
        "funnel_symmetry": symmetry,
        "egger_p": float(p_value),
        "egger_intercept": float(intercept),
        "estimated_missing_studies": int(missing),
        "adjusted_effect_size": float(adjusted),
        "publication_bias_risk": risk,
        "n_studies": n,
    }


def collect_quadas_scores(deep_path: Path) -> list[dict]:
    """Extract per-paper QUADAS totals from the deep_results JSONL."""
    out = []
    if not deep_path.exists():
        return out
    for line in deep_path.open(encoding="utf-8"):
        d = json.loads(line)
        qa = d.get("quality_assessment") or {}
        total = qa.get("quadas_total")
        if total is None:
            # fallback: try to compute from sub-blocks
            subblocks = [
                qa.get("quadas_patient_selection") or {},
                qa.get("quadas_index_test") or {},
                qa.get("quadas_reference_standard") or {},
                qa.get("quadas_flow_timing") or {},
                qa.get("quadas_reporting") or {},
            ]
            total = sum(
                v for blk in subblocks for v in blk.values() if isinstance(v, (int, float))
            ) if any(subblocks) else None
        out.append({
            "paper_id": d.get("paper_id"),
            "quadas_total": int(total) if total is not None else None,
            "acceptable": (int(total) > settings.QUADAS_CUTOFF) if total is not None else None,
            "raw": qa,
        })
    return out


def collect_effect_sizes(deep_path: Path) -> list[dict]:
    """Extract per-paper classified effect sizes for meta-analytic pooling."""
    out = []
    if not deep_path.exists():
        return out
    for line in deep_path.open(encoding="utf-8"):
        d = json.loads(line)
        pid = d.get("paper_id")
        n = (d.get("study_metadata") or {}).get("sample_size") or 0
        # variance approximation: Var(r) ≈ (1 - r²)² / (n - 1)
        for es in d.get("effect_sizes_classified") or []:
            r = es.get("r_equivalent")
            factor = es.get("factor")
            if r is None or factor is None or n < 5:
                continue
            try:
                r = float(r)
            except (TypeError, ValueError):
                continue
            var = ((1 - r ** 2) ** 2) / max(1, n - 1)
            out.append({
                "paper_id": pid,
                "factor": str(factor),
                "r": r,
                "variance": var,
                "n": n,
                "magnitude": es.get("magnitude"),
            })
    return out


def collect_moderators(deep_path: Path) -> list[dict]:
    """Per-paper moderators block, defaulting fields to null when missing."""
    out = []
    if not deep_path.exists():
        return out
    for line in deep_path.open(encoding="utf-8"):
        d = json.loads(line)
        mod = d.get("moderators") or {}
        out.append({"paper_id": d.get("paper_id"), **mod})
    return out


def meta_analyze_by_factor(effect_rows: list[dict], qaccept_ids: set[str] | None = None) -> dict:
    """Group effect sizes by factor name and run RE pooling + leave-one-out + publication bias.

    qaccept_ids: optional set of paper_ids that pass QUADAS. If provided, only those contribute.
    """
    by_factor: dict[str, list[dict]] = defaultdict(list)
    for row in effect_rows:
        if qaccept_ids is not None and row["paper_id"] not in qaccept_ids:
            continue
        by_factor[row["factor"]].append(row)

    results: dict[str, dict] = {}
    for factor, rows in by_factor.items():
        if len(rows) < 2:
            continue
        effects = [r["r"] for r in rows]
        variances = [r["variance"] for r in rows]
        paper_ids = [r["paper_id"] for r in rows]
        pool = _pool_random_effects(effects, variances)
        loo = leave_one_out_analysis(effects, variances, paper_ids)
        pub_bias = assess_publication_bias(effects, variances)
        results[factor] = {
            "pooled": pool,
            "model": select_model(pool["i_squared"]),
            "leave_one_out": loo,
            "publication_bias": pub_bias,
            "per_study": rows,
        }
    return results


def heterogeneity_critical_synthesis(factor: str, factor_result: dict, moderators_by_paper: dict[str, dict]) -> dict:
    """Call Sonnet for moderator analysis when I² ≥ 90%."""
    pool = factor_result["pooled"]
    studies = factor_result["per_study"]
    if pool["i_squared"] is None or pool["i_squared"] < settings.HETEROGENEITY_CRITICAL_THRESHOLD:
        return {}
    payload = []
    for s in studies:
        m = moderators_by_paper.get(s["paper_id"], {})
        payload.append({"paper_id": s["paper_id"], "r": s["r"], "n": s["n"], **{k: v for k, v in m.items() if k != "paper_id"}})

    prompt = (
        HETEROGENEITY_PROMPT
        .replace("{i_squared}", f"{pool['i_squared']:.1f}")
        .replace("{outcome}", factor)
        .replace("{n_studies}", str(len(studies)))
        .replace("{moderators_json}", json.dumps(payload, indent=2))
    )
    try:
        response = client.messages.create(
            model=settings.ANTHROPIC_SONNET_MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                return json.loads(raw[start:end + 1])
            return {"_parse_failed": True, "_raw": raw[:500]}
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def forest_plot_text(factor: str, factor_result: dict) -> str:
    """Render a text forest plot for I² ≥ 90% outcomes, per the spec."""
    pool = factor_result["pooled"]
    studies = factor_result["per_study"]
    loo = factor_result["leave_one_out"]
    influential = set(loo.get("influential_papers") or [])
    weights = pool.get("weights_re") or [1.0 / len(studies)] * len(studies)
    total_w = sum(weights) or 1.0
    lines = []
    title = f"FOREST PLOT — {factor} (Random Effects, I²={pool['i_squared']:.1f}%)"
    lines.append("═" * max(70, len(title)))
    lines.append(title)
    lines.append("═" * max(70, len(title)))
    lines.append(f"{'Study':<28} {'n':>6} {'r':>8}   {'95% CI':>16}    {'Weight':>7}")
    lines.append("─" * 75)
    for s, w in zip(studies, weights):
        pid_short = (s["paper_id"] or "?")[:24]
        star = "★ " if s["paper_id"] in influential else "  "
        lo = s["r"] - 1.96 * np.sqrt(s["variance"])
        hi = s["r"] + 1.96 * np.sqrt(s["variance"])
        pct = 100.0 * w / total_w
        lines.append(f"{star}{pid_short:<26} {s['n']:>6} {s['r']:>8.3f}   [{lo:>5.2f}, {hi:>5.2f}]   {pct:>6.1f}%")
    lines.append("─" * 75)
    lines.append(
        f"{'POOLED (RE)':<28} {pool['n_studies']:>6} {pool['pooled']:>8.3f}   "
        f"[{pool['ci_low']:>5.2f}, {pool['ci_high']:>5.2f}]   100.0%"
    )
    lines.append("─" * 75)
    lines.append(
        f"Heterogeneity: Q={pool['q']:.2f}, df={pool['n_studies'] - 1}; "
        f"I²={pool['i_squared']:.1f}%; τ²={pool['tau_squared']:.4f}"
    )
    lines.append("Model: Random effects, inverse variance method (DerSimonian–Laird)")
    if influential:
        lines.append(f"★ = Influential study (leave-one-out shifts pooled by ≥{int(settings.LEAVE_ONE_OUT_INFLUENCE_THRESHOLD * 100)}%)")
    lines.append("WARNING: I² ≥ 90% — interpret pooled estimate with caution. See moderator analysis below.")
    lines.append("═" * max(70, len(title)))
    return "\n".join(lines)


class CalibratedCertainty(str, Enum):
    ESTABLISHED = "established"
    PROBABLE = "probable"
    POSSIBLE = "possible"
    SPECULATIVE = "speculative"
    CONTRADICTED = "contradicted"


CERTAINTY_LANGUAGE = {
    CalibratedCertainty.ESTABLISHED: "Evidence establishes",
    CalibratedCertainty.PROBABLE: "Evidence suggests",
    CalibratedCertainty.POSSIBLE: "Limited evidence indicates",
    CalibratedCertainty.SPECULATIVE: "Preliminary findings hint",
    CalibratedCertainty.CONTRADICTED: "Contradictory evidence prevents conclusions about",
}


def compute_symptom_consensus(triage_path: Path) -> dict:
    counts: Counter[str] = Counter()
    total = 0
    for line in triage_path.open(encoding="utf-8"):
        data = json.loads(line)
        syms = data.get("main_symptoms") or []
        if syms:
            total += 1
            for s in syms:
                counts[s.lower().strip()] += 1
    return {s: {"count": c, "pct": round(c / total * 100, 1) if total else 0} for s, c in counts.most_common(30)}


def compute_definition_heterogeneity(triage_path: Path) -> dict:
    w: Counter[int] = Counter()
    for line in triage_path.open(encoding="utf-8"):
        data = json.loads(line)
        val = data.get("long_covid_definition_weeks")
        if val is not None:
            w[val] += 1
    return dict(w.most_common())


def compute_study_design_distribution(triage_path: Path) -> dict:
    d: Counter[str] = Counter()
    for line in triage_path.open(encoding="utf-8"):
        data = json.loads(line)
        v = data.get("study_design")
        if v:
            d[v] += 1
    return dict(d.most_common())


def aggregate_inferred_limitations(deep_path: Path) -> list[dict]:
    items = []
    for line in deep_path.open(encoding="utf-8"):
        d = json.loads(line)
        pid = d.get("paper_id")
        ma = d.get("methodology_appraisal") or {}
        for lim in ma.get("limitations_inferred") or []:
            items.append({"paper_id": pid, "limitation": lim})
    return items


def compute_methodology_quality(deep_path: Path) -> dict:
    grade: Counter[str] = Counter()
    nos_scores: list[int] = []
    bias_counts = {
        "surveillance_bias": 0,
        "baseline_absence": 0,
        "self_report_bias": 0,
        "variant_vaccine_confounding": 0,
        "selection_bias": 0,
        "circular_case_definition": 0,
    }
    phenotypes: Counter[str] = Counter()
    n = 0
    for line in deep_path.open(encoding="utf-8"):
        d = json.loads(line)
        n += 1
        ma = d.get("methodology_appraisal") or {}
        g = ma.get("grade_certainty")
        if g:
            grade[g] += 1
        if ma.get("nos_score") is not None:
            try:
                nos_scores.append(int(ma["nos_score"]))
            except (TypeError, ValueError):
                pass
        ba = d.get("bias_audit") or {}
        for key in bias_counts:
            if ba.get(key) is True:
                bias_counts[key] += 1
        pm = d.get("phenotype_mapping") or {}
        prim = pm.get("primary_mechanism")
        if prim:
            phenotypes[prim] += 1
        for sec in pm.get("secondary_mechanisms") or []:
            if isinstance(sec, str):
                phenotypes[sec] += 1
            elif isinstance(sec, dict):
                k = sec.get("phenotype") or sec.get("mechanism") or sec.get("name")
                if k:
                    phenotypes[k] += 1
    return {
        "n_deep": n,
        "grade_distribution": dict(grade),
        "nos_mean": round(sum(nos_scores) / len(nos_scores), 2) if nos_scores else None,
        "bias_audit_counts": bias_counts,
        "phenotype_counts": dict(phenotypes),
    }


def propagate_uncertainty(deep_extractions: list[dict]) -> dict:
    """Aggregate per-paper calibrated certainty into cross-paper symptom-level consensus."""
    symptom_data: dict[str, list[dict]] = defaultdict(list)
    for ext in deep_extractions:
        cal = ext.get("calibration") or {}
        certainty = cal.get("calibrated_certainty", "possible")
        conf = cal.get("extraction_confidence", 0.5)
        fx = ext.get("factual_extraction") or {}
        symptoms_prev = fx.get("symptoms_prevalence") or {}
        for symptom in symptoms_prev.keys():
            symptom_data[symptom.lower().strip()].append({"certainty": certainty, "confidence": conf})

    results = {}
    for symptom, entries in symptom_data.items():
        n = len(entries)
        mean_conf = float(np.mean([e["confidence"] or 0.5 for e in entries]))
        certainty_counts = {c.value: 0 for c in CalibratedCertainty}
        for e in entries:
            c = e["certainty"]
            if c in certainty_counts:
                certainty_counts[c] += 1

        if certainty_counts["contradicted"] > 0:
            consensus = CalibratedCertainty.CONTRADICTED
        elif n >= 5 and (certainty_counts["established"] + certainty_counts["probable"]) / n >= 0.6:
            if certainty_counts["established"] / n >= 0.4:
                consensus = CalibratedCertainty.ESTABLISHED
            else:
                consensus = CalibratedCertainty.PROBABLE
        elif n >= 2:
            consensus = CalibratedCertainty.POSSIBLE
        else:
            consensus = CalibratedCertainty.SPECULATIVE

        prefix = CERTAINTY_LANGUAGE[consensus]
        statement = f"{prefix} that {symptom} is a Long COVID manifestation (extraction confidence: {mean_conf:.2f}, n={n})."

        results[symptom] = {
            "n_papers": n,
            "mean_extraction_confidence": round(mean_conf, 3),
            "certainty_distribution": certainty_counts,
            "consensus_certainty": consensus.value,
            "consensus_statement": statement,
        }
    return results


def build_uncertainty_report_section(consensus_data: dict) -> dict:
    by_certainty = {c.value: [] for c in CalibratedCertainty}
    for symptom, data in consensus_data.items():
        by_certainty[data["consensus_certainty"]].append({"symptom": symptom, **data})
    for level in by_certainty:
        by_certainty[level].sort(
            key=lambda x: (x["n_papers"], x["mean_extraction_confidence"]),
            reverse=True,
        )
    return by_certainty


def _slim_deep(d: dict) -> dict:
    """Ultra-slim per-paper dict for Sonnet synthesis input. ~600 chars per entry."""
    sm = d.get("study_metadata") or {}
    ma = d.get("methodology_appraisal") or {}
    cal = d.get("calibration") or {}
    fx = d.get("factual_extraction") or {}
    return {
        "paper_id": d.get("paper_id"),
        "title": (sm.get("title") or "")[:120],
        "design": sm.get("design"),
        "n": sm.get("sample_size"),
        "grade": ma.get("grade_certainty"),
        "calibrated_certainty": cal.get("calibrated_certainty"),
        "key_findings": [str(x)[:120] for x in (fx.get("key_findings") or [])[:2]],
        "limitations_inferred": [str(x)[:120] for x in (ma.get("limitations_inferred") or [])[:2]],
    }


def _slim_aggregates(agg: dict, max_consensus_entries: int = 50) -> dict:
    """Cap large per-paper arrays before sending to Sonnet."""
    slim: dict = {}
    for k in ("n_papers", "consensus", "definition_heterogeneity", "study_designs", "methodology_quality"):
        if k in agg:
            slim[k] = agg[k]

    raw = agg.get("inferred_limitations_raw") or []
    if len(raw) > 80:
        slim["inferred_limitations_sample"] = raw[:80]
        slim["inferred_limitations_total_count"] = len(raw)
    else:
        slim["inferred_limitations_sample"] = raw

    cc = agg.get("calibrated_consensus") or {}
    if cc:
        top = sorted(cc.items(), key=lambda x: x[1]["n_papers"], reverse=True)[:max_consensus_entries]
        slim["calibrated_consensus_top"] = {
            sym: {
                "n_papers": d["n_papers"],
                "mean_extraction_confidence": d["mean_extraction_confidence"],
                "consensus_certainty": d["consensus_certainty"],
            }
            for sym, d in top
        }
        slim["calibrated_consensus_total"] = len(cc)

    fbc = agg.get("findings_by_certainty") or {}
    if fbc:
        slim["findings_by_certainty_counts"] = {k: len(v) for k, v in fbc.items()}

    return slim


def call_synthesizer(aggregates: dict, deep_path: Path, triage_path: Path) -> dict:
    deep_full = [json.loads(l) for l in deep_path.open(encoding="utf-8")] if deep_path.exists() else []
    triage_full = [json.loads(l) for l in triage_path.open(encoding="utf-8")] if triage_path.exists() else []
    deep_slim = [_slim_deep(d) for d in deep_full]
    agg_slim = _slim_aggregates(aggregates)

    from utils.run_context import topic_title, topic_lower
    prompt = (
        SYNTHESIS_PROMPT
        .replace("{topic_title}", topic_title())
        .replace("{topic}", topic_lower())
        .replace("{n_papers}", str(len(triage_full)))
        .replace("{n_deep}", str(len(deep_full)))
        .replace("{aggregates}", json.dumps(agg_slim, indent=2))
        .replace("{deep_extractions}", json.dumps(deep_slim, indent=2))
        .replace("{triage_extractions}", "(triage row-level data omitted; see aggregates.consensus / definition_heterogeneity / study_designs for triage-derived stats)")
    )

    console.print(f"Calling Sonnet for synthesis ({len(prompt)} chars input)...")
    response = client.messages.create(
        model=settings.ANTHROPIC_SONNET_MODEL,
        max_tokens=16384,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text
    console.print(f"Synthesis tokens: input={response.usage.input_tokens}, output={response.usage.output_tokens}, stop_reason={response.stop_reason}")

    raw_path = app_data("data/filtered/synthesis_raw.txt")
    raw_path.write_text(raw, encoding="utf-8")

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        console.print(f"[yellow]Strict JSON parse failed: {e}. Trying fallback...[/]")
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError as e2:
                console.print(f"[red]Fallback parse also failed: {e2}. Check synthesis_raw.txt[/]")
        return {}


def call_due_diligence(aggregates: dict, deep_path: Path) -> dict:
    deep_full = [json.loads(l) for l in deep_path.open(encoding="utf-8")] if deep_path.exists() else []
    deep_slim = [_slim_deep(d) for d in deep_full]
    agg_slim = _slim_aggregates(aggregates)

    from utils.run_context import topic_title, topic_lower
    prompt = (
        DUE_DILIGENCE_PROMPT
        .replace("{topic_title}", topic_title())
        .replace("{topic}", topic_lower())
        .replace("{n_deep}", str(len(deep_full)))
        .replace("{aggregates}", json.dumps(agg_slim, indent=2))
        .replace("{deep_extractions}", json.dumps(deep_slim, indent=2))
    )

    console.print(f"Calling Sonnet for due diligence brief ({len(prompt)} chars input)...")
    response = client.messages.create(
        model=settings.ANTHROPIC_SONNET_MODEL,
        max_tokens=16384,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text
    console.print(f"Due diligence tokens: input={response.usage.input_tokens}, output={response.usage.output_tokens}, stop_reason={response.stop_reason}")

    app_data("data/filtered/due_diligence_raw.txt").write_text(raw, encoding="utf-8")

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        console.print(f"[yellow]Due diligence strict JSON parse failed: {e}. Trying fallback...[/]")
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError as e2:
                console.print(f"[red]Due diligence fallback parse failed: {e2}[/]")
        return {}


def call_executive_summary(aggregates: dict, deep_path: Path) -> dict:
    """Third Sonnet pass: a 2-page non-technical exec summary for non-scientist readers."""
    deep_full = [json.loads(l) for l in deep_path.open(encoding="utf-8")] if deep_path.exists() else []
    deep_slim = [_slim_deep(d) for d in deep_full]
    agg_slim = _slim_aggregates(aggregates)

    from utils.run_context import topic_title, topic_lower
    prompt = (
        EXECUTIVE_SUMMARY_PROMPT
        .replace("{topic_title}", topic_title())
        .replace("{topic}", topic_lower())
        .replace("{n_deep}", str(len(deep_full)))
        .replace("{aggregates}", json.dumps(agg_slim, indent=2))
        .replace("{deep_extractions}", json.dumps(deep_slim, indent=2))
    )

    console.print(f"Calling Sonnet for executive summary ({len(prompt)} chars input)...")
    try:
        response = client.messages.create(
            model=settings.ANTHROPIC_SONNET_MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        console.print(f"[red]Executive summary call failed: {type(e).__name__}: {e}[/]")
        return {"_error": f"{type(e).__name__}: {e}"}
    raw = response.content[0].text
    console.print(f"Exec summary tokens: input={response.usage.input_tokens}, output={response.usage.output_tokens}, stop_reason={response.stop_reason}")

    app_data("data/filtered/executive_summary_raw.txt").write_text(raw, encoding="utf-8")

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        console.print(f"[yellow]Exec summary strict JSON parse failed: {e}. Trying fallback...[/]")
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError as e2:
                console.print(f"[red]Exec summary fallback parse failed: {e2}[/]")
        return {"_parse_failed": True}


def _collect_prose(obj) -> list[str]:
    """Recursively collect all string values from a synthesis structure."""
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_collect_prose(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_collect_prose(v))
    return out


def _reconcile_layers(synthesis: dict, ceiling_tier: str) -> dict:
    """WP-9: scan synthesis prose for certainty language stronger than the
    calibrated ceiling. Returns a structured report; phase 6 enforces it."""
    order = {"speculative": 0, "possible": 1, "probable": 2, "established": 3}
    ceiling = order.get((ceiling_tier or "speculative").lower(), 0)
    offending: list[str] = []
    for text in _collect_prose(synthesis):
        for word in rc.find_certainty_words(text):
            if word in order and order[word] > ceiling:
                offending.append(word)
    return {
        "ceiling_tier": ceiling_tier,
        "consistent": not offending,
        "offending_words": sorted(set(offending)),
    }


def run() -> None:
    checkpoint = Checkpoint("phase5_analyze")
    if checkpoint.is_complete():
        console.print("[green]Phase 5 already complete. Skipping.[/]")
        return

    console.print("[bold cyan]Phase 5: Cross-analysis (v2 with calibration)[/]")

    triage_path = app_data("data/filtered/triage_results.jsonl")
    deep_path = app_data("data/filtered/deep_results.jsonl")
    if not triage_path.exists():
        console.print("[red]No triage results — run Phase 3 first.[/]")
        return

    n_papers = sum(1 for _ in triage_path.open(encoding="utf-8"))

    aggregates = {
        "n_papers": n_papers,
        "consensus": compute_symptom_consensus(triage_path),
        "definition_heterogeneity": compute_definition_heterogeneity(triage_path),
        "study_designs": compute_study_design_distribution(triage_path),
        "inferred_limitations_raw": aggregate_inferred_limitations(deep_path) if deep_path.exists() else [],
        "methodology_quality": compute_methodology_quality(deep_path) if deep_path.exists() else {},
    }

    # v3: count how many deep extractions had the arbiter trigger a reconciliation
    if deep_path.exists():
        recon = 0
        for line in deep_path.open(encoding="utf-8"):
            try:
                d = json.loads(line)
                if d.get("reconciliation_triggered"):
                    recon += 1
            except json.JSONDecodeError:
                pass
        aggregates["reconciliations_triggered"] = recon

    if deep_path.exists():
        deep_extractions = [json.loads(l) for l in deep_path.open(encoding="utf-8")]
        calibrated_consensus = propagate_uncertainty(deep_extractions)
        aggregates["calibrated_consensus"] = calibrated_consensus
        aggregates["findings_by_certainty"] = build_uncertainty_report_section(calibrated_consensus)

        # ── UPGRADE v3.2 — authoritative methodology layer ────────────────
        # Computed BEFORE synthesis so the prompts consume the calibrated /
        # outcome-level truth instead of re-deriving it.
        from utils.run_context import topic_lower
        condition = topic_lower()
        norm = v32.normalisation_review(deep_extractions, condition)
        evidence_bodies = v32.build_evidence_bodies(deep_extractions, condition)
        ceiling_tier = v32.max_evidence_tier(calibrated_consensus, evidence_bodies)
        aggregates["evidence_bodies"] = evidence_bodies                     # WP-2 GRADE per outcome
        aggregates["normalisation_review"] = norm["normalisation_review"]    # WP-5/6 unmapped log
        aggregates["outcome_dictionary_version"] = norm["dictionary_version"]
        rob = v32.rob_assignments(deep_extractions)  # WP-3 design-matched
        aggregates["rob_instruments"] = rob
        aggregates["rob_instrument_counts"] = dict(Counter(a["primary_instrument"] for a in rob.values()))
        aggregates["quadas_paper_count"] = sum(1 for a in rob.values() if a["quadas_applicable"])
        aggregates["rct_count"] = v32.rct_count(deep_extractions)            # WP-§1.3
        aggregates["gated_synthesis"] = v32.gated_synthesis_decisions(deep_extractions, condition)  # WP-6
        aggregates["output_ceiling_tier"] = ceiling_tier                     # WP-10
        # WP-1 PRISMA flow record (written by phase 3), surfaced in the report.
        flow_path = app_data("data/filtered/flow_record.json")
        if flow_path.exists():
            try:
                aggregates["flow_record"] = json.loads(flow_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass

        # ── Methodological synthesis (Siciliano 2024 emulation) ──────────
        quadas_rows = collect_quadas_scores(deep_path)
        quadas_acceptable_ids = {r["paper_id"] for r in quadas_rows if r.get("acceptable")}
        quadas_excluded_ids = {r["paper_id"] for r in quadas_rows if r.get("acceptable") is False}
        quadas_totals = [r["quadas_total"] for r in quadas_rows if r["quadas_total"] is not None]
        quadas_distribution = {
            "n": len(quadas_rows),
            "mean": round(float(np.mean(quadas_totals)), 2) if quadas_totals else None,
            "median": int(np.median(quadas_totals)) if quadas_totals else None,
            "min": int(min(quadas_totals)) if quadas_totals else None,
            "max": int(max(quadas_totals)) if quadas_totals else None,
            "acceptable_n": len(quadas_acceptable_ids),
            "acceptable_pct": round(100 * len(quadas_acceptable_ids) / max(1, len(quadas_rows)), 1),
            "excluded_n": len(quadas_excluded_ids),
            "cutoff": settings.QUADAS_CUTOFF,
        }

        effect_rows = collect_effect_sizes(deep_path)
        meta_results = meta_analyze_by_factor(effect_rows, qaccept_ids=quadas_acceptable_ids if quadas_acceptable_ids else None)

        moderators_per_paper = {m["paper_id"]: m for m in collect_moderators(deep_path)}
        heterogeneity_section: dict[str, dict] = {}
        forest_plots: dict[str, str] = {}
        for factor, fres in meta_results.items():
            i2 = (fres["pooled"] or {}).get("i_squared")
            heterogeneity_section[factor] = {
                "n_studies": fres["pooled"]["n_studies"],
                "i_squared": i2,
                "model": fres["model"],
                "pooled_r": fres["pooled"]["pooled"],
                "ci": [fres["pooled"]["ci_low"], fres["pooled"]["ci_high"]],
                "tau_squared": fres["pooled"]["tau_squared"],
                "leave_one_out": fres["leave_one_out"],
                "publication_bias": fres["publication_bias"],
            }
            if i2 is not None and i2 >= settings.HETEROGENEITY_CRITICAL_THRESHOLD:
                heterogeneity_section[factor]["moderator_analysis"] = heterogeneity_critical_synthesis(
                    factor, fres, moderators_per_paper
                )
                forest_plots[factor] = forest_plot_text(factor, fres)

        aggregates["quadas_distribution"] = quadas_distribution
        aggregates["quadas_excluded_paper_ids"] = sorted(quadas_excluded_ids)
        aggregates["meta_analysis_by_factor"] = heterogeneity_section
        aggregates["forest_plots"] = forest_plots

        # Deep extraction yield diagnostics
        fail_path = app_data("data/filtered/deep_failures.jsonl")
        fail_rows = []
        if fail_path.exists():
            for line in fail_path.open(encoding="utf-8"):
                try:
                    fail_rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        deep_success = (aggregates.get("methodology_quality") or {}).get("n_deep") or len(quadas_rows)
        aggregates["deep_extraction_yield"] = {
            "requested": deep_success + len(fail_rows),
            "succeeded": deep_success,
            "failed": len(fail_rows),
            "failures": fail_rows[:20],  # cap to keep aggregates lean
        }

    synthesis = call_synthesizer(aggregates, deep_path, triage_path) if deep_path.exists() else {}
    due_diligence = call_due_diligence(aggregates, deep_path) if deep_path.exists() else {}
    executive_summary = call_executive_summary(aggregates, deep_path) if deep_path.exists() else {}

    # ── UPGRADE v3.2 — post-synthesis gates ───────────────────────────────
    ceiling_tier = aggregates.get("output_ceiling_tier", "speculative")
    if due_diligence:
        # WP-10: tie prescriptive detail to evidence strength. With a
        # speculative-max corpus this strips the Phase II skeleton, sample-size
        # math and named drug candidates, leaving landscape + gaps only.
        due_diligence = oc.gate_due_diligence(due_diligence, ceiling_tier, has_calc_effect_size=False)
    # WP-9: reconcile narrative certainty against the calibrated ceiling. The
    # calibrated layer is authoritative; prose may not exceed it.
    aggregates["reconciliation_report"] = _reconcile_layers(synthesis, ceiling_tier)

    analysis = {
        "aggregates": aggregates,
        "synthesis": synthesis,
        "due_diligence": due_diligence,
        "executive_summary": executive_summary,
        "meta": {"n_papers": n_papers},
    }

    out = app_data("data/filtered/analysis.json")
    out.write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"Analysis saved to {out}")

    checkpoint.mark_complete()
    console.print("[green]Phase 5 complete.[/]")


if __name__ == "__main__":
    run()