FROM python:3.13-slim

# uv handles the venv + lockfile-pinned install. We install it via the
# official static binary so the build doesn't need pip's package cache.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Non-interactive apt; trim packages we don't need.
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# System deps:
#   - libpq for asyncpg's runtime
#   - docker-cli so the agent can talk to the host docker socket
#     (docker-out-of-docker — see architecture.md "Sandbox execution
#     dispatch" decision; local-dev only)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libpq5 \
        docker.io \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (better layer caching).
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

# Now copy the project source and install it.
COPY src ./src
RUN uv sync --frozen --no-dev

# Runtime config — overridable from compose / k8s.
ENV PATH="/app/.venv/bin:${PATH}" \
    APP_PORT=8000 \
    SANDBOX_WORK_DIR=/work/sandbox \
    IMAGES_DIR=/work/images \
    LIBRARY_RAW_DIR=/work/library

# /work/sandbox is bind-mounted from the host so sandbox bind mounts the
# agent issues to the host docker daemon resolve to real host paths.
# /work/images persists generated artefacts across container restarts.
RUN mkdir -p /work/sandbox /work/images /work/library

EXPOSE 8000

# Single-process uvicorn (no --reload in compose; rebuild on code change).
CMD ["uvicorn", "research_assistant.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
