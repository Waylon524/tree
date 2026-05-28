#!/usr/bin/env bash
# Start local Qwen3-Embedding-4B-Q8_0 embedding server (Mac / Linux)
#
# Usage:
#   ./scripts/start-embed-server.sh                  # all GPU layers (default)
#   ./scripts/start-embed-server.sh --n-gpu-layers 0 # CPU only
#   ./scripts/start-embed-server.sh --n-gpu-layers -1 # force all GPU
#   ./scripts/start-embed-server.sh --n-ctx 32768      # context length
#   ./scripts/start-embed-server.sh --n-seq-max 1      # parallel embedding sequences

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

# Load .env if present
if [ -f .env ]; then
  set -a; source .env; set +a
fi

PORT="${EMBED_PORT:-8788}"
N_GPU_LAYERS="${EMBED_N_GPU_LAYERS:--1}"
N_CTX="${EMBED_N_CTX:-32768}"
N_SEQ_MAX="${EMBED_N_SEQ_MAX:-1}"

# Export proxy for HuggingFace downloads (if set in .env)
export HTTPS_PROXY="${HTTPS_PROXY:-${HTTP_PROXY:-}}"
export HTTP_PROXY="${HTTP_PROXY:-}"

echo "Starting Qwen3-Embedding-4B-Q8_0 embedding server on port $PORT (n_gpu_layers=$N_GPU_LAYERS, n_ctx=$N_CTX, n_seq_max=$N_SEQ_MAX)"
echo "Model: Qwen/Qwen3-Embedding-4B-GGUF / Qwen3-Embedding-4B-Q8_0.gguf"
echo "API endpoint: http://localhost:$PORT/v1/embeddings"
echo ""

exec python -m rag.server --port "$PORT" --n-gpu-layers "$N_GPU_LAYERS" --n-ctx "$N_CTX" --n-seq-max "$N_SEQ_MAX" "$@"
