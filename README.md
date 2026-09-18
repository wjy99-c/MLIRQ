# MLIRQ

Native MLIR foundations for **A Unified Compiler Infrastructure for
Heterogeneous Quantum Architectures**.

This is the first implementation milestone, M0: a C++/TableGen quantum
dialect, ownership verification, a logical optimization pass, and an initial
architecture contract. It accepts textual MLIR and produces verified textual
MLIR. Hardware execution, routing, native-gate lowering, HALO, and QLearn
integration remain future milestones.

## Implemented

- `!mlirq.qubit` represents a live quantum-state wire. Every value has exactly
  one consuming use; unused wires must be explicitly discarded.
- `mlirq.circuit` contains a closed, single-block circuit. Supported operations
  are `alloc`, `h`, `x`, `z`, `rz`, `cx`, terminal `measure`, `discard`, and
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

The GitHub Actions workflow uses the same Ubuntu/MLIR combination. It will
run when this source is added to a GitHub repository; no remote run is implied
by its inclusion.

## Try the compiler

```sh
# Parse and verify a Bell circuit.
build/bin/mlirq-opt examples/bell.mlir

# Six redundant gates disappear; allocation and measurement remain.
build/bin/mlirq-opt examples/optimize.mlir --mlirq-logical-opt

# Move logical IR to the initial architecture-aware representation.
build/bin/mlirq-opt examples/bell-target.mlir \
  --mlirq-logical-opt --mlirq-map-identity --mlirq-verify-target

# Inspect generic MLIR and the registered passes.
build/bin/mlirq-opt examples/bell.mlir --mlir-print-op-generic
build/bin/mlirq-opt --help
```

`examples/bell-target.mlir` uses a synthetic three-qubit line. It makes no
claim about a vendor device or its current calibration.

## Next implementation milestone

Implement L1 routing with an explicit final permutation and an equivalence
oracle that accounts for that permutation. Then introduce L2 native-gate
lowering and L3 executable output. The staged design and acceptance criteria
are in [docs/architecture.md](docs/architecture.md) and
[docs/roadmap.md](docs/roadmap.md).

Local build and test evidence is recorded in [docs/validation.md](docs/validation.md).
