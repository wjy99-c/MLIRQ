#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$repo_dir"
: "${MLIR_DIR:=/usr/lib/llvm-18/lib/cmake/mlir}"
: "${LLVM_DIR:=/usr/lib/llvm-18/lib/cmake/llvm}"
if ! command -v cmake >/dev/null || ! command -v ninja >/dev/null || [ ! -d "$MLIR_DIR" ]; then
  echo 'Install CMake, Ninja, LLVM/MLIR 18 first (see docs/experiments.md), or set MLIR_DIR and LLVM_DIR.' >&2
  exit 1
fi
"${PYTHON:-python3}" -m venv .venv
.venv/bin/python -m pip install -r experiments/requirements.txt -e '.[experiments]'
cmake -S . -B build -G Ninja -DMLIR_DIR="$MLIR_DIR" -DLLVM_DIR="$LLVM_DIR" -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel "${BUILD_JOBS:-2}"
ctest --test-dir build --output-on-failure
.venv/bin/python scripts/fetch-qrisk.py
MLIRQ_OPT="$repo_dir/build/bin/mlirq-opt" .venv/bin/python -m unittest discover -s test/python -q
echo 'Ready. Run scripts/run-rq1.sh through scripts/run-rq4.sh.'
