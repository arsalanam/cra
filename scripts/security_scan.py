"""Dependency CVE audit over the committed lockfile.

Exports ``uv.lock`` to a temporary requirements file and runs pip-audit
against it (via ``uvx``, so pip-audit is never a project dependency).
Nothing is written into the working tree.

Set ``BLOCKING = True`` to make dependency CVEs fail ``just qa`` / CI.
It starts advisory so a new upstream CVE cannot break unrelated PRs;
flip it once the baseline is clean and triaged.

Extension points (deliberately not implemented yet):
- ``trivy image research-assistant-agent`` after a compose build
- OWASP ZAP baseline scan against a running stack
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

BLOCKING = False


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        req = Path(tmp) / "requirements.txt"
        subprocess.run(  # noqa: S603 — fixed argv, no untrusted input
            [  # noqa: S607 — invoking uv from PATH is intended
                "uv",
                "export",
                "--format",
                "requirements-txt",
                "--no-emit-project",
                "--all-extras",
                "--quiet",
                "-o",
                str(req),
            ],
            check=True,
        )
        audit = subprocess.run(  # noqa: S603 — fixed argv, no untrusted input
            ["uvx", "pip-audit", "-r", str(req), "--disable-pip"],  # noqa: S607
            check=False,
        )

    if audit.returncode != 0:
        if BLOCKING:
            print("security: pip-audit found vulnerabilities (BLOCKING)")
            return audit.returncode
        print("security: pip-audit found vulnerabilities (advisory — not failing the build)")
    else:
        print("security: no known vulnerabilities in locked dependencies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
