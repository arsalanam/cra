"""Workflow-scoped last-kind lookup (web/dispatch._last_assistant_kind).

A general_qa detour mid-meta-analysis must not feed its "answer" kind into
the meta-analysis prepare_tools stage gate (review finding A1b).
"""

from __future__ import annotations

import json

from research_assistant.persistence.models import Message
from research_assistant.web.dispatch import _last_assistant_kind


def _msg(role: str, kind: str | None = None, workflow: str | None = None) -> Message:
    return Message(
        thread_id="t-1",
        role=role,
        input_text=None if role == "assistant" else "hi",
        final_answer=json.dumps({"kind": kind}) if kind else None,
        workflow=workflow,
    )


def test_unscoped_returns_most_recent_kind() -> None:
    msgs = [
        _msg("assistant", kind="pico", workflow="meta_analysis"),
        _msg("user"),
        _msg("assistant", kind="answer", workflow="general_qa"),
    ]
    assert _last_assistant_kind(msgs) == "answer"


def test_scoped_skips_foreign_workflow_turns() -> None:
    """The detour's 'answer' must not shadow the workflow's own stage."""
    msgs = [
        _msg("assistant", kind="data_extraction", workflow="meta_analysis"),
        _msg("user"),
        _msg("assistant", kind="answer", workflow="general_qa"),  # detour
    ]
    assert _last_assistant_kind(msgs, workflow="meta_analysis") == "data_extraction"


def test_scoped_returns_none_when_workflow_has_no_turns() -> None:
    msgs = [_msg("assistant", kind="answer", workflow="general_qa")]
    assert _last_assistant_kind(msgs, workflow="meta_analysis") is None


def test_legacy_null_workflow_rows_count_for_any_workflow() -> None:
    """Rows written before Message.workflow existed keep working."""
    msgs = [_msg("assistant", kind="search_results", workflow=None)]
    assert _last_assistant_kind(msgs, workflow="meta_analysis") == "search_results"
