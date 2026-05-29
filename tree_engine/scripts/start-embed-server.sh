#!/usr/bin/env bash
# Start local Qwen3-Embedding-4B-Q8_0 embedding server (Mac / Linux)
#
# Usage:
#   ./tree_engine/scripts/start-embed-server.sh                  # all GPU layers (default)
#   ./tree_engine/scripts/start-embed-server.sh --n-gpu-layers 0 # CPU only
#   ./tree_engine/scripts/start-embed-server.sh --n-gpu-layers -1 # force all GPU
#   ./tree_engine/scripts/start-embed-server.sh --n-ctx 32768      # context length
#   ./tree_engine/scripts/start-embed-server.sh --n-seq-max 1      # parallel embedding sequences

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"

if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
  PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON:-python}"
fi

# Load config if present
if [ -f "$HOME/.tree/config.env" ]; then
  set -a; source "$HOME/.tree/config.env"; set +a
fi
if [ -f .env ]; then
  set -a; source .env; set +a
fi
if [ -f .tree/config.env ]; then
  set -a; source .tree/config.env; set +a
fi

PORT="${EMBED_PORT:-8788}"
N_GPU_LAYERS="${EMBED_N_GPU_LAYERS:--1}"
N_CTX="${EMBED_N_CTX:-32768}"
N_SEQ_MAX="${EMBED_N_SEQ_MAX:-1}"

# Export proxy for HuggingFace downloads (if set in config)
export HTTPS_PROXY="${HTTPS_PROXY:-${HTTP_PROXY:-}}"
export HTTP_PROXY="${HTTP_PROXY:-}"
export PYTHONPATH="$PROJECT_ROOT/tree_engine:${PYTHONPATH:-}"

echo "Starting Qwen3-Embedding-4B-Q8_0 embedding server on port $PORT (n_gpu_layers=$N_GPU_LAYERS, n_ctx=$N_CTX, n_seq_max=$N_SEQ_MAX)"
echo "Model: Qwen/Qwen3-Embedding-4B-GGUF / Qwen3-Embedding-4B-Q8_0.gguf"
echo "API endpoint: http://localhost:$PORT/v1/embeddings"
echo ""

exec "$PYTHON_BIN" -m rag.server --port "$PORT" --n-gpu-layers "$N_GPU_LAYERS" --n-ctx "$N_CTX" --n-seq-max "$N_SEQ_MAX" "$@"
