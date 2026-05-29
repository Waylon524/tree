#!/usr/bin/env bash
# Bootstrap a fresh T.R.E.E. checkout through interactive workspace setup.
#
# Usage:
#   ./tree_engine/scripts/bootstrap.sh
#   ./tree_engine/scripts/bootstrap.sh --dev
#   ./tree_engine/scripts/bootstrap.sh --skip-setup
#   ./tree_engine/scripts/bootstrap.sh --force-embedding-rebuild

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

INSTALL_EXTRAS="rag"
RUN_SETUP=1
FORCE_EMBEDDING_REBUILD=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dev)
      INSTALL_EXTRAS="rag,dev"
      ;;
    --skip-setup)
      RUN_SETUP=0
      ;;
    --force-embedding-rebuild)
      FORCE_EMBEDDING_REBUILD=1
      ;;
    -h|--help)
      sed -n '1,12p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
  shift
done

cd "$PROJECT_ROOT"

log() {
  printf '\n==> %s\n' "$1"
}

fail() {
  printf '\nERROR: %s\n' "$1" >&2
  exit 1
}

require_project_root() {
  [ -f pyproject.toml ] || fail "Run this script from a cloned tree checkout, or use tree_engine/scripts/bootstrap.sh from the project root."
  [ -d tree_engine ] || fail "tree_engine/ is missing. The checkout looks incomplete."
  mkdir -p raw_materials finished_outputs tree_engine/.runtime
}

python_ok() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1
}

find_python() {
  if [ -n "${TREE_PYTHON:-}" ]; then
    python_ok "$TREE_PYTHON" && {
      printf '%s\n' "$TREE_PYTHON"
      return
    }
    fail "TREE_PYTHON is set but is not Python >= 3.12: $TREE_PYTHON"
  fi

  for candidate in python3.12 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && python_ok "$candidate"; then
      printf '%s\n' "$candidate"
      return
    fi
  done
  fail "Python >= 3.12 was not found. Install Python 3.12+, then rerun this script."
}

detect_device() {
  if [ "$(uname -s)" = "Darwin" ] && sysctl -n machdep.cpu.brand_string 2>/dev/null | grep -qi "apple"; then
    printf 'metal\n'
  elif command -v nvidia-smi >/dev/null 2>&1; then
    printf 'cuda\n'
  else
    printf 'cpu\n'
  fi
}

venv_python() {
  if [ -x .venv/bin/python ]; then
    printf '%s\n' "$PROJECT_ROOT/.venv/bin/python"
  else
    local py
    py="$(find_python)"
    log "Creating .venv with $py"
    "$py" -m venv .venv
    printf '%s\n' "$PROJECT_ROOT/.venv/bin/python"
  fi
}

verify_embedding_deps() {
  "$1" -c 'import llama_cpp, huggingface_hub, fastapi, uvicorn' >/dev/null 2>&1
}

require_project_root

DEVICE="$(detect_device)"
PYTHON_BIN="$(find_python)"
VENV_PYTHON="$(venv_python)"
export PYTHONPATH="$PROJECT_ROOT/tree_engine:${PYTHONPATH:-}"

log "Device profile"
echo "Project root: $PROJECT_ROOT"
echo "System: $(uname -s) $(uname -m)"
echo "Python: $("$PYTHON_BIN" --version)"
echo "Embedding device hint: $DEVICE"

log "Installing Python package and dependencies"
"$VENV_PYTHON" -m pip install -U pip
"$VENV_PYTHON" -m pip install ".[${INSTALL_EXTRAS}]"

if [ "$FORCE_EMBEDDING_REBUILD" -eq 1 ] || ! verify_embedding_deps "$VENV_PYTHON"; then
  log "Embedding dependencies need setup"
  "$SCRIPT_DIR/setup-embedding.sh" --device "$DEVICE"
fi

log "Verifying CLI and embedding imports"
"$VENV_PYTHON" -c 'import tree, ingest, rag; print("packages ok")'
"$VENV_PYTHON" -c 'import llama_cpp, huggingface_hub, fastapi, uvicorn; print("embedding deps ok")'
"$VENV_PYTHON" -m tree.cli --help >/dev/null

if [ "$RUN_SETUP" -eq 1 ]; then
  log "Starting workspace setup"
  "$VENV_PYTHON" -m tree.cli setup
else
  log "Skipping workspace setup"
fi

cat <<'NEXT'

Bootstrap complete.

Next:
  1. Start the embedding server in one terminal:
       ./tree_engine/scripts/start-embed-server.sh
  2. Open another terminal, activate .venv, then run:
       source .venv/bin/activate
       tree-run run
NEXT
