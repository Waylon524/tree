#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Load .env if present
if [ -f "$HOME/.tree/config.env" ]; then
  set -a
  source "$HOME/.tree/config.env"
  set +a
fi
if [ -f "$PROJECT_ROOT/.env" ]; then
  set -a
  source "$PROJECT_ROOT/.env"
  set +a
fi
if [ -f "$PROJECT_ROOT/.tree/config.env" ]; then
  set -a
  source "$PROJECT_ROOT/.tree/config.env"
  set +a
fi

# Defaults
: "${PADDLEOCR_API_URL:?PADDLEOCR_API_URL not set — get it from https://aistudio.baidu.com/paddleocr/task}"
: "${PADDLEOCR_API_TOKEN:?PADDLEOCR_API_TOKEN not set}"

INPUT="${1:?Usage: run-ingest.sh <input_path> [output_dir]}"
OUTPUT="${2:-.tree/runtime/source_materials/}"

cd "$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT/tree_engine:${PYTHONPATH:-}"
if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
  PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON:-python}"
fi
"$PYTHON_BIN" -m ingest.pipeline --input "$INPUT" --output "$OUTPUT"
