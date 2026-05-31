"""Heuristic router that picks a specialist for each user turn.

Two-phase classification:

  1. Slash commands take precedence (`/meta`, `/general`, …) so a user
     can force a specific specialist.
  2. Continuation messages (those starting with workflow-specific
     prefixes like "PICO confirmed", "Selected studies for data
     extraction", "Confirmed extracted data") stay in the current
     workflow regardless of keywords.
  3. Otherwise, if the thread is already pinned to a workflow, stay
     there.
  4. First-turn classification: keyword match against `_TRIGGER_KEYWORDS`
     by workflow. Default fallback is `general_qa`.

All routing is heuristic / regex-based. We can swap in a small classifier
LLM later if heuristics start misclassifying. The slash-command path
gives the user a manual override that always works.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any

from pydantic_ai.messages import ModelMessage

from ..auth.rbac import SKILL_PERMISSION, Permission
from .specialists import (
    SPECIALISTS,
    csr_drafter,
    general_qa,
    grade_drafter,
    ipd,
    irb_drafter,
    manuscript_drafter,
    meta_analysis,
    nma,
    registration_drafter,
    risk_of_bias,
    sap_drafter,
    search_strategy,
    sr_protocol,
    trial_stats,
)

logger = logging.getLogger(__name__)


class SkillNotAuthorizedError(Exception):
    """The classified workflow is gated behind a `skill.*` permission the
    caller does not hold. Surfaced as a 403 by the /turn handler.
    """

    def __init__(self, workflow: str, required: Permission) -> None:
        self.workflow = workflow
        self.required = required
        super().__init__(
            f"This account is not authorized for the {workflow!r} workflow "
            f"(needs {required.value})."
        )


# ── Slash-command map ────────────────────────────────────────────────────


_SLASH_COMMANDS: dict[str, str] = {
    "meta": meta_analysis.WORKFLOW_NAME,
    "meta-analysis": meta_analysis.WORKFLOW_NAME,
    "ma": meta_analysis.WORKFLOW_NAME,
    "search": search_strategy.WORKFLOW_NAME,
    "strategy": search_strategy.WORKFLOW_NAME,
    "protocol": sr_protocol.WORKFLOW_NAME,
    "sr": sr_protocol.WORKFLOW_NAME,
    "prisma": sr_protocol.WORKFLOW_NAME,
    "rob": risk_of_bias.WORKFLOW_NAME,
    "bias": risk_of_bias.WORKFLOW_NAME,
    "sap": sap_drafter.WORKFLOW_NAME,
    "samplesize": sap_drafter.WORKFLOW_NAME,
    "sample-size": sap_drafter.WORKFLOW_NAME,
    "power": sap_drafter.WORKFLOW_NAME,
    "manuscript": manuscript_drafter.WORKFLOW_NAME,
    "imrad": manuscript_drafter.WORKFLOW_NAME,
    "draft": manuscript_drafter.WORKFLOW_NAME,
    "response": manuscript_drafter.WORKFLOW_NAME,
    "register": registration_drafter.WORKFLOW_NAME,
    "registration": registration_drafter.WORKFLOW_NAME,
    "ctgov": registration_drafter.WORKFLOW_NAME,
    "euctr": registration_drafter.WORKFLOW_NAME,
    "ctis": registration_drafter.WORKFLOW_NAME,
    "irb": irb_drafter.WORKFLOW_NAME,
    "icf": irb_drafter.WORKFLOW_NAME,
    "consent": irb_drafter.WORKFLOW_NAME,
    "synopsis": irb_drafter.WORKFLOW_NAME,
    "csr": csr_drafter.WORKFLOW_NAME,
    "e3": csr_drafter.WORKFLOW_NAME,
    "study-report": csr_drafter.WORKFLOW_NAME,
    "grade": grade_drafter.WORKFLOW_NAME,
    "sof": grade_drafter.WORKFLOW_NAME,
    "prisma-checklist": grade_drafter.WORKFLOW_NAME,
    "trial-stats": trial_stats.WORKFLOW_NAME,
    "trialstats": trial_stats.WORKFLOW_NAME,
    "efficacy": trial_stats.WORKFLOW_NAME,
    "km": trial_stats.WORKFLOW_NAME,
    "mmrm": trial_stats.WORKFLOW_NAME,
    "nma": nma.WORKFLOW_NAME,
    "network-ma": nma.WORKFLOW_NAME,
    "indirect-comparison": nma.WORKFLOW_NAME,
    "league-table": nma.WORKFLOW_NAME,
    "ipd": ipd.WORKFLOW_NAME,
    "ipdma": ipd.WORKFLOW_NAME,
    "ipd-ma": ipd.WORKFLOW_NAME,
    "subject-level": ipd.WORKFLOW_NAME,
    "general": general_qa.WORKFLOW_NAME,
    "ask": general_qa.WORKFLOW_NAME,
    # Future: "gap" -> "research_gap", "ecrf" -> "ecrf_design"
}


# ── Continuation-message prefixes ────────────────────────────────────────


# When the frontend emits one of these (after the user clicks a button in a
# workflow card), we MUST stay in the originating workflow regardless of
# any keywords in the message body.
_WORKFLOW_CONTINUATIONS: dict[str, tuple[str, ...]] = {
    meta_analysis.WORKFLOW_NAME: (
        "PICO confirmed",
        "Selected studies for data extraction",
        "Confirmed extracted data",
    ),
    search_strategy.WORKFLOW_NAME: (
        "Confirmed query terms",
        "Tighten:",
        "Broaden:",
        "Finalize strategy",
    ),
    sr_protocol.WORKFLOW_NAME: (
        "Methods confirmed",
        "Finalize protocol",
    ),
    risk_of_bias.WORKFLOW_NAME: (
        "RoB confirmed",
        "Finalize RoB",
        "Run RoB on PMID",  # explicit ad-hoc trigger
        "Run risk of bias on extracted",  # handoff seed from data_extraction
    ),
    sap_drafter.WORKFLOW_NAME: (
        "PICOT confirmed",
        "Sample size confirmed",
        "Analysis plan confirmed",
        "Finalize SAP",
    ),
    manuscript_drafter.WORKFLOW_NAME: (
        "Manuscript intake confirmed",
        "Refine manuscript:",
        "Finalize manuscript",
        "Reviewer comments:",
        "Finalize reviewer response",
        "Draft as manuscript from meta-analysis",  # handoff seed (frontend)
        "Draft as manuscript from SR protocol",  # handoff seed
    ),
    registration_drafter.WORKFLOW_NAME: (
        "Intake confirmed",
        "Core fields confirmed",
        "Drafts confirmed",
        "Finalize registration",
    ),
    irb_drafter.WORKFLOW_NAME: (
        "Synopsis confirmed",
        "Refine ICF:",
        "ICF confirmed",
        "Finalize IRB",
        "Draft IRB packet from registration intake",  # handoff seed
    ),
    csr_drafter.WORKFLOW_NAME: (
        "CSR intake confirmed",
        "CSR synopsis confirmed",
        "CSR data sections confirmed",
        "Refine CSR:",
        "Finalize CSR",
        "Draft CSR from meta-analysis",  # handoff seed
        "Draft CSR from ADTTE",  # handoff seed
        "Draft CSR from SAP",  # handoff seed
        "Draft CSR from trial-stats",  # handoff seed (TRIAL-6)
    ),
    grade_drafter.WORKFLOW_NAME: (
        "GRADE intake confirmed",
        "Outcome assessed",
        "SoF confirmed",
        "PRISMA confirmed",
        "Refine SoF:",
        "Finalize GRADE",
        "Draft GRADE from meta-analysis",  # handoff seed
    ),
    trial_stats.WORKFLOW_NAME: (
        "Trial-stats intake confirmed",
        "Populations confirmed",
        "Time-to-event confirmed",
        "Continuous results confirmed",
        "Binary results confirmed",
        "Subgroup results confirmed",
        "Visualisations confirmed",
        "Add waterfall:",
        "Add swimmer:",
        "Refine trial-stats:",
        "Finalize trial-stats",
        "Draft trial-stats from ADTTE",  # handoff seed
        "Draft trial-stats from CDISC",  # handoff seed
    ),
    nma.WORKFLOW_NAME: (
        "NMA PICO confirmed",
        "NMA studies selected",
        "NMA extraction confirmed",
        "Run Bayesian NMA",
        "Refine NMA:",
        "Finalize NMA",
    ),
    ipd.WORKFLOW_NAME: (
        "IPD intake confirmed",
        "IPD bundle confirmed",
        "IPD main results confirmed",
        "IPD subgroup confirmed",
        "Add IPD subgroup:",
        "Refine IPD:",
        "Finalize IPD",
    ),
    # Future: research_gap / ecrf continuations
}


# ── First-turn keyword triggers ──────────────────────────────────────────


# Definitional / explanatory openings that should always go to general_qa
# even if they mention clinical concepts (e.g. "what is a forest plot",
# "explain meta-analysis"). Beats workflow keywords below.
_DEFINITIONAL_OPENINGS = re.compile(
    r"^\s*(what(\s+is|'s|\s+are)|explain|define|describe what|tell me about)\b",
    re.I,
)


# Compiled per-workflow patterns. Order matters: more-specific workflows
# are checked before less-specific ones. Search-strategy is checked before
# meta-analysis because phrases like "build a search strategy for …" would
# otherwise match the broad meta-analysis "effect of" pattern.
_TRIGGER_KEYWORDS: list[tuple[str, list[re.Pattern[str]]]] = [
    # risk_of_bias checked first: "assess risk of bias for these studies"
    # mentions studies but the user wants the RoB tool, not meta_analysis.
    (
        risk_of_bias.WORKFLOW_NAME,
        [
            re.compile(r"\brisk[-\s]of[-\s]bias\b", re.I),
            re.compile(r"\bRoB\s*(2(\.0)?|2|assessment|analysis)?\b", re.I),
            re.compile(r"\bROBINS[-\s]?I\b", re.I),
            re.compile(r"\bNewcastle[-\s]Ottawa\b", re.I),
            re.compile(r"\bQUADAS[-\s]?2\b", re.I),
        ],
    ),
    # manuscript_drafter checked early: "draft a manuscript on …" mentions
    # research topics that would otherwise grab meta_analysis or
    # sr_protocol patterns. Reviewer-response phrases also live here.
    (
        manuscript_drafter.WORKFLOW_NAME,
        [
            re.compile(r"\bdraft\s+(a|the)?\s*manuscript\b", re.I),
            re.compile(r"\bIMRaD\b", re.I),
            re.compile(r"\b(for|to)\s+journal\s+submission\b", re.I),
            re.compile(r"\bjournal[-\s]?ready\s+(draft|manuscript)\b", re.I),
            re.compile(r"\bpoint[-\s]by[-\s]point\b", re.I),
            re.compile(r"\brespond\s+to\s+(the\s+)?reviewers?\b", re.I),
            re.compile(r"\breviewer\s+response\b", re.I),
            re.compile(r"\bcover\s+letter\b", re.I),
            re.compile(r"\b(NEJM|Lancet|BMJ|JAMA)\s+(submission|format|manuscript)\b", re.I),
        ],
    ),
    # sap_drafter checked before meta_analysis: "sample size for X" + "trial
    # design" overlap with meta-analysis's "effect of" trigger but the user
    # wants the prospective-trial drafter.
    (
        sap_drafter.WORKFLOW_NAME,
        [
            re.compile(r"\bsample[-\s]?size\b", re.I),
            re.compile(r"\bpower\s+(calculation|analysis|the\s+trial)\b", re.I),
            re.compile(r"\bstatistical\s+analysis\s+plan\b", re.I),
            re.compile(r"\bSAP\b", re.I),
            re.compile(r"\bICH[-\s]?E9(R1)?\b", re.I),
            re.compile(r"\bprospective\s+trial\b", re.I),
            re.compile(r"\btrial\s+design\b", re.I),
            re.compile(r"\bpowered\s+to\s+detect\b", re.I),
            re.compile(r"\bPICOT\b", re.I),
        ],
    ),
    # registration_drafter: trial-registry shapes (CT.gov / EU CTR / CTIS).
    # NB: dispatcher matches against `msg_lower`, so patterns are
    # written lowercase (matches existing posture in this file).
    (
        registration_drafter.WORKFLOW_NAME,
        [
            re.compile(r"\bclinicaltrials\.?gov\b"),
            re.compile(r"\bct\.gov\b"),
            re.compile(r"\bprs\s+submission\b"),
            re.compile(r"\beu\s*ctr\b"),
            re.compile(r"\bctis\b"),
            re.compile(r"\beudract\b"),
            re.compile(r"\btrial\s+registration\b"),
            re.compile(r"\bregister (a |an |the |my )?(trial|study)\b"),
        ],
    ),
    # irb_drafter: IRB / ethics committee / Informed Consent Form.
    (
        irb_drafter.WORKFLOW_NAME,
        [
            re.compile(r"\birb\b"),
            re.compile(r"\bethics\s+committee\b"),
            re.compile(r"\binformed\s+consent\b"),
            re.compile(r"\bicf\b"),
            re.compile(r"\bprotocol\s+synopsis\b"),
            re.compile(r"\b21\s*cfr\s*50(\.25)?\b"),
            re.compile(r"\bich[-\s]?e6\b"),
            re.compile(r"\bsubject\s+consent\b"),
        ],
    ),
    # csr_drafter: Clinical Study Report (ICH E3).
    (
        csr_drafter.WORKFLOW_NAME,
        [
            re.compile(r"\bclinical\s+study\s+report\b"),
            re.compile(r"\bcsr\s+(draft|drafter|submission|document)\b"),
            re.compile(r"\bich[-\s]?e3\b"),
            re.compile(r"\bdraft\s+(a|the|my)?\s*csr\b"),
        ],
    ),
    # grade_drafter: GRADE Summary of Findings + PRISMA 2020 checklist.
    # Checked before risk_of_bias so phrases like "GRADE certainty
    # rating" don't slip into the RoB workflow.
    (
        grade_drafter.WORKFLOW_NAME,
        [
            re.compile(r"\bgrade\s+(sof|summary|certainty|assessment)\b"),
            re.compile(r"\bsummary\s+of\s+findings\b"),
            re.compile(r"\bcertainty\s+of\s+evidence\b"),
            re.compile(r"\bprisma\s+(checklist|2020|reporting)\b"),
            re.compile(r"\b(draft|generate)\s+(a|the|my)?\s*grade\s+(table|sof)\b"),
        ],
    ),
    # ipd: individual patient data meta-analysis (pools subject-level
    # data across trials). Checked BEFORE meta_analysis so "IPD MA on
    # statins" routes here rather than the aggregate pairwise specialist.
    (
        ipd.WORKFLOW_NAME,
        [
            re.compile(r"\bindividual\s+patient\s+data\b"),
            re.compile(r"\bipd\s+(meta[-\s]analysis|ma)\b"),
            re.compile(r"\bipd\b"),
            re.compile(r"\bpatient[-\s]level\s+data\b"),
            re.compile(r"\bsubject[-\s]level\s+data\b"),
            re.compile(r"\bone[-\s]stage\s+(model|meta|pooling)\b"),
            re.compile(r"\btwo[-\s]stage\s+(model|meta|pooling)\b"),
            re.compile(r"\btreatment\s*[×x]\s*subgroup\s+interaction\b"),
            re.compile(r"\bsubgroup[-\s]by[-\s]treatment\b"),
        ],
    ),
    # nma: network meta-analysis (≥3 interventions; indirect comparisons).
    # Checked BEFORE meta_analysis so "compare 5 DOACs" routes to NMA
    # rather than the pairwise specialist.
    (
        nma.WORKFLOW_NAME,
        [
            re.compile(r"\bnetwork\s+meta[-\s]analysis\b"),
            re.compile(r"\bnma\b"),
            re.compile(r"\bindirect\s+comparison\b"),
            re.compile(r"\bleague\s+table\b"),
            re.compile(r"\bsucra\b"),
            re.compile(r"\bmixed[-\s]treatment\s+comparison\b"),
            re.compile(r"\bmtc\b"),
            re.compile(r"\b(rank|ranking)\s+(of\s+)?treatments?\b"),
            # "compare N <noun>" with N >= 3 — gives NMA the multi-arm
            # canonical shape ("compare 5 DOACs", "compare 4 statins").
            re.compile(r"\bcompare\s+([3-9]|\d{2,})\s+\w+"),
        ],
    ),
    # trial_stats: post-lock analyses (K-M / MMRM / Cox / subgroup forest).
    # Checked before csr_drafter so phrases like "Kaplan-Meier on OS" don't
    # accidentally route to CSR. Patterns are lowercase to match `msg_lower`.
    (
        trial_stats.WORKFLOW_NAME,
        [
            re.compile(r"\bkaplan[-\s]?meier\b"),
            re.compile(r"\bmmrm\b"),
            re.compile(r"\bcox\s+(ph|proportional[-\s]hazards?|regression)\b"),
            re.compile(r"\blog[-\s]rank\b"),
            re.compile(r"\bsubgroup\s+forest\b"),
            re.compile(r"\bitt\s+(vs|versus)\s+pp\b"),
            re.compile(r"\b(per[-\s]protocol|intention[-\s]to[-\s]treat)\s+analysis\b"),
            re.compile(r"\binteraction\s+p[-\s]?value\b"),
            re.compile(r"\btrial[-\s]?stats?\b"),
            re.compile(r"\b(efficacy|safety|primary)\s+analysis\b"),
        ],
    ),
    # sr_protocol checked before search_strategy/meta_analysis: phrases like
    # "draft a protocol for a meta-analysis on X" mention meta-analysis but
    # the user wants the protocol drafter, not the analysis runner.
    (
        sr_protocol.WORKFLOW_NAME,
        [
            re.compile(r"\bPRISMA[-\s]?P?\b", re.I),
            re.compile(r"\bPROSPERO\b", re.I),
            re.compile(r"\b(systematic review|meta[-\s]analysis) protocol\b", re.I),
            re.compile(r"\bdraft (a |me a |the )?protocol\b", re.I),
            re.compile(r"\bregister (a |an )?(systematic|protocol)\b", re.I),
        ],
    ),
    (
        search_strategy.WORKFLOW_NAME,
        [
            re.compile(r"\bsearch strategy\b", re.I),
            re.compile(r"\b(pubmed|embase|cochrane) (query|search)\b", re.I),
            re.compile(r"\bbuild (a |me a |the )?(search|query)\b", re.I),
            re.compile(r"\bboolean (query|search|string)\b", re.I),
            re.compile(r"\bMeSH (terms|query|strategy)\b", re.I),
        ],
    ),
    (
        meta_analysis.WORKFLOW_NAME,
        [
            re.compile(r"\bmeta[\s-]analysis\b", re.I),
            re.compile(r"\bsystematic review\b", re.I),
            re.compile(r"\bPICO\b"),
            re.compile(r"\bPRISMA\b"),
            re.compile(r"\bpooled (effect|odds|risk)\b", re.I),
            re.compile(r"\bevidence (says|shows|suggests|on)\b", re.I),
            # "Does X reduce/increase/improve/prevent/treat Y" is the canonical
            # research-question shape.
            re.compile(
                r"\bdoes\s+\w+.*\b(reduce|increase|improve|prevent|cause|treat|compared?)\b",
                re.I,
            ),
            # "Compare/efficacy of/effect of …"
            re.compile(r"\b(compare|efficacy of|effect of|risk of)\s+\w", re.I),
        ],
    ),
    # Future:
    # ("research_gap", [re.compile(r"\bresearch gap\b", re.I), ...]),
    # ("ecrf_design", [re.compile(r"\beCRF\b"), re.compile(r"\bcase report form\b", re.I), ...]),
]


def classify(user_message: str, current_workflow: str | None) -> str:
    """Return the workflow id that should handle this turn.

    `current_workflow` is the workflow this thread was previously routed
    into (read from `Thread.workflow`). When set, continuation messages
    and stage-internal turns stay there.
    """
    msg = user_message.strip()
    msg_lower = msg.lower()

    # 1. Slash command override
    if msg.startswith("/"):
        first = msg.split(maxsplit=1)[0][1:].lower()
        if first in _SLASH_COMMANDS:
            chosen = _SLASH_COMMANDS[first]
            logger.info("Dispatcher: slash-command /%s -> %s", first, chosen)
            return chosen

    # 2. Continuation messages — stay in the current workflow
    for workflow, prefixes in _WORKFLOW_CONTINUATIONS.items():
        if any(msg.startswith(p) for p in prefixes):
            logger.info(
                "Dispatcher: continuation prefix matched -> %s",
                workflow,
            )
            return workflow

    # 3. Definitional openings always go to general_qa, even mid-workflow
    #    ("what is a forest plot?", "explain PICO"). The user wants a
    #    definition, not a specialist takeover.
    if _DEFINITIONAL_OPENINGS.match(msg):
        logger.info("Dispatcher: definitional opening -> general_qa")
        return general_qa.WORKFLOW_NAME

    # 4. If already pinned to a workflow, stay there
    if current_workflow and current_workflow in SPECIALISTS:
        logger.debug(
            "Dispatcher: thread pinned to %r, staying",
            current_workflow,
        )
        return current_workflow

    # 5. First-turn keyword classification
    for workflow, patterns in _TRIGGER_KEYWORDS:
        if any(p.search(msg_lower) for p in patterns):
            logger.info(
                "Dispatcher: keyword match -> %s",
                workflow,
            )
            return workflow

    # 6. Default
    logger.info("Dispatcher: no match -> %s", general_qa.WORKFLOW_NAME)
    return general_qa.WORKFLOW_NAME


def authorize_workflow(
    workflow: str,
    effective_permissions: frozenset[Permission] | None,
) -> None:
    """Raise `SkillNotAuthorizedError` if the caller may not run `workflow`.

    `effective_permissions=None` bypasses the check — used when auth is
    disabled (tests / early dev) so the dispatcher stays usable without a
    real identity. Production callers pass the resolved global-scope
    permission set from `UserRepository.effective_permissions_for_sub`.

    A workflow with no permission mapping (e.g. one not yet added to
    `SKILL_PERMISSION`) is treated as unauthorized — fail closed.
    """
    if effective_permissions is None:
        return
    required = SKILL_PERMISSION.get(workflow)
    if required is None:
        raise SkillNotAuthorizedError(workflow, Permission.SKILL_GENERAL_QA)
    if required not in effective_permissions:
        raise SkillNotAuthorizedError(workflow, required)


async def dispatch(
    user_message: str,
    *,
    current_workflow: str | None = None,
    message_history: Sequence[ModelMessage] | None = None,
    last_turn_kind: str | None = None,
    effective_permissions: frozenset[Permission] | None = None,
) -> tuple[Any, dict[str, Any], str]:
    """Classify the message, run the chosen specialist, return (output, meta, workflow).

    The endpoint persists `workflow` back to the thread so subsequent turns
    stay routed correctly even if the user's later phrasing is ambiguous.

    `effective_permissions` gates which specialist may run. Passing None
    (the default) preserves the pre-RBAC behaviour where any workflow is
    reachable, which is what we want for auth-disabled test/dev runs.
    """
    workflow = classify(user_message, current_workflow)
    authorize_workflow(workflow, effective_permissions)
    specialist = SPECIALISTS[workflow]
    output, meta = await specialist.run_turn(
        user_message,
        message_history=message_history,
        last_turn_kind=last_turn_kind,
    )
    return output, meta, workflow
