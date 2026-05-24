#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
docker build -t pydantic-sandbox:latest "$SCRIPT_DIR"
echo "Done — image: pydantic-sandbox:latest"
