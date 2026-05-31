"""run_visualisation tool — sandbox-backed funnel / waterfall / swimmer.

Wraps `sandbox_exec` with the 3 canonical visualisation scripts in
`research_assistant/visualizations/sandbox_scripts/`. The specialist
that registers this tool (meta_analysis for funnel; trial_stats for
waterfall + swimmer) calls it with a per-plot canonical JSON payload.

The GRADE chip table is a separate code path — it's host-rendered SVG
in `visualizations/chip_table.py`, no sandbox needed.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic_ai import Agent, RunContext

from ...agent.deps import AgentDeps
from ...visualizations import AVAILABLE_VISUALIZATIONS, VisualizationKind, load_script
from .._emit import emit_run
from .sandbox_exec import _impl as _sandbox_impl

logger = logging.getLogger(__name__)


async def _run(
    kind: VisualizationKind,
    data_payload: str,
) -> dict[str, Any]:
    try:
        script_text = load_script(kind)
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
    async def run_visualisation(
        ctx: RunContext[AgentDeps],
        viz_kind: VisualizationKind,
        data_payload: str,
    ) -> str:
        """
        Render a sandbox-side visualisation from canonical JSON.

        `viz_kind`:
          • "funnel"    — Funnel plot + Egger's test for publication
                            bias. Input: `{"data": {"outcome_label":
                            ..., "effect_measure": "OR"|"RR"|"MD"|"SMD",
                            "studies": [{label, effect, se}]}}`.
                            Requires ≥3 studies.
          • "waterfall" — Per-subject best response sorted descending.
                            Input: `{"data": {"outcome_label": ...,
                            "subjects": [{usubjid, best_change_pct,
                            treatment}], "treatments": [...]}}`. The
                            sandbox categorises CR/PR/SD/PD per
                            RECIST 1.1 thresholds (PR ≤ −30%, PD ≥ +20%).
          • "swimmer"   — Per-subject treatment timeline with event
                            markers. Input: `{"data": {"outcome_label":
                            ..., "subjects": [{usubjid, treatment,
                            duration_days, ongoing, events:
                            [{day, kind}]}], "treatments": [...]}}`.
                            Event kinds: response_onset / pr / cr /
                            progression / death / off_treatment.

        Returns JSON with `stdout`, `files` (filename → URL or inlined
        text content), and `error`. Use the inlined JSON artefact
        (e.g. `trial-stats-funnel.json`) to populate the workflow's
        schema fields on the next turn.
        """
        if viz_kind not in AVAILABLE_VISUALIZATIONS:
            return json.dumps(
                {
                    "error": (
                        f"Unknown viz_kind {viz_kind!r}. Choose one of: "
                        f"{', '.join(AVAILABLE_VISUALIZATIONS)}."
                    )
                }
            )

        async def _impl() -> str:
            try:
                result = await _run(viz_kind, data_payload)
            except Exception as e:
                logger.exception("run_visualisation failed")
                return json.dumps({"error": f"{type(e).__name__}: {e}"})
            return json.dumps(result, ensure_ascii=False)

        result_str = await emit_run(
            ctx,
            tool="run_visualisation",
            icon="📈",
            args={"viz_kind": viz_kind},
            description=f"Rendering {viz_kind} in sandbox",
            impl=_impl,
        )

        try:
            payload = json.loads(result_str)
            for fname, content in (payload.get("files") or {}).items():
                if isinstance(content, str) and content.startswith("/images/"):
                    ctx.deps.artifacts[fname] = content
        except (json.JSONDecodeError, AttributeError):  # pragma: no cover
            pass

        await asyncio.sleep(0)
        return result_str


__all__ = ["register"]
