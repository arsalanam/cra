"""run_ipd_analysis tool — sandbox-backed IPD MA (one-stage / two-stage / subgroup)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic_ai import Agent, RunContext

from ...agent.deps import AgentDeps
from ...ipd import AVAILABLE_IPD_SCRIPTS, IpdScriptKind, load_script
from .._emit import emit_run
from .sandbox_exec import _impl as _sandbox_impl

logger = logging.getLogger(__name__)


async def _run(
    stage: IpdScriptKind,
    data_payload: str,
) -> dict[str, Any]:
    try:
        script_text = load_script(stage)
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
    async def run_ipd_analysis(
        ctx: RunContext[AgentDeps],
        stage: IpdScriptKind,
        data_payload: str,
    ) -> str:
        """
        Run a sandbox-side IPD meta-analysis.

        `stage`:
          • "one_stage"  — single multilevel model. Continuous:
                            statsmodels MixedLM (random trial intercept +
                            fixed treatment); REML. Binary: GLM Binomial
                            logit with trial dummies + treatment (fixed-
                            effects approximation). TTE: stratified Cox
                            PH (strata=trial_id) + treatment.
          • "two_stage"  — per-trial estimate + DerSimonian-Laird random-
                            effects pool. Returns I² + τ² + per-trial
                            effects.
          • "subgroup"   — adds treatment × subgroup interaction to the
                            one-stage model. Requires `subgroup_variable`
                            in the data payload. Returns per-level pooled
                            effects + the joint Wald interaction p.

        `data_payload` is a JSON string with the shape:

            {"data": {
              "trials": [
                {"trial_id": "RCT-1", "treatment_column": "trt",
                 "treatment_active_value": "1", "outcome_column": "y",
                 "event_column": null, "covariate_columns": ["age"],
                 "rows_csv": "subject_id,trt,y,age\\n1,1,1.2,55\\n..."},
                ...
              ],
              "effect_measure": "OR",  # OR | RR | MD | SMD | HR
              "subgroup_variable": "sex"  # only for stage=subgroup
            }}

        Returns JSON with `stdout`, `files`, and `error`. Use the inlined
        `ipd-one-stage.json` / `ipd-two-stage.json` / `ipd-subgroup.json`
        artefact to populate the IpdMainResults / IpdSubgroupResults
        schema on the next turn.
        """
        if stage not in AVAILABLE_IPD_SCRIPTS:
            return json.dumps(
                {
                    "error": (
                        f"Unknown IPD stage {stage!r}. Choose one of: "
                        f"{', '.join(AVAILABLE_IPD_SCRIPTS)}."
                    )
                }
            )

        async def _impl() -> str:
            try:
                result = await _run(stage, data_payload)
            except Exception as e:
                logger.exception("run_ipd_analysis failed")
                return json.dumps({"error": f"{type(e).__name__}: {e}"})
            return json.dumps(result, ensure_ascii=False)

        result_str = await emit_run(
            ctx,
            tool="run_ipd_analysis",
            icon="🧪",
            args={"stage": stage},
            description=f"Running IPD {stage} in sandbox",
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
