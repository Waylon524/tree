#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load .env if present
if [ -f "$PROJECT_ROOT/.env" ]; then
  set -a
  source "$PROJECT_ROOT/.env"
  set +a
fi

# Defaults
: "${PADDLEOCR_API_URL:?PADDLEOCR_API_URL not set — get it from https://aistudio.baidu.com/paddleocr/task}"
: "${PADDLEOCR_API_TOKEN:?PADDLEOCR_API_TOKEN not set}"

INPUT="${1:?Usage: run-ingest.sh <input_path> [output_dir]}"
OUTPUT="${2:-source_materials/}"

cd "$PROJECT_ROOT"
python -m ingest.pipeline --input "$INPUT" --output "$OUTPUT"
