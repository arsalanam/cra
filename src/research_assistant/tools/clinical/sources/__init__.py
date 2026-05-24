"""Pluggable paper-source backends behind a shared Protocol."""

from .base import PaperSource
from .europepmc import EuropePMCSource
from .normalize import dedupe_studies
from .pubmed import PubmedSource
from .registry import get_enabled_sources, get_pubmed_source
from .schema import StudyRecord

__all__ = [
    "EuropePMCSource",
    "PaperSource",
    "PubmedSource",
    "StudyRecord",
    "dedupe_studies",
    "get_enabled_sources",
    "get_pubmed_source",
]
