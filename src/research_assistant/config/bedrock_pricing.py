"""AWS Bedrock pricing constants for Claude models (P2 #6 budget rollup).

Per-model USD per 1K tokens. Source: AWS Bedrock public pricing page as
of 2026-05; verify before relying on these for invoicing.

The pricing dict is keyed by both the SHORT model id (e.g.
"claude-haiku-4-5") and the FULL inference-profile id (e.g.
"us.anthropic.claude-haiku-4-5-20251001-v1:0") because the platform's
`settings.bedrock_model_id` carries the long form by default. `lookup`
normalises both shapes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelPricing:
    """USD per 1K tokens for one model."""

    input_per_1k_usd: float
    output_per_1k_usd: float
    family: str  # "haiku" | "sonnet" | "opus" — for rollup grouping
    label: str  # human-readable name


# AWS Bedrock public pricing — verify before invoicing.
# Source: aws.amazon.com/bedrock/pricing/ as of 2026-05.
_PRICING_BY_FAMILY: Final[dict[str, ModelPricing]] = {
    # Claude 4 family
    "haiku-4-5": ModelPricing(
        input_per_1k_usd=0.0008,
        output_per_1k_usd=0.004,
        family="haiku",
        label="Claude Haiku 4.5",
    ),
    "sonnet-4-6": ModelPricing(
        input_per_1k_usd=0.003,
        output_per_1k_usd=0.015,
        family="sonnet",
        label="Claude Sonnet 4.6",
    ),
    "opus-4-7": ModelPricing(
        input_per_1k_usd=0.015,
        output_per_1k_usd=0.075,
        family="opus",
        label="Claude Opus 4.7",
    ),
    # Claude 3 family (legacy — kept for thread history that predates the
    # 4.x migration).
    "haiku-3-5": ModelPricing(
        input_per_1k_usd=0.0008,
        output_per_1k_usd=0.004,
        family="haiku",
        label="Claude 3.5 Haiku",
    ),
    "sonnet-3-5": ModelPricing(
        input_per_1k_usd=0.003,
        output_per_1k_usd=0.015,
        family="sonnet",
        label="Claude 3.5 Sonnet",
    ),
    "opus-3": ModelPricing(
        input_per_1k_usd=0.015,
        output_per_1k_usd=0.075,
        family="opus",
        label="Claude 3 Opus",
    ),
}


# Sensible default when the model id can't be resolved (e.g. a new
# Bedrock model that ships before the pricing table is updated). We err
# on the side of overestimating cost so the budget rollup is the
# conservative reading.
_DEFAULT_PRICING: Final[ModelPricing] = _PRICING_BY_FAMILY["sonnet-4-6"]


def _normalise(model_id: str) -> str:
    """Map a Bedrock model id (short or full inference-profile) onto the
    family key used by `_PRICING_BY_FAMILY`.

    Examples:
      "claude-haiku-4-5"                                    → "haiku-4-5"
      "us.anthropic.claude-haiku-4-5-20251001-v1:0"        → "haiku-4-5"
      "anthropic.claude-3-5-sonnet-20240620-v1:0"          → "sonnet-3-5"
      "claude-opus-4-7"                                     → "opus-4-7"
    """
    if not model_id:
        return ""
    lower = model_id.lower()
    # Direct match against family keys.
    for key in _PRICING_BY_FAMILY:
        if key in lower:
            return key
    # Fallback ordering matters: more specific (4-x) before legacy 3-x
    # so "claude-3-5-sonnet" doesn't get caught by "sonnet" alone.
    for family in ("haiku-4-5", "sonnet-4-6", "opus-4-7"):
        if family in lower:
            return family
    if "haiku" in lower and "3" in lower:
        return "haiku-3-5"
    if "sonnet" in lower and "3-5" in lower:
        return "sonnet-3-5"
    if "opus" in lower and "3" in lower:
        return "opus-3"
    return ""


def lookup(model_id: str | None) -> ModelPricing:
    """Return pricing for a Bedrock model id, falling back to the default."""
    if not model_id:
        return _DEFAULT_PRICING
    key = _normalise(model_id)
    if key in _PRICING_BY_FAMILY:
        return _PRICING_BY_FAMILY[key]
    logger.debug(
        "bedrock_pricing: no pricing entry for model_id=%r — using "
        "Sonnet 4.6 as a conservative default",
        model_id,
    )
    return _DEFAULT_PRICING


def compute_message_cost(
    *, input_tokens: int, output_tokens: int, model_id: str | None
) -> float:
    """Return USD cost for one message turn."""
    pricing = lookup(model_id)
    return (
        input_tokens / 1000.0 * pricing.input_per_1k_usd
        + output_tokens / 1000.0 * pricing.output_per_1k_usd
    )


def known_families() -> tuple[str, ...]:
    """Distinct family names in the pricing table — useful for tests +
    rollup grouping."""
    return tuple(sorted({p.family for p in _PRICING_BY_FAMILY.values()}))


__all__ = [
    "ModelPricing",
    "compute_message_cost",
    "known_families",
    "lookup",
]
