#!/usr/bin/env bash
# Run the test suite with the full devenv environment (bwrap sandbox vars).
# Usage: scripts/cairn-test.sh [pytest args...]
set -euo pipefail
cd "$(dirname "$0")/.."
source .devenv/shell-env.sh >/dev/null 2>&1 || true
export PATH="$UV_PROJECT_ENVIRONMENT/bin:$PATH"
exec .devenv/state/venv/bin/python -m pytest "$@"
