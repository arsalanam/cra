"""
model.py
────────────────────────────────────────────────────────────────────────────
Build the BedrockConverseModel from Settings. Pydantic AI supports many
providers; BedrockConverseModel wraps the boto3 Bedrock Converse API, which
supports streaming and tool use natively.
"""

from __future__ import annotations

from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.providers.bedrock import BedrockProvider

from ..config import Settings, get_settings


def build_bedrock_model(settings: Settings | None = None) -> BedrockConverseModel:
    """Construct a BedrockConverseModel from the given (or default) Settings."""
    s = settings or get_settings()
    return BedrockConverseModel(
        model_name=s.bedrock_model_id,
        provider=BedrockProvider(region_name=s.aws_region),
    )
