"""TLF generator — Tables / Listings / Figures (top-6 #6).

MVP scope:
  • T-1 Subject disposition — n randomised / completed / SAFFL=Y / DTHFL=Y
  • T-2 Demographics — AGE summary + SEX/RACE counts
  • T-3 AE summary — total events / subjects with ≥1 AE / SAEs / by SOC
        (SOC stays "Unspecified" until a MedDRA dictionary is wired in;
        the platform-MVP MedDRA PT is captured but not back-indexed to
        a system organ class).
  • F-1 AE-frequency — top-10 preferred terms by count, hand-rolled SVG
        (no matplotlib / sandbox round-trip; same posture as PRISMA).

Outputs are persisted as `TlfArtefact` rows (content_json for tables,
svg_content for figures).
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from collections.abc import Iterable
from typing import Any

from ..persistence.clinical.models import AdamAdsl, AdamAdtte, SdtmAe, TlfArtefact


def _table(
    *,
    deployment_id: str,
    tlf_id: str,
    title: str,
    columns: list[str],
    rows: list[list[Any]],
) -> TlfArtefact:
    return TlfArtefact(
        deployment_id=deployment_id,
        kind="table",
        tlf_id=tlf_id,
        title=title,
        content_json=json.dumps({"columns": columns, "rows": rows}, ensure_ascii=False),
    )


def _disposition_table(
    deployment_id: str, adsl: list[AdamAdsl]
) -> TlfArtefact:
    n_total = len(adsl)
    n_itt = sum(1 for r in adsl if r.ITTFL == "Y")
    n_saf = sum(1 for r in adsl if r.SAFFL == "Y")
    n_death = sum(1 for r in adsl if r.DTHFL == "Y")

    def pct(n: int) -> str:
        return f"{(100.0 * n / n_total):.1f}" if n_total else "0.0"

    return _table(
        deployment_id=deployment_id,
        tlf_id="t-disposition",
        title="Table 1 — Subject Disposition",
        columns=["Status", "n", "%"],
        rows=[
            ["Subjects in dataset", n_total, "100.0"],
            ["ITT population (ITTFL=Y)", n_itt, pct(n_itt)],
            ["Safety population (SAFFL=Y)", n_saf, pct(n_saf)],
            ["Subjects with death event (DTHFL=Y)", n_death, pct(n_death)],
        ],
    )


def _demographics_table(
    deployment_id: str, adsl: list[AdamAdsl]
) -> TlfArtefact:
    ages = [r.AGE for r in adsl if r.AGE is not None]
    sexes = Counter(r.SEX or "Unknown" for r in adsl)
    races = Counter(r.RACE or "Unknown" for r in adsl)
    n = len(adsl)
    rows: list[list[Any]] = []
    if ages:
        if len(ages) > 1:
            mean_sd = f"mean {statistics.mean(ages):.1f}, sd {statistics.stdev(ages):.1f}"
        else:
            mean_sd = f"mean {ages[0]:.1f}"
        rows.append([
            "Age (years)",
            mean_sd,
            f"median {statistics.median(ages):.1f}, range {min(ages)}-{max(ages)}",
        ])
    else:
        rows.append(["Age (years)", "(no data)", "—"])
    for sex, c in sorted(sexes.items()):
        pct = f"{(100.0 * c / n):.1f}%" if n else "0.0%"
        rows.append([f"Sex: {sex}", c, pct])
    for race, c in sorted(races.items()):
        pct = f"{(100.0 * c / n):.1f}%" if n else "0.0%"
        rows.append([f"Race: {race}", c, pct])
    return _table(
        deployment_id=deployment_id,
        tlf_id="t-demographics",
        title="Table 2 — Baseline Demographics",
        columns=["Characteristic", "Value", "Detail"],
        rows=rows,
    )


def _ae_summary_table(
    deployment_id: str, ae_records: list[SdtmAe], adsl: list[AdamAdsl]
) -> TlfArtefact:
    n_subj = len(adsl)
    n_events = len(ae_records)
    subjects_with_ae = {r.USUBJID for r in ae_records}
    n_subj_with_ae = len(subjects_with_ae)
    n_sae = sum(1 for r in ae_records if r.AESER == "Y")
    subjects_with_sae = {r.USUBJID for r in ae_records if r.AESER == "Y"}
    rows: list[list[Any]] = [
        ["Total AE events", n_events, "—"],
        [
            "Subjects with ≥1 AE",
            n_subj_with_ae,
            f"{(100.0 * n_subj_with_ae / n_subj):.1f}%" if n_subj else "0.0%",
        ],
        ["Total SAEs", n_sae, "—"],
        [
            "Subjects with ≥1 SAE",
            len(subjects_with_sae),
            f"{(100.0 * len(subjects_with_sae) / n_subj):.1f}%" if n_subj else "0.0%",
        ],
    ]
    # Severity breakdown (SDTM AESEV controlled term).
    sev_counts = Counter(r.AESEV or "(unspecified)" for r in ae_records)
    for sev, c in sorted(sev_counts.items()):
        rows.append([f"Severity: {sev}", c, "—"])
    return _table(
        deployment_id=deployment_id,
        tlf_id="t-ae-summary",
        title="Table 3 — Adverse Event Summary",
        columns=["Metric", "n", "%"],
        rows=rows,
    )


# ── Figure: AE frequency bar chart (hand-rolled SVG) ────────────────────


def _ae_frequency_svg(ae_records: list[SdtmAe]) -> str:
    """Top-10 PTs by count as a horizontal bar chart.

    Falls back to AETERM (verbatim) when AEDECOD isn't populated, since
    the MVP MedDRA-PT capture is free text and may be missing.
    """
    counts: Counter[str] = Counter()
    for r in ae_records:
        key = (r.AEDECOD or r.AETERM or "Unspecified").strip()
        if key:
            counts[key] += 1
    top = counts.most_common(10)

    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    W, H = 720, max(280, 40 * len(top) + 60)
    if not top:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="120" '
            f'viewBox="0 0 {W} 120">'
            f'<rect width="{W}" height="120" fill="#ffffff"/>'
            f'<text x="{W // 2}" y="60" text-anchor="middle" '
            f'font-family="Helvetica,Arial,sans-serif" font-size="14" fill="#666">'
            f"No adverse events recorded.</text></svg>"
        )

    max_count = max(c for _, c in top)
    label_x = 12
    bar_x = 230
    bar_max_w = 440
    bar_height = 24
    y_top = 30
    bars: list[str] = []
    bars.append(
        f'<text x="{W // 2}" y="20" text-anchor="middle" '
        f'font-family="Helvetica,Arial,sans-serif" font-size="13" font-weight="700" '
        f'fill="#0F2A47">AE frequency — top {len(top)} preferred terms</text>'
    )
    for i, (term, count) in enumerate(top):
        y = y_top + i * (bar_height + 10)
        bar_w = int((count / max_count) * bar_max_w)
        bars.append(
            f'<text x="{label_x}" y="{y + bar_height // 2 + 4}" '
            f'font-family="Helvetica,Arial,sans-serif" font-size="11" '
            f'fill="#1d2026">{esc(term[:32])}</text>'
        )
        bars.append(
            f'<rect x="{bar_x}" y="{y}" width="{bar_w}" height="{bar_height}" '
            f'fill="#1f3a66" rx="3"/>'
        )
        bars.append(
            f'<text x="{bar_x + bar_w + 8}" y="{y + bar_height // 2 + 4}" '
            f'font-family="Helvetica,Arial,sans-serif" font-size="11" '
            f'fill="#1d2026">{count}</text>'
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}">'
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>'
        + "".join(bars)
        + "</svg>"
    )


def _ae_frequency_figure(
    deployment_id: str, ae_records: list[SdtmAe]
) -> TlfArtefact:
    return TlfArtefact(
        deployment_id=deployment_id,
        kind="figure",
        tlf_id="f-ae-frequency",
        title="Figure 1 — AE Frequency (Top 10)",
        svg_content=_ae_frequency_svg(ae_records),
    )


def _km_median(times: list[float], events: list[int]) -> float | None:
    """Median time-to-event via the Kaplan-Meier estimator.

    Handles censoring properly (event=1 means observed, 0 means
    censored — matches the convention used in this helper, NOT the
    ADaM CNSR convention which is inverted). Returns the first time
    at which the survival function S(t) drops below 0.5; None if it
    never does (i.e. the median is not reached in the observation
    window — a common, valid outcome for sparse or short follow-up).
    """
    if not times:
        return None
    paired = sorted(zip(times, events, strict=True), key=lambda x: x[0])
    n_at_risk = len(paired)
    surv = 1.0
    median: float | None = None
    i = 0
    while i < len(paired):
        t = paired[i][0]
        j = i
        events_at_t = 0
        while j < len(paired) and paired[j][0] == t:
            events_at_t += paired[j][1]
            j += 1
        if events_at_t > 0:
            surv *= 1.0 - events_at_t / n_at_risk
            if surv <= 0.5 and median is None:
                median = t
                break
        n_at_risk -= (j - i)
        i = j
    return median


def _tte_summary_table(
    deployment_id: str, adtte_records: list[AdamAdtte]
) -> TlfArtefact:
    """Per-PARAMCD summary — n / events / censored / median TTE (days)."""
    by_param: dict[str, dict[str, Any]] = {}
    for r in adtte_records:
        bucket = by_param.setdefault(
            r.PARAMCD,
            {"label": r.PARAM, "n": 0, "events": 0, "censored": 0, "times": [], "evts": []},
        )
        bucket["n"] += 1
        if r.AVAL is not None:
            bucket["times"].append(float(r.AVAL))
            # K-M math uses 1=event, 0=censored; ADaM CNSR is inverted.
            bucket["evts"].append(1 if r.CNSR == 0 else 0)
        if r.CNSR == 0:
            bucket["events"] += 1
        else:
            bucket["censored"] += 1
    rows: list[list[Any]] = []
    if not by_param:
        rows.append(["(no ADTTE rows)", "—", "—", "—", "—"])
    else:
        for paramcd in sorted(by_param):
            b = by_param[paramcd]
            median = _km_median(b["times"], b["evts"])
            rows.append(
                [
                    paramcd,
                    b["label"],
                    b["n"],
                    f"{b['events']} ({b['censored']} censored)",
                    f"{median:.1f}" if median is not None else "not reached",
                ]
            )
    return _table(
        deployment_id=deployment_id,
        tlf_id="t-tte-summary",
        title="Table 4 — Time-to-Event Summary",
        columns=["PARAMCD", "Parameter", "n", "Events", "Median (days)"],
        rows=rows,
    )


def generate_tlfs(
    *,
    deployment_id: str,
    adsl: Iterable[AdamAdsl],
    ae_records: Iterable[SdtmAe],
    adtte_records: Iterable[AdamAdtte] | None = None,
) -> list[TlfArtefact]:
    """Produce the MVP TLF set: 4 tables + 1 figure.

    `adtte_records` is optional for backward compatibility with the
    sparse tests; when None, the t-tte-summary row is omitted.
    """
    adsl_list = list(adsl)
    ae_list = list(ae_records)
    out: list[TlfArtefact] = [
        _disposition_table(deployment_id, adsl_list),
        _demographics_table(deployment_id, adsl_list),
        _ae_summary_table(deployment_id, ae_list, adsl_list),
        _ae_frequency_figure(deployment_id, ae_list),
    ]
    if adtte_records is not None:
        out.append(_tte_summary_table(deployment_id, list(adtte_records)))
    return out


__all__ = ["generate_tlfs"]
