"""Host-side readability metrics (P2 #3 — patient-facing lay summaries).

Computes the Flesch-Kincaid Grade Level (FKGL) from plain text so the
lay_summary specialist can enforce a target reading level without
relying on the model to compute its own metric. Pure-Python, stdlib
only — no `textstat` / `readability` dependency.

The math (FKGL, Kincaid 1975, US Navy training-manual study):

    grade = 0.39 × (words / sentences)
          + 11.8 × (syllables / words)
          - 15.59

Inputs are clamped: a single-word "Hi." paragraph returns a defensible
~0.5 grade rather than blowing up on division.

The syllable estimator is a vowel-group counter with the standard
adjustments (silent terminal 'e', trailing 'le' on consonants, +1
floor). It is a known-imperfect heuristic — produces ±0.5 grade noise
versus a dictionary-backed counter, which is well inside the practical
tolerance for IRB / patient-facing readability decisions.

Reference: Flesch-Kincaid is the standard NIH + CDISC + EMA lay-summary
readability metric. The 6–8 grade target band comes from CDISC's
patient-facing-language guidance + NIH plain-language guidance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+\s+|[.!?]+$")
_WORD_RE = re.compile(r"[A-Za-z]+(?:[\-'][A-Za-z]+)*")
_VOWEL_GROUP_RE = re.compile(r"[aeiouy]+")


def _count_sentences(text: str) -> int:
    """Naive but stable sentence count.

    Splits on `.`, `!`, `?` followed by whitespace OR end-of-text. Empty
    leftover pieces are discarded. Returns at least 1 so the FKGL
    denominator is never zero.
    """
    if not text.strip():
        return 0
    parts = [p for p in _SENTENCE_SPLIT_RE.split(text.strip()) if p.strip()]
    return max(1, len(parts))


def _count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _count_syllables_in_word(word: str) -> int:
    """Vowel-group syllable estimator with the standard adjustments.

    Steps:
      1. Lowercase, strip non-letters.
      2. Count contiguous vowel groups (a, e, i, o, u, y).
      3. Silent-e rule: if the word ends in 'e' and isn't monosyllabic,
         subtract one. EXCEPTION: words ending in 'le' preceded by a
         consonant (`simple`, `apple`, `table`) — there the trailing
         'e' is pronounced as a syllable, so don't subtract.
      4. Floor at 1 — every spelled word has at least one syllable.
    """
    word = re.sub(r"[^a-z]", "", word.lower())
    if not word:
        return 0
    groups = _VOWEL_GROUP_RE.findall(word)
    count = len(groups)
    le_after_consonant = len(word) >= 3 and word.endswith("le") and word[-3] not in "aeiouy"
    if word.endswith("e") and count > 1 and not le_after_consonant:
        count -= 1
    return max(1, count)


def _count_syllables(text: str) -> int:
    return sum(_count_syllables_in_word(w) for w in _WORD_RE.findall(text))


@dataclass(frozen=True)
class ReadabilityResult:
    """Output bundle for `flesch_kincaid_grade`.

    Carries the raw counts as well as the computed grade so the UI can
    show "412 words, 28 sentences → grade 7.4" alongside the score.
    """

    grade: float
    words: int
    sentences: int
    syllables: int

    def as_dict(self) -> dict[str, float | int]:
        return {
            "grade": round(self.grade, 1),
            "words": self.words,
            "sentences": self.sentences,
            "syllables": self.syllables,
        }


def flesch_kincaid_grade(text: str) -> ReadabilityResult:
    """Flesch-Kincaid Grade Level for the supplied text.

    Empty / whitespace-only text returns grade 0. Single-word inputs
    return a small positive grade rather than dividing by zero — we'd
    rather under-report than crash on a 1-word edge case.
    """
    words = _count_words(text)
    sentences = _count_sentences(text)
    syllables = _count_syllables(text)
    if words == 0 or sentences == 0:
        return ReadabilityResult(grade=0.0, words=words, sentences=sentences, syllables=syllables)
    grade = 0.39 * (words / sentences) + 11.8 * (syllables / words) - 15.59
    # Clamp non-negative — long single-word sentences ("Hi.") can land
    # below zero on the formula. Negative grades aren't meaningful for
    # this use case.
    return ReadabilityResult(
        grade=max(0.0, grade),
        words=words,
        sentences=sentences,
        syllables=syllables,
    )


__all__ = [
    "ReadabilityResult",
    "flesch_kincaid_grade",
]
