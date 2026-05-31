"""Read packaged sandbox IPD scripts as text."""

from __future__ import annotations

from importlib import resources
from typing import Final, Literal

IpdScriptKind = Literal["one_stage", "two_stage", "subgroup"]


AVAILABLE_IPD_SCRIPTS: Final[tuple[IpdScriptKind, ...]] = (
    "one_stage",
    "two_stage",
    "subgroup",
)


def load_script(kind: IpdScriptKind) -> str:
    """Return the canonical IPD analysis script for `kind` as text."""
    if kind not in AVAILABLE_IPD_SCRIPTS:
        raise FileNotFoundError(
            f"Unknown IPD script {kind!r}. Available: {', '.join(AVAILABLE_IPD_SCRIPTS)}."
        )
    script_path = resources.files(__package__).joinpath("sandbox_scripts", f"{kind}.py")
    return script_path.read_text(encoding="utf-8")


__all__ = ["AVAILABLE_IPD_SCRIPTS", "IpdScriptKind", "load_script"]
