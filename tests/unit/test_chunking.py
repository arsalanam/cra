"""Unit tests for section-aware chunking."""

from __future__ import annotations

from research_assistant.rag.chunking import chunk_text


def test_empty_text_yields_no_chunks() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_short_text_is_single_body_chunk() -> None:
    chunks = chunk_text("A short abstract about heart failure.")
    assert len(chunks) == 1
    assert chunks[0].section == "body"
    assert chunks[0].ordinal == 0
    assert chunks[0].token_count > 0


def test_section_headings_set_labels() -> None:
    text = (
        "Introduction\n\n"
        "We studied empagliflozin in heart failure patients.\n\n"
        "Methods\n\n"
        "A randomized double-blind trial enrolled 1000 patients.\n\n"
        "Results\n\n"
        "The hazard ratio for hospitalization was 0.75.\n\n"
        "Materials and Methods\n\n"
        "Assays were run in triplicate."
    )
    chunks = chunk_text(text)
    sections = {c.section for c in chunks}
    assert "introduction" in sections
    assert "methods" in sections
    assert "results" in sections
    # "Materials and Methods" normalises to methods, not a new label.
    assert "materials" not in sections


def test_long_text_splits_with_overlap() -> None:
    # 30 paragraphs well over the target window → multiple chunks.
    paras = [f"Paragraph {i} " + ("lorem ipsum " * 40) for i in range(30)]
    text = "\n\n".join(paras)
    chunks = chunk_text(text, target_tokens=128, overlap_tokens=20)

    assert len(chunks) > 1
    # Each chunk respects the target window (plus the carried overlap).
    for c in chunks:
        assert c.token_count <= 128 + 20 + 5  # small slack for paragraph boundaries
    # Ordinals are contiguous from 0.
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    # Overlap: the tail of one chunk reappears at the head of the next.
    assert chunks[1].text[:20] in chunks[0].text
