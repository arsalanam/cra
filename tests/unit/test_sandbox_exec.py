"""Unit tests for the sandbox_exec tool (mocked Docker)."""

from __future__ import annotations

import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from research_assistant.tools.data_science.sandbox_exec import _impl


async def test_basic_execution() -> None:
    mock_client = MagicMock()
    mock_client.containers.run.return_value = b"hello world\n"

    with patch("docker.from_env", return_value=mock_client):
        result = await _impl("print('hello world')")

    assert "hello world" in result.stdout
    assert result.error is None


async def test_with_csv_input_data() -> None:
    mock_client = MagicMock()
    mock_client.containers.run.return_value = b"processed\n"

    with patch("docker.from_env", return_value=mock_client):
        result = await _impl(
            code="import pandas as pd; print(pd.read_csv('/home/sandbox/input/data.csv'))",
            input_data="name,age\nalice,30\nbob,25",
            input_format="csv",
        )

    assert "processed" in result.stdout
    mock_client.containers.run.assert_called_once()


async def test_with_json_input_data() -> None:
    mock_client = MagicMock()
    mock_client.containers.run.return_value = b"done\n"

    with patch("docker.from_env", return_value=mock_client):
        result = await _impl(
            code="import json; print('done')",
            input_data='[{"study": "A", "or": 1.5}]',
            input_format="json",
        )

    assert "done" in result.stdout


async def test_sandbox_disabled() -> None:
    with patch("research_assistant.tools.data_science.sandbox_exec.get_settings") as mock_settings:
        mock_settings.return_value.sandbox_enabled = False
        result = await _impl("print('hi')")

    assert result.error is not None
    assert "disabled" in result.error.lower()


async def test_docker_error_handled() -> None:
    mock_client = MagicMock()
    mock_client.containers.run.side_effect = Exception("container OOM killed")

    with patch("docker.from_env", return_value=mock_client):
        result = await _impl("print('hi')")

    assert result.error is not None
    assert "OOM" in result.error


async def test_docker_image_missing() -> None:
    mock_client = MagicMock()
    mock_client.containers.run.side_effect = Exception(
        "No such image: research-assistant-sandbox:latest"
    )

    with patch("docker.from_env", return_value=mock_client):
        result = await _impl("print('test')")

    assert result.error is not None
    assert "Build the image" in result.error


async def test_container_run_called_with_security_flags() -> None:
    mock_client = MagicMock()
    mock_client.containers.run.return_value = b"ok\n"

    with patch("docker.from_env", return_value=mock_client):
        await _impl("print('test')")

    call_kwargs = mock_client.containers.run.call_args
    assert call_kwargs.kwargs["network_disabled"] is True
    assert call_kwargs.kwargs["remove"] is True
    assert "mem_limit" in call_kwargs.kwargs
    assert "nano_cpus" in call_kwargs.kwargs


async def test_volumes_mounted() -> None:
    mock_client = MagicMock()
    mock_client.containers.run.return_value = b"ok\n"

    with patch("docker.from_env", return_value=mock_client):
        await _impl("print('test')", input_data="a,b\n1,2")

    call_kwargs = mock_client.containers.run.call_args
    volumes = call_kwargs.kwargs["volumes"]
    bind_paths = [v["bind"] for v in volumes.values()]
    assert "/home/sandbox/script.py" in bind_paths
    assert "/home/sandbox/input" in bind_paths
    assert "/home/sandbox/output" in bind_paths


async def test_result_has_no_files_when_empty() -> None:
    mock_client = MagicMock()
    mock_client.containers.run.return_value = b"ok\n"

    with patch("docker.from_env", return_value=mock_client):
        result = await _impl("print('test')")

    assert result.files == {}
    assert result.stdout == "ok\n"


async def test_image_outputs_are_saved_to_images_dir_as_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: forest-plot–style outputs end up on disk under
    settings.images_dir and are referenced by URL path, NOT by data URI.

    Simulates a Docker run by hooking the container call: at the moment
    the mock "container" returns, drop a fake PNG into the host-side
    output directory that `_impl` then scans.
    """
    images_dir = tmp_path / "images"
    monkeypatch.setenv("IMAGES_DIR", str(images_dir))

    captured: dict[str, Path] = {}

    def _fake_run(**kwargs: object) -> bytes:
        # Find the host path bound to /home/sandbox/output and drop a PNG.
        volumes = kwargs["volumes"]
        assert isinstance(volumes, dict)
        for host_path, bind_spec in volumes.items():
            if isinstance(bind_spec, dict) and bind_spec.get("bind") == "/home/sandbox/output":
                out = Path(str(host_path)) / "forest_plot_outcome_0.png"
                out.write_bytes(b"\x89PNG\r\n\x1a\nFAKE-IMAGE-BYTES")
                captured["host_output"] = Path(str(host_path))
                break
        return b"ok\n"

    mock_client = MagicMock()
    mock_client.containers.run.side_effect = _fake_run

    with patch("docker.from_env", return_value=mock_client):
        result = await _impl("plt.savefig('/home/sandbox/output/forest_plot_outcome_0.png')")

    assert "forest_plot_outcome_0.png" in result.files
    url = result.files["forest_plot_outcome_0.png"]
    assert url.startswith("/images/"), f"expected URL path, got {url!r}"
    assert "forest_plot_outcome_0.png" in url
    assert not url.startswith("data:"), "image must not be inlined as data URI"

    # The file actually exists on disk under the configured images_dir,
    # with the run-prefixed name from the URL.
    stored_name = url.removeprefix("/images/")
    assert (images_dir / stored_name).exists()
    assert (images_dir / stored_name).read_bytes().startswith(b"\x89PNG")


async def test_output_dir_is_world_writable_for_non_root_sandbox_user() -> None:
    """Regression: the agent container runs as root, the sandbox container runs as
    UID 1000. tempfile.mkdtemp() + plain mkdir() produces a 0o755 root-owned
    directory, leaving the sandbox user unable to write to /home/sandbox/output.
    The output subdir MUST be widened to 0o777 before the sandbox is spawned.

    `_impl` deletes its tmpdir on return via finally, so the mode is captured
    INSIDE the mocked docker.run callback (while the dir still exists) — the
    same moment a real sandbox container would be reading those perms.
    """
    captured_mode: dict[str, int] = {}

    def _capture_perms(**kwargs: object) -> bytes:
        volumes = kwargs["volumes"]
        assert isinstance(volumes, dict)
        for host_path, bind_spec in volumes.items():
            if isinstance(bind_spec, dict) and bind_spec.get("bind") == "/home/sandbox/output":
                captured_mode["output"] = stat.S_IMODE(Path(str(host_path)).stat().st_mode)
                break
        return b"ok\n"

    mock_client = MagicMock()
    mock_client.containers.run.side_effect = _capture_perms

    with patch("docker.from_env", return_value=mock_client):
        await _impl("print('hi')")

    mode = captured_mode.get("output")
    assert mode is not None, "no /home/sandbox/output bind mount was set up"
    # The world-write bit is the critical one — without it a non-root sandbox
    # user can't open files for writing under the bind-mounted output dir.
    assert mode & stat.S_IWOTH, (
        f"output dir mode is 0o{mode:o}; needs the world-write bit (0o002) so "
        f"the non-root sandbox user can write artefacts. See sandbox_exec.py "
        f"chmod call."
    )
