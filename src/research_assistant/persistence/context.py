"""Convert persisted messages to pydantic-ai ModelMessage objects for context."""

from __future__ import annotations

import re

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from .models import Message

# Match any JSON string value that is a data: URI (base64-encoded images,
# PDFs, etc.). Source specialists sometimes inline these into a structured
# output field (e.g. `forest_plot_image`); persisted into `final_answer`
# they would be re-sent on every subsequent turn as conversation history,
# adding hundreds of thousands of tokens per Bedrock call. Strip them at
# the history-rebuild boundary — the frontend already has the artifact
# from the first render.
_DATA_URI_RE = re.compile(r'"data:[^"]*"')
_DATA_URI_PLACEHOLDER = '"[binary artifact omitted from agent context]"'


def _strip_data_uris(final_answer: str) -> str:
    return _DATA_URI_RE.sub(_DATA_URI_PLACEHOLDER, final_answer)


def messages_to_history(
    messages: list[Message],
    thread_summary: str | None = None,
) -> list[ModelMessage]:
    """Build a pydantic-ai message_history from stored messages.

    If the thread has a summary (from a prior summarisation pass), it is
    prepended as a system-style user message so the agent has context
    about the truncated earlier conversation.
    """
    history: list[ModelMessage] = []

    if thread_summary:
        history.append(
            ModelRequest(
                parts=[UserPromptPart(content=f"[Previous conversation summary]: {thread_summary}")]
            )
        )

    for msg in messages:
        if msg.role == "user" and msg.input_text:
            history.append(ModelRequest(parts=[UserPromptPart(content=msg.input_text)]))
        elif msg.role == "assistant" and msg.final_answer:
            history.append(
                ModelResponse(parts=[TextPart(content=_strip_data_uris(msg.final_answer))])
            )

    return history
