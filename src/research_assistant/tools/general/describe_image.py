"""
describe_image tool — describe the image the user attached to this turn,
using the vision model (Claude Sonnet) via Bedrock.

History: this tool originally took a URL and downloaded the image itself.
That let the model go fishing for web images (paywalled / bot-blocked
forest plots → 403 churn), so it was removed from general_qa and
meta_analysis entirely. It is now upload-only: the bytes come from
``AgentDeps.image_content`` (populated by the /api/turn endpoint when the
user attaches an image) and the tool is hidden by ``prepare_tools`` on
turns without an attachment. There is no URL parameter any more — the
fishing path is structurally gone.
"""

from __future__ import annotations

import logging

from pydantic_ai import Agent, RunContext

from ...agent.deps import AgentDeps
from ...config import get_settings
from .._emit import emit_run

logger = logging.getLogger(__name__)

# MIME type → Bedrock Converse image format token.
SUPPORTED_MEDIA_TYPES = {
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}

MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB


def format_from_media_type(media_type: str | None) -> str | None:
    """Map an attachment MIME type to the Bedrock format token (or None)."""
    if not media_type:
        return None
    return SUPPORTED_MEDIA_TYPES.get(media_type.lower().split(";")[0].strip())


async def _impl(
    image_bytes: bytes,
    media_type: str | None,
    question: str,
    deps: AgentDeps | None = None,
) -> str:
    """Send the attached image to the vision model for a description.

    When ``deps`` is supplied, the converse call's token usage is
    accumulated onto it so the turn's spend ledger can price vision
    separately from the main model (T1 spend quota).
    """
    import boto3

    fmt = format_from_media_type(media_type)
    if fmt is None:
        return (
            f"Unsupported image format ({media_type or 'unknown'}). "
            "Supported: JPEG, PNG, GIF, WebP."
        )
    if len(image_bytes) > MAX_IMAGE_BYTES:
        return f"Image too large ({len(image_bytes)} bytes, max {MAX_IMAGE_BYTES})."

    prompt = (
        "Describe this image in detail. Include: the main subject, visual "
        "elements, any text visible in the image, colors, composition, and "
        "context. Be factual and thorough."
    )
    if question.strip():
        prompt += f"\n\nThe user specifically wants to know: {question.strip()}"

    try:
        settings = get_settings()
        bedrock = boto3.client("bedrock-runtime", region_name=settings.aws_region)
        response = bedrock.converse(
            modelId=settings.vision_model_id,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "image": {
                                "format": fmt,
                                "source": {"bytes": image_bytes},
                            },
                        },
                        {"text": prompt},
                    ],
                }
            ],
            inferenceConfig={"maxTokens": 1024, "temperature": 0.2},
        )
        if deps is not None:
            usage = response.get("usage", {})
            deps.vision_input_tokens += int(usage.get("inputTokens", 0) or 0)
            deps.vision_output_tokens += int(usage.get("outputTokens", 0) or 0)
        output_parts = response.get("output", {}).get("message", {}).get("content", [])
        description = " ".join(part["text"] for part in output_parts if "text" in part)
        logger.info(
            "Attached image described (%d bytes, %d chars)", len(image_bytes), len(description)
        )
        return description or "No description generated."
    except Exception as e:
        logger.error("Image description failed: %s", e, exc_info=True)
        return f"Image description failed: {e}"


def register(agent: Agent[AgentDeps]) -> None:
    @agent.tool
    async def describe_image(ctx: RunContext[AgentDeps], question: str = "") -> str:
        """
        Describe the image the user attached to this message using the vision
        model. Only available on turns where the user actually attached an
        image (e.g. a forest plot screenshot, a figure from a paper, a lab
        report photo). Pass `question` to focus the description on what the
        user asked about.
        """
        image = ctx.deps.image_content
        if image is None:
            return (
                "No image is attached to this turn. Ask the user to attach "
                "the image to their message."
            )
        return await emit_run(
            ctx,
            tool="describe_image",
            icon="I",
            args={"question": question, "bytes": len(image)},
            description="Analyzing the attached image…",
            impl=lambda: _impl(image, ctx.deps.image_media_type, question, deps=ctx.deps),
        )
