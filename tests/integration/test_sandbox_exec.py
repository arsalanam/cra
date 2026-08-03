"""Integration tests for sandbox_exec — requires Docker + research-assistant-sandbox image."""

from __future__ import annotations

import shutil
import subprocess

import pytest


def _sandbox_image_exists() -> bool:
    """Check if the research-assistant-sandbox:latest Docker image is available."""
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", "research-assistant-sandbox:latest"],  # noqa: S607 — invoking docker from PATH is intended
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _sandbox_image_exists(),
    reason="Docker not available or research-assistant-sandbox:latest image not built",
)


async def test_real_pandas_execution() -> None:
    from research_assistant.tools.data_science.sandbox_exec import _impl

    code = """\
import pandas as pd
df = pd.read_csv('/home/sandbox/input/data.csv')
print(df.describe().to_string())
"""
    result = await _impl(
        code=code,
        input_data="x,y\n1,2\n3,4\n5,6\n7,8",
        input_format="csv",
    )
    assert result.error is None
    assert "mean" in result.stdout


async def test_plot_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    """Image artefacts land on disk under images_dir and are referenced by URL.

    Previously they were inlined as base64 data URIs — that path was
    retired (it blew up token usage when persisted into Message.final_answer
    and re-sent as conversation history). The new contract is a URL path
    served at /images/<uuid>_<name>.
    """
    from research_assistant.tools.data_science.sandbox_exec import _impl

    # Route the produced PNG to a per-test temp dir so we don't pollute
    # the project's images/ directory.
    images_dir = str(tmp_path)
    monkeypatch.setenv("IMAGES_DIR", images_dir)

    code = """\
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.plot([1, 2, 3], [1, 4, 9])
plt.savefig('/home/sandbox/output/plot.png')
print('plot saved')
"""
    result = await _impl(code=code)
    assert result.error is None
    assert "plot saved" in result.stdout
    assert "plot.png" in result.files
    url = result.files["plot.png"]
    assert url.startswith("/images/"), f"expected URL path, got {url!r}"
    assert not url.startswith("data:"), "must not inline data URI"
    # The file is actually on disk under the configured images_dir.
    stored_name = url.removeprefix("/images/")
    from pathlib import Path

    assert (Path(images_dir) / stored_name).exists()
