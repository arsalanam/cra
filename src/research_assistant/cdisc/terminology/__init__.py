"""CDISC controlled-terminology lookup tables (top-6 #6).

JSON files shipped with the codebase. For production deployments using
the official NCI/CDISC terminology releases, swap these files at deploy
time — the mapper loads them via package data, not from a remote.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any


def load(name: str) -> dict[str, Any]:
    """Load a terminology JSON file from this package."""
    text = resources.files(__name__).joinpath(name).read_text(encoding="utf-8")
    obj: Any = json.loads(text)
    assert isinstance(obj, dict)
    return obj
