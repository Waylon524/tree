#!/usr/bin/env bash
# Build the standalone `tre-engine` sidecar binary with PyInstaller (Phase 1).
# Run on each target OS (no cross-compile). Output: packaging/dist/tre-engine/.
set -euo pipefail
cd "$(dirname "$0")/.."

python -m PyInstaller --noconfirm --clean \
  --distpath packaging/dist --workpath packaging/build \
  packaging/tre-engine.spec

ENGINE="packaging/dist/tre-engine/tre-engine"
if [[ "${OS:-}" == "Windows_NT" ]]; then
  ENGINE="${ENGINE}.exe"
fi
"$ENGINE" doctor --strict

echo "Built: packaging/dist/tre-engine/tre-engine (run: tre-engine serve --port 8799 --token <t>)"
