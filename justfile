# Quality gates and dev loop for CRA. `just qa` is the single source of
# truth — CI runs exactly this file, so green locally means green on GitHub.
# Every recipe is one cross-platform command; logic lives in scripts/*.py.

set windows-shell := ["cmd.exe", "/c"]

compose := "docker compose -f deploy/compose/docker-compose.yml"

# List available recipes
default:
    @just --list

# One-time bootstrap: locked venv with dev tools
setup:
    uv sync --all-extras

# ── Quality gates ──────────────────────────────────────────────────────────

# Full test suite (in-memory SQLite, no Docker needed)
test:
    uv run pytest -q

# Tests with coverage; enforces the fail_under ratchet from pyproject.toml
coverage:
    uv run pytest -q --cov --cov-report=term --cov-report=html

# Ruff lint over everything that is Python
lint:
    uv run ruff check src tests scripts

# Formatting must already be clean (CI posture)
format-check:
    uv run ruff format --check src tests scripts

# Fix lint + reformat in one go (local convenience)
format:
    uv run ruff check src tests scripts --fix && uv run ruff format src tests scripts

# mypy strict over src/
typecheck:
    uv run mypy src

# Dependency CVE audit over uv.lock (pip-audit via uvx)
security:
    uv run python scripts/security_scan.py

# All gates, in the order CI runs them. `coverage` IS the test run —
# running the suite twice (bare + covered) would double CI time.
qa: coverage lint format-check typecheck security
    @echo all quality gates passed

# ── Runtime (compose-only; see CLAUDE.md) ─────────────────────────────────

# Build the sandbox image the data-science tools depend on
sandbox:
    docker build -t research-assistant-sandbox:latest ./sandbox

# Start the stack (postgres + clinical-postgres + agent)
up:
    {{compose}} up -d --build

# Follow agent logs
logs:
    {{compose}} logs -f agent

# Stop the stack
down:
    {{compose}} down

# Remove tool caches and coverage artefacts
clean:
    uv run python scripts/dev_clean.py
