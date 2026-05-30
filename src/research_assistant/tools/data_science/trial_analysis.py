"""run_trial_analysis tool — sandbox-backed K-M / MMRM / binary / subgroup.

Wraps `sandbox_exec` with a curated set of canonical scripts so the
trial_stats specialist doesn't have to author MMRM / Cox PH / log-binomial
Python from scratch every turn. Each script lives under
`trial_stats/sandbox_scripts/` as packaged data.

The script is loaded as text, the operator's ADaM-shaped JSON is bundled
into ``{"data": [...], "params": {...}}``, and `sandbox_exec` runs it.
Output artefacts (K-M PNGs, subgroup forest) are persisted to the
images dir and returned by URL, mirroring the meta_analysis flow.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic_ai import Agent, RunContext

from ...agent.deps import AgentDeps
from ...trial_stats import AVAILABLE_ANALYSES, AnalysisKind, load_script
from .._emit import emit_run
from .sandbox_exec import _impl as _sandbox_impl

logger = logging.getLogger(__name__)


async def _run(
    analysis_kind: AnalysisKind,
    data_payload: str,
) -> dict[str, Any]:
    """Load the canonical script and forward to sandbox_exec.

    `data_payload` is the operator-pasted JSON bundling rows + params.
    """
    try:
        script_text = load_script(analysis_kind)
    except FileNotFoundError as e:
        return {"error": str(e)}
    sandbox = await _sandbox_impl(
        code=script_text,
        input_data=data_payload,
        input_format="json",
    )
    return {
        "stdout": sandbox.stdout,
        "files": sandbox.files,
        "error": sandbox.error,
    }


def register(agent: Agent[AgentDeps]) -> None:
    @agent.tool
    async def run_trial_analysis(
        ctx: RunContext[AgentDeps],
        analysis_kind: AnalysisKind,
        data_payload: str,
    ) -> str:
        """
        Run a canonical trial-stats analysis inside the Docker sandbox.

        `analysis_kind`:
          • "kaplan_meier"    — K-M curve + log-rank + Cox PH HR per PARAMCD
                                  (requires ADTTE-shaped rows with USUBJID,
                                  PARAMCD, AVAL, CNSR, TRT01A).
          • "mmrm"            — Mixed-Models for Repeated Measures on a
                                  longitudinal continuous endpoint.
                                  (requires USUBJID, PARAMCD, AVISIT,
                                  AVISITN, AVAL, TRT01A; optional BASE).
          • "binary"          — Fisher's exact or log-binomial RR on a
                                  binary endpoint (requires USUBJID,
                                  PARAMCD, AVALC, TRT01A; method +
                                  event_value passed in params).
          • "subgroup_forest" — per-subgroup HR + interaction p +
                                  subgroup forest PNG (requires the same
                                  shape as kaplan_meier plus an extra
                                  SUBGROUP column named in params).

        `data_payload` is a JSON string with the shape:

            {
              "data": [ {row dict}, ... ],
              "params": {
                "paramcds": ["OS"], "arms": ["Placebo", "Drug A"],
                ...analysis-specific keys...
              }
            }

        Returns a JSON document with `stdout`, `files` (filename → URL or
        inlined text content), and `error`. Use the inlined JSON
        artefacts (e.g. `trial-stats-tte.json`) to populate the
        TimeToEventResult / ContinuousResult / BinaryResult /
        SubgroupAnalysis schema rows on the next turn.
        """
        if analysis_kind not in AVAILABLE_ANALYSES:
            return json.dumps(
                {
                    "error": (
                        f"Unknown analysis_kind {analysis_kind!r}. Choose one of: "
                        f"{', '.join(AVAILABLE_ANALYSES)}."
                    )
                }
            )

        async def _impl() -> str:
            try:
                result = await _run(analysis_kind, data_payload)
            except Exception as e:
                logger.exception("run_trial_analysis failed")
                return json.dumps({"error": f"{type(e).__name__}: {e}"})
            return json.dumps(result, ensure_ascii=False)

        # Stash any image artefacts emitted by the sandbox so the
        # frontend can render K-M / subgroup-forest figures inline.
        result_str = await emit_run(
            ctx,
            tool="run_trial_analysis",
            icon="📊",
            args={"analysis_kind": analysis_kind},
            description=f"Running {analysis_kind} in sandbox",
            impl=_impl,
        )

        try:
            payload = json.loads(result_str)
            for fname, content in (payload.get("files") or {}).items():
                if isinstance(content, str) and content.startswith("/images/"):
                    ctx.deps.artifacts[fname] = content
        except (json.JSONDecodeError, AttributeError):  # pragma: no cover — defensive
            pass

        # Defensive: surface the explicit timing of the asyncio loop
        # to avoid an unused-await false-positive when the result is empty.
        await asyncio.sleep(0)
        return result_str


__all__ = ["register"]
