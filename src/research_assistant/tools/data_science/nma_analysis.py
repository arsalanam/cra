"""run_nma_analysis tool — sandbox-backed frequentist/Bayesian NMA + geometry.

Wraps `sandbox_exec` with the 3 canonical NMA scripts in
`nma/sandbox_scripts/`. The specialist that registers this tool
(nma) calls it with a canonical JSON payload per backend.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic_ai import Agent, RunContext

from ...agent.deps import AgentDeps
from ...nma import AVAILABLE_NMA_SCRIPTS, NmaScriptKind, load_script
from .._emit import emit_run
from .sandbox_exec import _impl as _sandbox_impl

logger = logging.getLogger(__name__)


async def _run(
    backend: NmaScriptKind,
    data_payload: str,
) -> dict[str, Any]:
    try:
        script_text = load_script(backend)
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
    async def run_nma_analysis(
        ctx: RunContext[AgentDeps],
        backend: NmaScriptKind,
        data_payload: str,
    ) -> str:
        """
        Run a sandbox-side NMA analysis.

        `backend`:
          • "frequentist" — mvmeta-style + electrical-network analogy via
                              statsmodels + numpy. League table + SUCRA +
                              network metadata. Use as the default backend.
          • "bayesian"    — PyMC MCMC. Returns posterior medians + 95%
                              credible intervals + SUCRA. REQUIRES PyMC
                              pinned in the sandbox image — if not
                              available, returns a structured skip_reason
                              and the host falls back to frequentist.
          • "geometry"    — Network-geometry PNG (nodes for interventions,
                              edges for head-to-head pairs). Called once
                              the league_table + network metadata exist.

        `data_payload` is a JSON string. For frequentist/bayesian:

            {"data": {
              "studies": [
                {"source_id": "12345", "arms": [
                  {"intervention": "Drug A", "n": 200, "events": 40},
                  {"intervention": "Placebo", "n": 198, "events": 62}
                ]}, ...
              ],
              "interventions": ["Placebo", "Drug A", "Drug B", "Drug C"],
              "effect_measure": "OR"
            }}

        For geometry: `{"data": {"nodes": [...], "edges": [...]}}`.

        Returns JSON with `stdout`, `files`, and `error`. Use the inlined
        JSON artefact to populate the NmaResults schema on the next turn.
        """
        if backend not in AVAILABLE_NMA_SCRIPTS:
            return json.dumps(
                {
                    "error": (
                        f"Unknown NMA backend {backend!r}. Choose one of: "
                        f"{', '.join(AVAILABLE_NMA_SCRIPTS)}."
                    )
                }
            )

        async def _impl() -> str:
            try:
                result = await _run(backend, data_payload)
            except Exception as e:
                logger.exception("run_nma_analysis failed")
                return json.dumps({"error": f"{type(e).__name__}: {e}"})
            return json.dumps(result, ensure_ascii=False)

        result_str = await emit_run(
            ctx,
            tool="run_nma_analysis",
            icon="🕸",
            args={"backend": backend},
            description=f"Running NMA backend={backend} in sandbox",
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
