"""
sandbox_exec tool — run Python code in a Docker container with data science packages.

Pre-installed: pandas, numpy, scipy, statsmodels, matplotlib, seaborn, forestplot.
Designed for clinical meta-analysis: forest plots, heterogeneity tests, funnel plots.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic_ai import Agent, RunContext

from ...agent.deps import AgentDeps
from ...config import get_settings
from .._emit import truncate

logger = logging.getLogger(__name__)

_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".svg")
_IMAGE_URL_PREFIX = "/images"

# Docker-out-of-docker path translation.
#
# When the agent runs inside a container and uses the host docker socket
# to spawn sandbox sibling containers, the bind-mount paths it passes to
# the daemon must be HOST paths — the daemon doesn't see the agent
# container's filesystem.
#
# Compose sets both:
#   SANDBOX_WORK_DIR=/work/sandbox                       (agent-internal)
#   HOST_SANDBOX_WORK_DIR=/abs/host/path/sandbox-work    (host-side)
#
# When the agent is run directly on the host (tests, ad-hoc), neither
# is set and the existing tempfile.mkdtemp() default applies — host and
# in-process paths match, no translation needed.
_SANDBOX_WORK_DIR_ENV = "SANDBOX_WORK_DIR"
_HOST_SANDBOX_WORK_DIR_ENV = "HOST_SANDBOX_WORK_DIR"


def _container_to_host_path(path: Path) -> str:
    """Translate an in-container sandbox tmpdir to its host equivalent.

    Returns the unchanged path string when no translation env is set,
    which is the host-run case (tests, plain `uv run`).
    """
    container_root = os.getenv(_SANDBOX_WORK_DIR_ENV)
    host_root = os.getenv(_HOST_SANDBOX_WORK_DIR_ENV)
    if not container_root or not host_root:
        return str(path)
    s = str(path)
    if not s.startswith(container_root):
        # tempfile.mkdtemp returned a path outside the configured root —
        # usually a misconfiguration. Pass through; daemon will reject.
        return s
    return host_root + s[len(container_root) :]


@dataclass
class SandboxResult:
    """Structured result from sandbox execution.

    For each output file, `files[<original-name>]` carries:
      • an image: the served URL path (e.g. `/images/<uuid>_forest.png`).
        The file itself is on disk under `settings.images_dir`.
      • a small text artefact (<50KB): its raw text content.
    Returning URL paths instead of data URIs keeps `Message.final_answer`
    small enough that re-sending it as conversation history doesn't blow
    up token usage.
    """

    stdout: str
    files: dict[str, str]
    error: str | None = None


async def _impl(
    code: str,
    input_data: str | None = None,
    input_format: str = "csv",
) -> SandboxResult:
    """Execute code in a Docker container and return structured results."""
    settings = get_settings()

    if not settings.sandbox_enabled:
        logger.warning("sandbox_exec called but sandbox is disabled")
        return SandboxResult(stdout="", files={}, error="Sandbox execution is disabled.")

    try:
        import docker
    except ImportError:
        logger.error("Docker SDK (docker-py) not installed")
        return SandboxResult(
            stdout="", files={}, error="Docker SDK not installed. Run: pip install docker"
        )

    # When SANDBOX_WORK_DIR is set (compose), tempfile.mkdtemp uses that
    # mount so the path is bind-mounted from the host. Otherwise default
    # tempdir behaviour (host runs / tests).
    work_root = os.getenv(_SANDBOX_WORK_DIR_ENV) or None
    tmpdir = tempfile.mkdtemp(prefix=f"sandbox-{uuid.uuid4().hex[:8]}-", dir=work_root)
    try:
        tmppath = Path(tmpdir)
        (tmppath / "input").mkdir()
        output_dir = tmppath / "output"
        output_dir.mkdir()
        # The sandbox container runs as the non-root `sandbox` user (UID
        # 1000, per sandbox/Dockerfile) while the agent runs as root in the
        # compose image. tempfile.mkdtemp() produces 0o700 root-owned dirs;
        # `mkdir()` on the subdirs honours the umask (typically 0o755 root-
        # owned), leaving the sandbox user without write permission on the
        # bind-mounted /home/sandbox/output. Explicitly widen JUST the output
        # subdir so the sandbox can write its artefacts (forest plots, etc.).
        # The parent tmpdir keeps its 0o700 mode — only the docker daemon
        # (as root) traverses it.
        output_dir.chmod(0o777)

        (tmppath / "script.py").write_text(code, encoding="utf-8")

        if input_data:
            ext = "json" if input_format == "json" else "csv"
            (tmppath / "input" / f"data.{ext}").write_text(input_data, encoding="utf-8")

        # Host-equivalent paths for the docker bind mounts. Identical to
        # the in-container paths when not running under compose.
        host_tmppath = _container_to_host_path(tmppath)

        logger.info(
            "Sandbox exec: image=%s, timeout=%ds, memory=%s, code=%d chars",
            settings.sandbox_image,
            settings.sandbox_timeout_seconds,
            settings.sandbox_memory_limit,
            len(code),
        )
        # Client HTTP timeout sits ABOVE the container wait timeout so the
        # explicit `wait(timeout=…)` below fires first with a clean error.
        client = docker.from_env(timeout=settings.sandbox_timeout_seconds + 30)

        # Container lifecycle: run detached, wait with a real timeout, then
        # force-remove in EVERY path (success, failure, turn-cancellation).
        # The previous `remove=True, detach=False` form leaked a
        # still-running container whenever the client-side wait timed out or
        # the turn was cancelled — a client HTTP timeout does not stop the
        # container, and docker-py's remove step only ran after a clean wait.
        holder: dict[str, Any] = {}

        def _run_container() -> tuple[int, bytes]:
            container = client.containers.run(
                image=settings.sandbox_image,
                volumes={
                    f"{host_tmppath}/script.py": {
                        "bind": "/home/sandbox/script.py",
                        "mode": "ro",
                    },
                    f"{host_tmppath}/input": {
                        "bind": "/home/sandbox/input",
                        "mode": "ro",
                    },
                    f"{host_tmppath}/output": {
                        "bind": "/home/sandbox/output",
                        "mode": "rw",
                    },
                },
                mem_limit=settings.sandbox_memory_limit,
                nano_cpus=int(settings.sandbox_cpu_count * 1e9),
                network_disabled=True,
                detach=True,
            )
            holder["container"] = container
            exit_info: dict[str, Any] = container.wait(timeout=settings.sandbox_timeout_seconds)
            logs: bytes = container.logs(stdout=True, stderr=True)
            return int(exit_info.get("StatusCode", -1)), logs

        def _force_remove(wait_for_handle: bool = False) -> None:
            """Kill + remove the container; idempotent and never raises.

            On turn-cancellation the worker thread may not have stored the
            handle yet — poll briefly so a just-created container is still
            reaped instead of orphaned.
            """
            if wait_for_handle:
                for _ in range(20):
                    if "container" in holder:
                        break
                    time.sleep(0.5)
            container = holder.get("container")
            if container is None:
                return
            try:
                container.remove(force=True)
            except Exception:
                # NotFound (already gone) or daemon hiccup — log and move on;
                # cleanup must never mask the primary result.
                logger.warning("Sandbox container cleanup failed", exc_info=True)

        try:
            exit_code, raw_logs = await asyncio.to_thread(_run_container)
        except asyncio.CancelledError:
            # Turn cancelled (agent wall-clock timeout). We cannot await
            # while cancelled — reap the container from a detached thread.
            threading.Thread(
                target=_force_remove, kwargs={"wait_for_handle": True}, daemon=True
            ).start()
            raise
        except Exception:
            # wait() timeout or daemon error — kill the container before
            # surfacing the error (outer handler formats the message).
            await asyncio.to_thread(_force_remove)
            raise
        else:
            await asyncio.to_thread(_force_remove)

        logger.info(
            "Container finished, exit_code=%d, output=%d bytes",
            exit_code,
            len(raw_logs) if raw_logs else 0,
        )

        stdout_text = (
            raw_logs.decode("utf-8", errors="replace")
            if isinstance(raw_logs, bytes)
            else str(raw_logs)
        )

        if exit_code != 0:
            # Parity with the old ContainerError path: a crashing script
            # surfaces as an error result (counts toward the tool-error
            # budget) with the output tail so the model can fix its code.
            tail = stdout_text[-2000:]
            return SandboxResult(
                stdout=stdout_text,
                files={},
                error=f"Sandbox script exited with code {exit_code}. Output tail:\n{tail}",
            )

        # Collect output files. Images move to a persistent disk location
        # (settings.images_dir) and are referenced by URL path; small text
        # files are inlined as before.
        output_files: dict[str, str] = {}
        output_dir = tmppath / "output"
        run_prefix = uuid.uuid4().hex[:12]
        images_dir = Path(settings.images_dir)
        images_dir.mkdir(parents=True, exist_ok=True)

        for fpath in output_dir.iterdir():
            if fpath.suffix.lower() in _IMAGE_SUFFIXES:
                stored_name = f"{run_prefix}_{fpath.name}"
                dest = images_dir / stored_name
                shutil.copyfile(fpath, dest)
                output_files[fpath.name] = f"{_IMAGE_URL_PREFIX}/{stored_name}"
            elif fpath.stat().st_size < 50_000:
                output_files[fpath.name] = fpath.read_text(encoding="utf-8", errors="replace")

        logger.info(
            "Sandbox produced %d output files: %s",
            len(output_files),
            list(output_files.keys()),
        )
        return SandboxResult(stdout=stdout_text, files=output_files)

    except Exception as e:
        logger.error("Sandbox execution failed: %s", e, exc_info=True)
        err = str(e)
        hint = ""
        if "No such image" in err or "not found" in err.lower():
            hint = (
                " Build the image first: "
                "docker build -t research-assistant-sandbox:latest ./sandbox"
            )
        elif "timed out" in err.lower() or "timeout" in err.lower():
            hint = (
                f" The script exceeded the {get_settings().sandbox_timeout_seconds}s "
                f"sandbox limit and the container was killed. Simplify the code "
                f"(smaller loops, lower dpi) or raise SANDBOX_TIMEOUT_SECONDS."
            )
        elif "connect" in err.lower() or "pipe" in err.lower() or "daemon" in err.lower():
            hint = " Docker Desktop does not appear to be running. Start it and try again."
        return SandboxResult(stdout="", files={}, error=f"Sandbox execution failed: {e}.{hint}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def register(agent: Agent[AgentDeps]) -> None:
    @agent.tool
    async def sandbox_exec(
        ctx: RunContext[AgentDeps],
        code: str,
        input_data: str | None = None,
        input_format: str = "csv",
    ) -> str:
        """
        Execute Python code in an isolated Docker sandbox with data science
        packages pre-installed (pandas, numpy, scipy, statsmodels, matplotlib,
        seaborn, forestplot). Use for meta-analysis computations, statistical
        tests, and generating plots (forest plots, funnel plots, etc.).

        The code runs as a standalone script. Available packages: pandas, numpy,
        scipy, statsmodels, matplotlib, seaborn, forestplot.

        If input_data is provided, it will be available at:
          /home/sandbox/input/data.csv  (if input_format="csv")
          /home/sandbox/input/data.json (if input_format="json")

        To save plots or output files, write them to /home/sandbox/output/
        For example: plt.savefig('/home/sandbox/output/forest_plot.png')

        Results include stdout and the filenames of any artefacts produced.
        Images are persisted to a server-side directory and referenced by
        URL — the LLM never sees the bytes.
        No network access is available inside the sandbox.
        """
        await ctx.deps.event_queue.put(
            {
                "type": "tool_start",
                "tool": "sandbox_exec",
                "icon": "D",
                "args": {"code": truncate(code, 200)},
                "description": "Running code in Docker sandbox...",
            }
        )

        result = await _impl(code, input_data, input_format)

        # Stash image URL paths in deps.artifacts so meta_analysis's
        # post-processor can substitute them into MetaAnalysisResults output.
        # Also emit on event_queue for legacy streaming UI compatibility.
        for fname, content in result.files.items():
            if isinstance(content, str) and content.startswith(f"{_IMAGE_URL_PREFIX}/"):
                logger.info("Image artifact: %s -> %s", fname, content)
                ctx.deps.artifacts[fname] = content
                await ctx.deps.event_queue.put(
                    {
                        "type": "artifact",
                        "tool": "sandbox_exec",
                        "filename": fname,
                        "artifact_type": "image",
                        "url": content,
                    }
                )
            elif isinstance(content, str):
                logger.info("Text artifact: %s (%d bytes)", fname, len(content))
                await ctx.deps.event_queue.put(
                    {
                        "type": "artifact",
                        "tool": "sandbox_exec",
                        "filename": fname,
                        "artifact_type": "text",
                        "content": content,
                    }
                )

        # Build the string returned to the LLM.
        # Strip large base64 data — the LLM doesn't need image bytes,
        # just the filenames so it can reference them in its answer.
        llm_result: dict[str, Any] = {"stdout": result.stdout}
        if result.error:
            llm_result["error"] = result.error
        if result.files:
            llm_result["files_generated"] = list(result.files.keys())
        llm_json = json.dumps(llm_result, ensure_ascii=False)

        await ctx.deps.event_queue.put(
            {
                "type": "tool_end",
                "tool": "sandbox_exec",
                "result_preview": truncate(llm_json),
            }
        )

        return llm_json
