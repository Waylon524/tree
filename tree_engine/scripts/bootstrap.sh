#!/usr/bin/env bash
# Bootstrap a fresh T.R.E.E. checkout through interactive workspace setup.
#
# Usage:
#   ./tree_engine/scripts/bootstrap.sh
#   ./tree_engine/scripts/bootstrap.sh --dev
#   ./tree_engine/scripts/bootstrap.sh --skip-setup
#   ./tree_engine/scripts/bootstrap.sh --skip-embedding-start
#   ./tree_engine/scripts/bootstrap.sh --force-embedding-rebuild

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

INSTALL_EXTRAS="rag"
RUN_SETUP=1
FORCE_EMBEDDING_REBUILD=0
START_EMBEDDING=1

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dev)
      INSTALL_EXTRAS="rag,dev"
      ;;
    --skip-setup)
      RUN_SETUP=0
      ;;
    --skip-embedding-start)
      START_EMBEDDING=0
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

log_stderr() {
  printf '\n==> %s\n' "$1" >&2
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
    log_stderr "Creating .venv with $py"
    "$py" -m venv .venv
    printf '%s\n' "$PROJECT_ROOT/.venv/bin/python"
  fi
}

verify_embedding_deps() {
  "$1" -c 'import llama_cpp, huggingface_hub, fastapi, uvicorn' >/dev/null 2>&1
}

embedding_ready() {
  "$VENV_PYTHON" -c 'from pathlib import Path; from tree.services import embedding_health; raise SystemExit(0 if embedding_health(Path.cwd())[0] else 1)' >/dev/null 2>&1
}

embedding_pid_running() {
  local pid_file="tree_engine/.runtime/services/embedding.pid"
  [ -f "$pid_file" ] || return 1
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" >/dev/null 2>&1
}

start_embedding_with_progress() {
  log "Starting embedding server in the background"
  "$VENV_PYTHON" -m tree.cli start-embedding --no-wait

  local log_file="tree_engine/.runtime/services/embedding.log"
  local tail_pid=""
  stop_log_tail() {
    if [ -n "$tail_pid" ]; then
      kill "$tail_pid" >/dev/null 2>&1 || true
      wait "$tail_pid" 2>/dev/null || true
      tail_pid=""
    fi
  }

  if command -v tail >/dev/null 2>&1; then
    touch "$log_file"
    echo "Showing embedding server log while it starts. First launch may download ~4.3 GB."
    tail -n 40 -f "$log_file" &
    tail_pid="$!"
  else
    echo "Embedding log: $PROJECT_ROOT/$log_file"
  fi

  while ! embedding_ready; do
    if ! embedding_pid_running; then
      stop_log_tail
      fail "Embedding server exited before becoming ready. Check $PROJECT_ROOT/$log_file"
    fi
    sleep 2
  done

  stop_log_tail
  echo ""
  echo "Embedding server is ready."
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

if [ "$START_EMBEDDING" -eq 1 ]; then
  start_embedding_with_progress
else
  log "Skipping embedding server startup"
fi

cat <<'NEXT'

Bootstrap complete.

Next:
  1. Put course files into raw_materials/
  2. Open the TREE interactive CLI:
       .venv/bin/tree-run
  3. Type slash commands inside TREE:
       /continue
       /status
       /stop
       /quit

Tip:
  After running: source .venv/bin/activate
  you can use: tree-run
NEXT
