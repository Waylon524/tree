#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"

if [[ ! -x "$PYTHON" ]]; then
  echo "Python test runtime not found: $PYTHON" >&2
  echo "Create .venv and install the project test dependencies first." >&2
  exit 2
fi

cd "$ROOT"
exec "$PYTHON" -m pytest "$@"
