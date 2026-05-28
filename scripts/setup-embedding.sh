#!/usr/bin/env bash
# Install llama-cpp-python with GPU acceleration for embedding service
#
# Usage:
#   ./scripts/setup-embedding.sh              # auto-detect (Metal on Mac, CUDA on Linux)
#   ./scripts/setup-embedding.sh --device cpu # CPU only (no GPU)
#   ./scripts/setup-embedding.sh --device metal # Force Metal (Apple Silicon)
#   ./scripts/setup-embedding.sh --device cuda # Force CUDA (NVIDIA)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

DEVICE="${1:---device auto}"

if [ "$DEVICE" = "--device auto" ]; then
  if [[ "$(uname)" == "Darwin" ]] && sysctl -n machdep.cpu.brand_string 2>/dev/null | grep -qi "apple"; then
    DEVICE="--device metal"
  elif command -v nvidia-smi &>/dev/null; then
    DEVICE="--device cuda"
  else
    DEVICE="--device cpu"
  fi
fi

echo "=== Installing llama-cpp-python ($DEVICE) ==="

# Load .env for proxy
if [ -f .env ]; then
  set -a; source .env; set +a
fi
export HTTPS_PROXY="${HTTPS_PROXY:-${HTTP_PROXY:-}}"
export HTTP_PROXY="${HTTP_PROXY:-}"

case "$DEVICE" in
  --device metal)
    echo "Compiling with Metal support..."
    CMAKE_ARGS="-DGGML_METAL=on" FORCE_CMAKE=1 \
      pip install "llama-cpp-python>=0.3.0" --force-reinstall --no-cache-dir
    ;;
  --device cuda)
    echo "Compiling with CUDA support..."
    CMAKE_ARGS="-DGGML_CUDA=on" FORCE_CMAKE=1 \
      pip install "llama-cpp-python>=0.3.0" --force-reinstall --no-cache-dir
    ;;
  --device cpu)
    echo "Installing CPU-only version..."
    pip install "llama-cpp-python>=0.3.0" --force-reinstall --no-cache-dir
    ;;
  *)
    echo "Unknown device: $DEVICE"
    echo "Usage: $0 [--device auto|cpu|metal|cuda]"
    exit 1
    ;;
esac

echo ""
echo "Installing remaining dependencies..."
pip install "huggingface-hub>=0.20.0" "fastapi>=0.111.0" "uvicorn>=0.30.0"

echo ""
echo "=== Verifying installation ==="
python -c "
from llama_cpp import Llama
print(f'llama-cpp-python: OK')
from huggingface_hub import hf_hub_download
print(f'huggingface-hub: OK')
"

echo ""
echo "=== Setup complete ==="
echo "Model will be auto-downloaded from HuggingFace on first server start:"
echo "  Repo:   Qwen/Qwen3-Embedding-4B-GGUF"
echo "  File:   Qwen3-Embedding-4B-Q8_0.gguf (~4.3 GB)"
echo ""
echo "Start the server with:"
echo "  ./scripts/start-embed-server.sh"
