"""Scripts that run INSIDE the sandbox container.

Bundled with the package so the endpoint code can read them as text
and hand off to the sandbox executor — never imported in-process.
The host-side `_load_script` helper in `pipeline.py` / the survival
endpoint reads the file via `importlib.resources`.
"""

from __future__ import annotations
