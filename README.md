# MLIRQ

Native MLIR foundations for **A Unified Compiler Infrastructure for
Heterogeneous Quantum Architectures**.

MLIRQ provides a C++/TableGen quantum dialect, ownership verification,
logical optimization, an initial architecture contract, and backend-specific
[QRisk](https://github.com/qzydustin/qrisk) pattern scanning and disruption.
It accepts textual MLIR and produces verified textual MLIR. Hardware execution,
routing, full native-gate lowering, and HALO remain future milestones.

## Implemented

- `!mlirq.qubit` represents a live quantum-state wire. Every value has exactly
  one consuming use; unused wires must be explicitly discarded.
- `mlirq.circuit` contains a closed, single-block circuit. Supported operations
  are `alloc`, `h`, `x`, `z`, `sx`, `rz`, `cx`, `cz`, terminal `measure`, `discard`, and
  `output`.
- `--mlirq-logical-opt` removes adjacent H/H, X/X, Z/Z, and CX/CX inverse
  pairs. It preserves opaque operation annotations by leaving annotated pairs
  unchanged.
- `--mlirq-map-identity` assigns physical indices in allocation order and
  validates the entire module before committing. It rejects circuits that
  need routing or exceed target capacity.
- `--mlirq-verify-target` verifies physical placement, uniqueness, connectivity,
  and CX direction. These checks also run automatically when parsing an
  architecture-stage circuit.
- `--mlirq-qrisk-scan` matches a local catalog against the exact backend and
  physical qubits. `--mlirq-qrisk-mitigate` breaks occurrences through verified
  commuting reorders, preserving ideal semantics and reporting unresolved hits.
- `tools/import_qrisk.py` imports QRisk DDMin reports or pattern memories. The
  supplied catalog contains three historical observations from the actual
  upstream repository, with source revisions, timestamps, and file hashes.
- CTest regression tests invoke the native compiler. A Python standard-library
  oracle compares complex amplitudes for optimized random circuits on every
  three-qubit basis input.

## Build on Ubuntu 24.04

The initial API compatibility target is **LLVM/MLIR 18.x**. Keep LLVM and MLIR
on the same major version. Later releases need a deliberate compatibility
update; the build rejects them instead of assuming API compatibility.

```sh
sudo apt-get update
sudo apt-get install -y cmake ninja-build g++ python3 \
  libmlir-18-dev mlir-18-tools llvm-18-dev

cmake -S . -B build -G Ninja \
  -DMLIR_DIR=/usr/lib/llvm-18/lib/cmake/mlir \
  -DLLVM_DIR=/usr/lib/llvm-18/lib/cmake/llvm \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel 2
ctest --test-dir build --output-on-failure
```

Alternatively, build and test in the supplied container:

```sh
docker build -t mlirq .
docker run --rm -i mlirq --mlirq-logical-opt < examples/optimize.mlir
```

The GitHub Actions workflow uses the same Ubuntu/MLIR combination and runs
the native regression and import suites on pushes and pull requests.

## Try the compiler

```sh
# Parse and verify a Bell circuit.
build/bin/mlirq-opt examples/bell.mlir

# Six redundant gates disappear; allocation and measurement remain.
build/bin/mlirq-opt examples/optimize.mlir --mlirq-logical-opt

# Move logical IR to the initial architecture-aware representation.
build/bin/mlirq-opt examples/bell-target.mlir \
  --mlirq-logical-opt --mlirq-map-identity --mlirq-verify-target

# Disrupt two occurrences of an actual QRisk Fez observation.
# This example is already physically mapped; do not run identity mapping again.
build/bin/mlirq-opt examples/qrisk-fez.mlir \
  --mlirq-qrisk-mitigate="patterns-file=patterns/qrisk-imported.json" \
  --mlirq-verify-target

# Inspect generic MLIR and the registered passes.
build/bin/mlirq-opt examples/bell.mlir --mlir-print-op-generic
build/bin/mlirq-opt --help
```

`examples/bell-target.mlir` uses a synthetic three-qubit line. It makes no
claim about a vendor device or its current calibration.

The QRisk example reports `before_total = 2`, `after_total = 0`, and
`status = "eliminated"`. Its repeated pattern is a test harness built from a
published observation, not the original discovery workload. No hardware
fidelity improvement is claimed. See [docs/qrisk.md](docs/qrisk.md) for the
catalog, import commands, matching rules, and remaining limits. Toy patterns
remain only in the regression fixtures.

## Next implementation milestone

Implement L1 routing with an explicit final permutation and an equivalence
oracle that accounts for that permutation. Then introduce L2 native-gate
lowering and L3 executable output. The staged design and acceptance criteria
are in [docs/architecture.md](docs/architecture.md) and
[docs/roadmap.md](docs/roadmap.md).

Local build and test evidence is recorded in [docs/validation.md](docs/validation.md).
