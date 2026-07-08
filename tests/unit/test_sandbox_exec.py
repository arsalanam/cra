"""Unit tests for the sandbox_exec tool (mocked Docker).

The container runs detached: `containers.run(detach=True)` returns a
container handle, `_impl` waits on it with a real timeout, reads logs, and
force-removes it in every path (success, failure, turn-cancellation) —
see the Phase-4 audit in docs/agent-loop-review.md.
"""

from __future__ import annotations

import asyncio
import stat
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from research_assistant.tools.data_science.sandbox_exec import _impl


def _mock_container(logs: bytes = b"ok\n", exit_code: int = 0) -> MagicMock:
    container = MagicMock()
    container.wait.return_value = {"StatusCode": exit_code}
    container.logs.return_value = logs
    return container


def _mock_client(container: MagicMock) -> MagicMock:
    client = MagicMock()
    client.containers.run.return_value = container
    return client


async def test_basic_execution() -> None:
    container = _mock_container(logs=b"hello world\n")
    with patch("docker.from_env", return_value=_mock_client(container)):
        result = await _impl("print('hello world')")

    assert "hello world" in result.stdout
    assert result.error is None


async def test_with_csv_input_data() -> None:
    container = _mock_container(logs=b"processed\n")
    client = _mock_client(container)
    with patch("docker.from_env", return_value=client):
        result = await _impl(
            code="import pandas as pd; print(pd.read_csv('/home/sandbox/input/data.csv'))",
            input_data="name,age\nalice,30\nbob,25",
            input_format="csv",
        )

    assert "processed" in result.stdout
    client.containers.run.assert_called_once()


async def test_with_json_input_data() -> None:
    container = _mock_container(logs=b"done\n")
    with patch("docker.from_env", return_value=_mock_client(container)):
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
    client = MagicMock()
    client.containers.run.side_effect = Exception("container OOM killed")

    with patch("docker.from_env", return_value=client):
        result = await _impl("print('hi')")

    assert result.error is not None
    assert "OOM" in result.error


async def test_docker_image_missing() -> None:
    client = MagicMock()
    client.containers.run.side_effect = Exception(
        "No such image: research-assistant-sandbox:latest"
    )

    with patch("docker.from_env", return_value=client):
        result = await _impl("print('test')")

    assert result.error is not None
    assert "Build the image" in result.error


async def test_container_run_called_with_security_flags() -> None:
    container = _mock_container()
    client = _mock_client(container)
    with patch("docker.from_env", return_value=client):
        await _impl("print('test')")

    call_kwargs = client.containers.run.call_args
    assert call_kwargs.kwargs["network_disabled"] is True
    assert call_kwargs.kwargs["detach"] is True
    assert "mem_limit" in call_kwargs.kwargs
    assert "nano_cpus" in call_kwargs.kwargs


async def test_volumes_mounted() -> None:
    container = _mock_container()
    client = _mock_client(container)
    with patch("docker.from_env", return_value=client):
        await _impl("print('test')", input_data="a,b\n1,2")

    call_kwargs = client.containers.run.call_args
    volumes = call_kwargs.kwargs["volumes"]
    bind_paths = [v["bind"] for v in volumes.values()]
    assert "/home/sandbox/script.py" in bind_paths
    assert "/home/sandbox/input" in bind_paths
    assert "/home/sandbox/output" in bind_paths


async def test_result_has_no_files_when_empty() -> None:
    container = _mock_container()
    with patch("docker.from_env", return_value=_mock_client(container)):
        result = await _impl("print('test')")

    assert result.files == {}
    assert result.stdout == "ok\n"


# ── Container lifecycle (Phase-4 audit) ──────────────────────────────────


async def test_container_force_removed_after_success() -> None:
    container = _mock_container()
    with patch("docker.from_env", return_value=_mock_client(container)):
        await _impl("print('hi')")

    container.remove.assert_called_once_with(force=True)


async def test_nonzero_exit_returns_error_and_removes_container() -> None:
    container = _mock_container(logs=b"Traceback ...\nValueError: bad dpi\n", exit_code=1)
    with patch("docker.from_env", return_value=_mock_client(container)):
        result = await _impl("raise ValueError('bad dpi')")

    assert result.error is not None
    assert "exited with code 1" in result.error
    assert "ValueError" in result.error  # output tail included for the model
    container.remove.assert_called_once_with(force=True)


async def test_wait_timeout_kills_container() -> None:
    """A hung script: wait() raises a read timeout — the container must be
    force-removed (previously it kept running forever) and the error must
    carry the sandbox-timeout hint."""
    container = _mock_container()
    container.wait.side_effect = Exception(
        "UnixHTTPConnectionPool(host='localhost'): Read timed out. (read timeout=60)"
    )
    with patch("docker.from_env", return_value=_mock_client(container)):
        result = await _impl("while True: pass")

    assert result.error is not None
    assert "was killed" in result.error
    container.remove.assert_called_once_with(force=True)


async def test_turn_cancellation_reaps_container() -> None:
    """Turn-level asyncio.wait_for cancellation abandons the worker thread —
    a detached cleanup thread must still force-remove the container."""
    started = threading.Event()
    release = threading.Event()

    container = MagicMock()
    container.logs.return_value = b""

    def _blocking_wait(**kwargs: Any) -> dict[str, int]:
        started.set()
        release.wait(5)
        return {"StatusCode": 0}

    container.wait.side_effect = _blocking_wait

    with patch("docker.from_env", return_value=_mock_client(container)):
        task = asyncio.create_task(_impl("while True: pass"))
        await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not container.remove.called:
            await asyncio.sleep(0.05)
        release.set()  # unblock the abandoned worker thread

    assert container.remove.called
    assert container.remove.call_args.kwargs.get("force") is True


async def test_image_outputs_are_saved_to_images_dir_as_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: forest-plot–style outputs end up on disk under
    settings.images_dir and are referenced by URL path, NOT by data URI.

    Simulates a Docker run by hooking the container call: at the moment
    the mock "container" is spawned, drop a fake PNG into the host-side
    output directory that `_impl` then scans.
    """
    images_dir = tmp_path / "images"
    monkeypatch.setenv("IMAGES_DIR", str(images_dir))

    def _fake_run(**kwargs: object) -> MagicMock:
        # Find the host path bound to /home/sandbox/output and drop a PNG.
        volumes = kwargs["volumes"]
        assert isinstance(volumes, dict)
        for host_path, bind_spec in volumes.items():
            if isinstance(bind_spec, dict) and bind_spec.get("bind") == "/home/sandbox/output":
                out = Path(str(host_path)) / "forest_plot_outcome_0.png"
                out.write_bytes(b"\x89PNG\r\n\x1a\nFAKE-IMAGE-BYTES")
                break
        return _mock_container()

    client = MagicMock()
    client.containers.run.side_effect = _fake_run

    with patch("docker.from_env", return_value=client):
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

    def _capture_perms(**kwargs: object) -> MagicMock:
        volumes = kwargs["volumes"]
        assert isinstance(volumes, dict)
        for host_path, bind_spec in volumes.items():
            if isinstance(bind_spec, dict) and bind_spec.get("bind") == "/home/sandbox/output":
                captured_mode["output"] = stat.S_IMODE(Path(str(host_path)).stat().st_mode)
                break
        return _mock_container()

    client = MagicMock()
    client.containers.run.side_effect = _capture_perms

    with patch("docker.from_env", return_value=client):
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
