"""Cross-platform `just clean`: remove tool caches and coverage artefacts."""

from __future__ import annotations

import shutil
from pathlib import Path

DIRS = ("htmlcov", ".pytest_cache", ".mypy_cache", ".ruff_cache")
FILES = (".coverage", "coverage.xml")


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    for name in DIRS:
        target = root / name
        if target.is_dir():
            shutil.rmtree(target)
            print(f"removed {name}/")
    for name in FILES:
        target = root / name
        if target.is_file():
            target.unlink()
            print(f"removed {name}")
    for pycache in (root / "src").rglob("__pycache__"):
        shutil.rmtree(pycache)
    for pycache in (root / "tests").rglob("__pycache__"):
        shutil.rmtree(pycache)
    for pycache in (root / "scripts").rglob("__pycache__"):
        shutil.rmtree(pycache)
    print("clean")


if __name__ == "__main__":
    main()
