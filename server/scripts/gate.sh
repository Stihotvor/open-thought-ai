#!/usr/bin/env bash
# Quality gate for the server. Local runs, the pre-push hook and CI all call this script.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy src tests
uv run --locked pytest --cov --cov-report=term-missing
