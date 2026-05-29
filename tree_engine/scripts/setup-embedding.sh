#!/usr/bin/env bash
# Install llama-cpp-python with GPU acceleration for embedding service
#
# Usage:
#   ./tree_engine/scripts/setup-embedding.sh              # auto-detect (Metal on Mac, CUDA on Linux)
#   ./tree_engine/scripts/setup-embedding.sh --device cpu # CPU only (no GPU)
#   ./tree_engine/scripts/setup-embedding.sh --device metal # Force Metal (Apple Silicon)
#   ./tree_engine/scripts/setup-embedding.sh --device cuda # Force CUDA (NVIDIA)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"

if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
  PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON:-python}"
fi

DEVICE="auto"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --device)
      DEVICE="${2:-}"
      if [ -z "$DEVICE" ]; then
        echo "Missing value for --device"
        exit 2
      fi
      shift 2
      ;;
    --device=*)
      DEVICE="${1#--device=}"
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [--device auto|cpu|metal|cuda]"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      echo "Usage: $0 [--device auto|cpu|metal|cuda]"
      exit 2
      ;;
  esac
done

if [ "$DEVICE" = "auto" ]; then
  if [[ "$(uname)" == "Darwin" ]] && sysctl -n machdep.cpu.brand_string 2>/dev/null | grep -qi "apple"; then
    DEVICE="metal"
  elif command -v nvidia-smi &>/dev/null; then
    DEVICE="cuda"
  else
    DEVICE="cpu"
  fi
fi

echo "=== Installing llama-cpp-python ($DEVICE) ==="

# Load .env for proxy
if [ -f .env ]; then
  set -a; source .env; set +a
fi
export HTTPS_PROXY="${HTTPS_PROXY:-${HTTP_PROXY:-}}"
export HTTP_PROXY="${HTTP_PROXY:-}"
export PYTHONPATH="$PROJECT_ROOT/tree_engine:${PYTHONPATH:-}"

case "$DEVICE" in
  metal)
    echo "Compiling with Metal support..."
    CMAKE_ARGS="-DGGML_METAL=on" FORCE_CMAKE=1 \
      "$PYTHON_BIN" -m pip install "llama-cpp-python>=0.3.0" --force-reinstall --no-cache-dir
    ;;
  cuda)
    echo "Compiling with CUDA support..."
    CMAKE_ARGS="-DGGML_CUDA=on" FORCE_CMAKE=1 \
      "$PYTHON_BIN" -m pip install "llama-cpp-python>=0.3.0" --force-reinstall --no-cache-dir
    ;;
  cpu)
    echo "Installing CPU-only version..."
    "$PYTHON_BIN" -m pip install "llama-cpp-python>=0.3.0" --force-reinstall --no-cache-dir
    ;;
  *)
    echo "Unknown device: $DEVICE"
    echo "Usage: $0 [--device auto|cpu|metal|cuda]"
    exit 1
    ;;
esac

echo ""
echo "Installing remaining dependencies..."
"$PYTHON_BIN" -m pip install "huggingface-hub>=0.20.0" "fastapi>=0.111.0" "uvicorn>=0.30.0"

echo ""
echo "=== Verifying installation ==="
"$PYTHON_BIN" -c "
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
echo "  ./tree_engine/scripts/start-embed-server.sh"
