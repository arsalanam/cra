"""Pure helpers for the eCRF safety subsystem (top-6 #4).

Two things live here, both deliberately DB-free so they're trivially
unit-testable and reusable from anywhere:

  • `auto_classify_serious(...)` — apply ICH E2A "serious adverse event"
    criteria to a captured AE and return `(is_serious, reasons)`. The
    repo layer calls this on every AE write and persists the result;
    a PI can later override via the `ae.classify` endpoint.

  • `compute_reporting_deadline(...)` — return the 24-hour reporting
    deadline for an SAE per FDA 21 CFR §312.32(c)(1)(i) (initial
    reports for fatal or life-threatening SAEs within 7 days; all
    other SAEs within 15 days; the 24-hour clock is the platform's
    internal escalation timer for serious events that need triage).

The model + repo wire these in via two lines each — keeping the rule
logic centralised means a future change to the criteria (e.g. adding
"persistent disability extended to ≥60 days") touches one file plus the
tests, not every call site.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

# Reasons an AE is classified as serious per ICH E2A §III.A. Returned by
# `auto_classify_serious` as a list (an AE can be serious for multiple
# reasons simultaneously — e.g. hospitalisation + life-threatening).
SeriousReason = Literal[
    "death",
    "life_threatening",
    "hospitalisation",
    "persistent_disability",
    "congenital_anomaly",
    "other_medically_significant",
]

# Outcomes per CDISC SDTM AE.AEOUT controlled terminology, narrowed to
# the ones the safety classifier actually inspects.
AeOutcome = Literal[
    "recovered",
    "recovering",
    "not_recovered",
    "death",
    "unknown",
]

# Expectedness assessed against the Reference Safety Information (the
# Investigator's Brochure list of expected events). Captured on the AE
# form; `unexpected` is the SUSAR trigger. `unknown` never fires SUSAR —
# the platform does not infer an unexpectedness the PI hasn't asserted.
Expectedness = Literal["expected", "unexpected", "unknown"]

# Relationship-to-intervention levels that make an AE a *suspected adverse
# reaction* for SUSAR detection: `probable` and `definite`. A weaker
# `possible` link is intentionally NOT flagged — this keeps the SUSAR
# signal specific (fewer false positives for the safety physician to
# triage). Regulatory "adverse reaction" definitions (ICH E2A §II.B, EU CT
# Reg (EU) No 536/2014 Art. 2(2)(30)) turn on a "reasonable possibility" of
# causation; where a deployment wants to include `possible`, widen this
# single set.
SUSAR_RELATIONSHIP_LEVELS = frozenset({"probable", "definite"})


# Severity-grade-5 maps to a fatal AE per the standard NCI CTCAE scale
# (Grade 1 = mild, 5 = death related to AE). We treat grade 5 as always
# serious. Grade ≥3 is severe and is routinely treated as serious for
# safety-monitoring purposes (the platform's auto-classifier; the PI
# can still override down to non-serious via `ae.classify`).
GRADE_AUTO_SERIOUS_THRESHOLD = 3


def auto_classify_serious(
    *,
    severity_grade: int,
    outcome: str,
    hospitalisation_flag: bool = False,
    life_threatening_flag: bool = False,
    persistent_disability_flag: bool = False,
    congenital_anomaly_flag: bool = False,
    other_medically_significant_flag: bool = False,
) -> tuple[bool, list[SeriousReason]]:
    """Return `(is_serious, reasons)` per ICH E2A §III.A criteria.

    Multiple criteria can fire simultaneously; the returned list carries
    every reason in a deterministic order so audit-trail diffs are
    stable. `severity_grade` is the CTCAE 1–5 scale (5 = fatal). A grade
    ≥3 AE is auto-flagged serious even without an explicit
    death/hospitalisation flag — that's the standard safety-monitoring
    posture. PI may override via `ae.classify`.

    `outcome == "death"` always sets the death reason regardless of
    grade — captures the (rare) case where the form was filled in with a
    lower grade but the outcome already shows fatality.
    """
    reasons: list[SeriousReason] = []
    if outcome == "death" or severity_grade >= 5:
        reasons.append("death")
    if life_threatening_flag:
        reasons.append("life_threatening")
    if hospitalisation_flag:
        reasons.append("hospitalisation")
    if persistent_disability_flag:
        reasons.append("persistent_disability")
    if congenital_anomaly_flag:
        reasons.append("congenital_anomaly")
    if other_medically_significant_flag:
        reasons.append("other_medically_significant")

    # Grade ≥3 alone is enough for the platform's internal escalation —
    # but only add the catch-all reason when no other specific reason
    # already fired (so a grade-3 hospitalisation reports as
    # `hospitalisation`, not `other_medically_significant`).
    if not reasons and severity_grade >= GRADE_AUTO_SERIOUS_THRESHOLD:
        reasons.append("other_medically_significant")

    return (len(reasons) > 0, reasons)


def compute_reporting_deadline(
    *,
    is_serious: bool,
    reported_at: datetime,
) -> datetime | None:
    """Internal 24-hour escalation deadline for an SAE.

    Non-serious AEs return None — they don't fire the escalation timer.
    The 24-hour window is the platform's own posture (used by the
    overdue-SAE endpoint to surface events the sponsor needs to triage);
    the regulatory clock (FDA 7-day for fatal/life-threatening, 15-day
    for all other SAEs) is separate and is documented in the FDA 3500A
    report draft.
    """
    if not is_serious:
        return None
    return reported_at + timedelta(hours=24)


def is_susar(
    *,
    is_serious: bool,
    relationship_to_intervention: str,
    expectedness: str,
) -> bool:
    """Return True when an AE meets the SUSAR criteria.

    SUSAR = **S**uspected **U**nexpected **S**erious **A**dverse
    **R**eaction: the event is serious, it is a suspected reaction
    (relationship `probable` or `definite` — see
    `SUSAR_RELATIONSHIP_LEVELS`), and it is unexpected against the
    Reference Safety Information.

    SUSARs carry the tightest regulatory clock — expedited reporting
    within 7 calendar days for fatal / life-threatening events and 15 days
    otherwise (ICH E2A §III.C, 21 CFR §312.32, EU CT Reg Art. 42) — so the
    platform surfaces them for review. This is a SCREENING signal, not a
    regulatory determination: the sponsor's safety physician makes the
    final SUSAR call and owns the submission.
    """
    return (
        is_serious
        and relationship_to_intervention in SUSAR_RELATIONSHIP_LEVELS
        and expectedness == "unexpected"
    )


__all__ = [
    "GRADE_AUTO_SERIOUS_THRESHOLD",
    "SUSAR_RELATIONSHIP_LEVELS",
    "AeOutcome",
    "Expectedness",
    "SeriousReason",
    "auto_classify_serious",
    "compute_reporting_deadline",
    "is_susar",
]
