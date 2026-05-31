"""Host-side Flesch-Kincaid readability service (P2 #2 lay summaries)."""

from __future__ import annotations

import pytest

from research_assistant.services.readability import (
    ReadabilityResult,
    flesch_kincaid_grade,
)

# ── Trivial edges ─────────────────────────────────────────────────────────


def test_empty_text_returns_zero() -> None:
    r = flesch_kincaid_grade("")
    assert r.grade == 0.0
    assert r.words == 0
    assert r.sentences == 0


def test_whitespace_only_returns_zero() -> None:
    r = flesch_kincaid_grade("   \n\t  ")
    assert r.grade == 0.0
    assert r.words == 0


def test_single_short_sentence_no_crash() -> None:
    r = flesch_kincaid_grade("Hi there.")
    assert r.words == 2
    assert r.sentences == 1
    assert r.grade >= 0.0


# ── Sentence + word + syllable counts ─────────────────────────────────────


def test_word_count_handles_punctuation() -> None:
    r = flesch_kincaid_grade("The cat sat on the mat. Then it slept.")
    assert r.words == 9
    assert r.sentences == 2


def test_hyphenated_word_counts_as_one() -> None:
    r = flesch_kincaid_grade("Self-care matters.")
    assert r.words == 2


def test_apostrophe_word_counts_as_one() -> None:
    r = flesch_kincaid_grade("She doesn't smoke.")
    assert r.words == 3


def test_syllable_counter_silent_e() -> None:
    """`make` is 1 syllable, `simple` is 2 (`le` rule fires)."""
    from research_assistant.services.readability import _count_syllables_in_word

    assert _count_syllables_in_word("make") == 1
    assert _count_syllables_in_word("simple") == 2
    assert _count_syllables_in_word("the") == 1


def test_syllable_counter_le_after_consonant() -> None:
    from research_assistant.services.readability import _count_syllables_in_word

    assert _count_syllables_in_word("apple") == 2
    assert _count_syllables_in_word("table") == 2


def test_syllable_counter_floor_one() -> None:
    from research_assistant.services.readability import _count_syllables_in_word

    # No vowel groups in 'pst' → still floored to 1.
    assert _count_syllables_in_word("pst") == 1


# ── Grade level — known shapes ────────────────────────────────────────────


def test_short_simple_text_lands_low_grade() -> None:
    """5–6 short words per sentence + 1-syllable words → low grade."""
    r = flesch_kincaid_grade("I like to read. The cat sat on the mat. We eat food.")
    assert r.grade < 5.0


def test_long_words_push_grade_higher_than_short_words() -> None:
    """At constant sentence length, polysyllabic vocabulary raises FK."""
    short = flesch_kincaid_grade("She ate the food. He drank the milk. They went home.")
    long_ = flesch_kincaid_grade(
        "She consumed the nutriment. He metabolised the lactose. They proceeded homeward."
    )
    assert long_.grade > short.grade


def test_long_sentence_pushes_grade_higher_than_short_sentences() -> None:
    """At constant vocabulary, longer sentences raise FK."""
    short_sentences = flesch_kincaid_grade(
        "The cat ran. The dog ran. The bird flew. The fish swam."
    )
    long_sentence = flesch_kincaid_grade(
        "The cat ran and the dog ran and the bird flew and the fish swam."
    )
    assert long_sentence.grade > short_sentences.grade


# ── ReadabilityResult.as_dict ─────────────────────────────────────────────


def test_as_dict_rounds_grade_one_decimal() -> None:
    r = ReadabilityResult(grade=7.234567, words=100, sentences=8, syllables=160)
    d = r.as_dict()
    assert d["grade"] == 7.2
    assert d["words"] == 100
    assert d["sentences"] == 8
    assert d["syllables"] == 160


# ── No accidental crashes on weird inputs ─────────────────────────────────


@pytest.mark.parametrize(
    "txt",
    [
        "???",
        "...",
        "12345",
        "!@#$%",
        "\n\n\n",
        "a",  # single letter
        "A. B. C. D.",  # all 1-letter sentences
    ],
)
def test_no_crash_on_pathological_inputs(txt: str) -> None:
    r = flesch_kincaid_grade(txt)
    assert r.grade >= 0.0
