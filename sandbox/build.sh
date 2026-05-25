#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
docker build -t research-assistant-sandbox:latest "$SCRIPT_DIR"
echo "Done — image: research-assistant-sandbox:latest"
