"""
describe_image tool — download an image and describe it using Claude Sonnet via Bedrock.
"""

from __future__ import annotations

import logging

import httpx
from pydantic_ai import Agent, RunContext

from ...agent.deps import AgentDeps
from ...config import get_settings
from .._emit import emit_run

logger = logging.getLogger(__name__)

_SUPPORTED_FORMATS = {
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}

_MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB


def _guess_format(url: str, content_type: str) -> str | None:
    ct = content_type.lower().split(";")[0].strip()
    if ct in _SUPPORTED_FORMATS:
        return _SUPPORTED_FORMATS[ct]
    url_lower = url.lower().split("?")[0]
    for ext in ("jpeg", "jpg", "png", "gif", "webp"):
        if url_lower.endswith(f".{ext}"):
            return "jpeg" if ext == "jpg" else ext
    return None


async def _impl(url: str) -> str:
    """Download an image and send it to Sonnet for a textual description."""
    import boto3

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            resp = await client.get(url, headers={"User-Agent": "ResearchAssistant/1.0"})
            resp.raise_for_status()

            if len(resp.content) > _MAX_IMAGE_BYTES:
                return f"Image too large ({len(resp.content)} bytes, max {_MAX_IMAGE_BYTES})."

            content_type = resp.headers.get("content-type", "")
            fmt = _guess_format(url, content_type)
            if fmt is None:
                return (
                    f"Unsupported image format (Content-Type: {content_type}). "
                    "Supported: JPEG, PNG, GIF, WebP."
                )

            image_bytes = resp.content

        settings = get_settings()
        bedrock = boto3.client("bedrock-runtime", region_name=settings.aws_region)

        response = bedrock.converse(
            modelId=settings.vision_model_id,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "image": {
                            "format": fmt,
                            "source": {"bytes": image_bytes},
                        },
                    },
                    {
                        "text": (
                            "Describe this image in detail. Include: the main subject, "
                            "visual elements, any text visible in the image, colors, "
                            "composition, and context. Be factual and thorough."
                        ),
                    },
                ],
            }],
            inferenceConfig={"maxTokens": 1024, "temperature": 0.2},
        )

        output_parts = response.get("output", {}).get("message", {}).get("content", [])
        description = " ".join(
            part["text"] for part in output_parts if "text" in part
        )

        logger.info("Image described: %s (%d chars)", url[:80], len(description))
        return description or "No description generated."

    except httpx.HTTPStatusError as e:
        logger.warning("HTTP %d downloading image %s", e.response.status_code, url[:80])
        return f"Failed to download image: HTTP {e.response.status_code}"
    except Exception as e:
        logger.error("Image description failed for %s: %s", url[:80], e, exc_info=True)
        return f"Image description failed: {e}"


def register(agent: Agent[AgentDeps]) -> None:
    @agent.tool
    async def describe_image(ctx: RunContext[AgentDeps], url: str) -> str:
        """
        Download an image from a URL and return a detailed textual description
        using Claude Sonnet's vision capabilities. Use when a search result or
        Wikipedia article contains an image that needs to be understood. Supports
        JPEG, PNG, GIF, and WebP formats.
        """
        return await emit_run(
            ctx,
            tool="describe_image",
            icon="I",
            args={"url": url},
            description=f"Analyzing image: {url[:80]}...",
            impl=lambda: _impl(url),
        )
