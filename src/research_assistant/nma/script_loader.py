"""Read packaged sandbox NMA scripts as text."""

from __future__ import annotations

from importlib import resources
from typing import Final, Literal

NmaScriptKind = Literal["frequentist", "bayesian", "geometry"]


AVAILABLE_NMA_SCRIPTS: Final[tuple[NmaScriptKind, ...]] = (
    "frequentist",
    "bayesian",
    "geometry",
)


def load_script(kind: NmaScriptKind) -> str:
    """Return the canonical NMA Python script for `kind` as text."""
    if kind not in AVAILABLE_NMA_SCRIPTS:
        raise FileNotFoundError(
            f"Unknown NMA script {kind!r}. Available: {', '.join(AVAILABLE_NMA_SCRIPTS)}."
        )
    script_path = resources.files(__package__).joinpath("sandbox_scripts", f"{kind}.py")
    return script_path.read_text(encoding="utf-8")


__all__ = ["AVAILABLE_NMA_SCRIPTS", "NmaScriptKind", "load_script"]
