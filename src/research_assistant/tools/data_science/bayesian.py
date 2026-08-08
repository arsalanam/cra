"""bayesian tool — posterior probability of a treatment effect.

Given a frequentist effect estimate and its standard error, plus an optional
Normal prior, returns the Bayesian posterior for the true effect: its mean,
credible interval, and the posterior probability that the effect beats a
decision threshold (e.g. P(HR < 1), P(mean benefit > MCID)). This is the
modern Bayesian-decision summary that a p-value alone can't give.

Method. A Normal-Normal conjugate update. The estimate is treated as
`estimate ~ N(theta, se^2)`; with a prior `theta ~ N(prior_mean,
prior_sd^2)` the posterior is Normal with

    precision = 1/se^2 + 1/prior_sd^2
    var       = 1 / precision
    mean      = var * (estimate/se^2 + prior_mean/prior_sd^2)

A non-informative (flat) prior — `prior_sd=None` — reduces the posterior to
`N(estimate, se^2)`, so the posterior tail probability equals the one-sided
frequentist result: a deliberate, transparent bridge, not a coincidence.

Runs on scipy.stats.norm in the agent process (no sandbox, no PyMC). For
effects estimated on the log scale (log-HR, log-OR), pass
`exponentiate=True` to also get the ratio-scale mean + credible interval.
Full MCMC (hierarchical priors, non-Normal likelihoods) is out of scope for
this closed-form helper.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Any

from pydantic_ai import Agent, RunContext
from scipy.stats import norm  # type: ignore[import-untyped]

from ...agent.deps import AgentDeps
from .._emit import emit_run

logger = logging.getLogger(__name__)


def bayesian_posterior(
    *,
    estimate: float,
    se: float,
    threshold: float = 0.0,
    prior_mean: float = 0.0,
    prior_sd: float | None = None,
    cred_level: float = 0.95,
    exponentiate: bool = False,
) -> dict[str, Any]:
    """Normal-Normal posterior for a treatment effect.

    `estimate` / `se` are the point estimate and standard error (on the
    analysis scale — often log-HR or log-OR). `threshold` is the decision
    boundary (default 0, i.e. "no effect"). `prior_sd=None` is a flat
    non-informative prior. `cred_level` sets the credible-interval mass.
    `exponentiate=True` additionally reports exp(mean) + exp(interval) for
    log-scale effects.

    Returns posterior_mean/sd, the credible interval, and the posterior
    probabilities the true effect is below and above the threshold.
    """
    if se <= 0:
        raise ValueError("se must be positive.")
    if not (0.0 < cred_level < 1.0):
        raise ValueError("cred_level must be in (0, 1).")
    if prior_sd is not None and prior_sd <= 0:
        raise ValueError("prior_sd must be positive when provided.")

    if prior_sd is None:
        post_mean = estimate
        post_var = se * se
        prior_desc: dict[str, Any] = {"type": "flat", "prior_mean": None, "prior_sd": None}
    else:
        precision = 1.0 / (se * se) + 1.0 / (prior_sd * prior_sd)
        post_var = 1.0 / precision
        post_mean = post_var * (estimate / (se * se) + prior_mean / (prior_sd * prior_sd))
        prior_desc = {"type": "normal", "prior_mean": prior_mean, "prior_sd": prior_sd}

    post_sd = math.sqrt(post_var)
    z = float(norm.ppf(0.5 + cred_level / 2.0))
    ci_low = post_mean - z * post_sd
    ci_high = post_mean + z * post_sd

    prob_less = float(norm.cdf((threshold - post_mean) / post_sd))
    prob_greater = 1.0 - prob_less

    out: dict[str, Any] = {
        "posterior_mean": round(post_mean, 6),
        "posterior_sd": round(post_sd, 6),
        "cred_level": cred_level,
        "credible_interval": [round(ci_low, 6), round(ci_high, 6)],
        "threshold": threshold,
        "prob_less_than_threshold": round(prob_less, 6),
        "prob_greater_than_threshold": round(prob_greater, 6),
        "prior": prior_desc,
        "inputs": {"estimate": estimate, "se": se},
    }
    if exponentiate:
        out["exp_posterior_mean"] = round(math.exp(post_mean), 6)
        out["exp_credible_interval"] = [round(math.exp(ci_low), 6), round(math.exp(ci_high), 6)]
    return out


def register(agent: Agent[AgentDeps]) -> None:
    @agent.tool
    async def bayesian_effect(
        ctx: RunContext[AgentDeps],
        estimate: float,
        se: float,
        threshold: float = 0.0,
        prior_mean: float = 0.0,
        prior_sd: float | None = None,
        cred_level: float = 0.95,
        exponentiate: bool = False,
    ) -> str:
        """
        Bayesian posterior probability of a treatment effect.

        estimate/se: the point estimate + standard error on the analysis
          scale (e.g. a log hazard ratio and its SE).
        threshold: decision boundary (default 0 = no effect). The tool
          reports P(effect < threshold) and P(effect > threshold).
        prior_mean/prior_sd: a Normal prior; omit prior_sd for a flat
          non-informative prior (posterior tail == the frequentist result).
        cred_level: credible-interval mass (default 0.95).
        exponentiate: also return exp(mean)+exp(CI) for log-scale effects
          (turns a log-HR into an HR).

        Returns JSON with the posterior mean/SD, credible interval, and the
        posterior probabilities either side of the threshold.
        """

        async def _impl() -> str:
            try:
                result = await asyncio.to_thread(
                    lambda: bayesian_posterior(
                        estimate=estimate,
                        se=se,
                        threshold=threshold,
                        prior_mean=prior_mean,
                        prior_sd=prior_sd,
                        cred_level=cred_level,
                        exponentiate=exponentiate,
                    )
                )
            except ValueError as e:
                return json.dumps({"error": str(e)})
            return json.dumps(result, ensure_ascii=False)

        return await emit_run(
            ctx,
            tool="bayesian_effect",
            icon="🎲",
            args={"estimate": estimate, "threshold": threshold},
            description="Computing Bayesian posterior for the treatment effect",
            impl=_impl,
        )


__all__ = ["bayesian_posterior", "register"]
