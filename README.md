# MLIRQ

Native MLIR foundations for **A Unified Compiler Infrastructure for
Heterogeneous Quantum Architectures**.

MLIRQ's first milestone is a **post-Qiskit compilation pass**: take a circuit
that Qiskit has already compiled for a backend, apply equivalent transformations
to reduce occurrences of that backend's [QRisk](https://github.com/qzydustin/qrisk)
patterns, and return an optimized Qiskit circuit with a before/after report.

| Boundary | M1 contract |
| --- | --- |
| Input | A backend-compiled Qiskit `QuantumCircuit`, its exact backend identity and target, and a QRisk pattern catalog |
| Processing | Match backend/physical-qubit patterns and apply semantics-preserving commuting reorders |
| Output | A Qiskit `QuantumCircuit` for the same target, preserving physical placement, measurement mapping, and ideal semantics, plus pattern counts and unresolved matches |

The first supported input subset will be static circuits with bound parameters,
after Qiskit's layout, routing, gate translation, and optimization, before timing
scheduling. The caller performs Qiskit compilation before invoking MLIRQ.
MLIRQ does not rerun those stages in this workflow.

**Current status: M1 is implemented for the supported circuit subset.**
`optimize_compiled_circuit()` connects import, native mitigation, checked export,
output Target validation, and an independent final rescan. It returns a new
Qiskit circuit and structured report. Experiment runners for RQ1–RQ4 and an
explicit hardware prepare/submit/collect workflow are in
[docs/experiments.md](docs/experiments.md). Hardware efficacy remains unevaluated.

## Implemented

- `!mlirq.qubit` represents a live quantum-state wire. Every value has exactly
  one consuming use; unused wires must be explicitly discarded.
- `mlirq.circuit` contains a closed, single-block circuit. Supported operations
  are `alloc`, `h`, `x`, `z`, `sx`, `rz`, `cx`, `cz`, terminal `measure`, architecture
  `barrier`, `discard`, and `output`.
- `--mlirq-logical-opt` removes adjacent H/H, X/X, Z/Z, and CX/CX inverse
  pairs. It preserves opaque operation annotations by leaving annotated pairs
  unchanged.
- `--mlirq-map-identity` assigns physical indices in allocation order and
  validates the entire module before committing. It rejects circuits that
  need routing or exceed target capacity. It is a minimal placement utility,
  not a routing algorithm.
- `--mlirq-verify-target` verifies physical placement, uniqueness, connectivity,
  and CX direction. These checks also run automatically when parsing an
  architecture-stage circuit.
- `--mlirq-qrisk-scan` matches a local catalog against the exact backend and
  physical qubits. `--mlirq-qrisk-mitigate` breaks occurrences through verified
  commuting reorders, preserving ideal semantics and reporting unresolved hits.
- `tools/import_qrisk.py` imports QRisk DDMin reports or pattern memories. The
  supplied catalog contains three historical observations from the actual
  upstream repository, with source revisions, timestamps, and file hashes.
- `export_qiskit_circuit()` reconstructs a new Qiskit circuit from native output,
  preserving layout, phase, registers, measurement destinations, labels, and
  barriers. `--mlirq-export-qiskit` supplies verified structured interchange.
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

## M1: post-Qiskit pattern mitigation

- [x] Import backend-specific QRisk pattern data and observation provenance.
- [x] Match patterns by backend, physical qubits, gate order, and parameters.
- [x] Apply equivalent commuting rewrites and report reduced/unresolved occurrences.
- [x] Verify the native IR with toy and imported-pattern regression tests.
- [x] Accept a compiled Qiskit circuit and its backend/target without remapping it.
- [x] Preserve circuit width, layouts, phase, classical-bit mapping, and barriers during conversion.
- [x] Export the optimized native order as a new Qiskit circuit without recompiling.
- [x] Return an optimized Qiskit circuit and a structured report; validate native instructions before and after.
- [x] Run Qiskit round-trip/equivalence tests in CI.
- [x] Complete integration coverage and the full optimization/report example.

Checked items describe the supported, implemented Qiskit-to-Qiskit workflow. The ordered task list, supported input scope, and
completion criteria are in [docs/roadmap.md](docs/roadmap.md). M1 is complete when
a caller can pass in Qiskit's compiled circuit and receive the verified optimized
circuit plus report. Blocked patterns remain visible; zero occurrences are not
guaranteed for every circuit. Hardware fidelity evaluation follows this milestone.

The custom router remains removed. Logical optimization and identity mapping
are foundation utilities, outside the M1 post-compilation path. See
[docs/architecture.md](docs/architecture.md) for the internal IR contract.

Local build and test evidence is recorded in [docs/validation.md](docs/validation.md).

## Python circuit conversion and optimization (T1–T8)

After building the native compiler, install the Python package and run the
offline round-trip example:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python examples/import-qiskit.py --mlirq-opt build/bin/mlirq-opt
```

`import_compiled_circuit()` accepts an already-compiled Qiskit circuit, exact
backend name, target, and pattern catalog. It preserves physical indices and
the full circuit width in native IR, with isolated snapshots of the input
context. `NativeCompiler.run()` verifies, scans, or mitigates that IR and
returns a native result. `export_qiskit_circuit(result, compiler=compiler)`
exports that result as a new Qiskit circuit. See
[docs/qiskit-adapter.md](docs/qiskit-adapter.md) for the API, supported input
subset, errors, and tests.

## Run the experiments

```sh
bash scripts/setup-experiments.sh
scripts/run-rq1.sh --output results/rq1
scripts/run-rq2.sh --output results/rq2
scripts/run-rq3.sh --output results/rq3
scripts/run-rq4.sh --output results/rq4-simulator
```

The default is a small offline smoke study using IBM FakeFez and Qiskit Aer.
Each command saves raw circuits, JSONL/CSV results, calibration/catalog
snapshots, seeds and version hashes. Larger configurations, held-out QPY
inputs, baseline definitions and the user-operated hardware commands are in
[the experiment guide](docs/experiments.md). Simulator results do not establish
hardware improvement, and constructed fixtures are reported separately.

```python
from mlirq_qiskit import NativeCompiler, optimize_compiled_circuit

# compiled was already transpiled with this backend's Target.
result = optimize_compiled_circuit(
    compiled, backend_name="ibm_fez", target=backend.target,
    patterns="patterns/qrisk-imported.json",
    compiler=NativeCompiler("build/bin/mlirq-opt"),
)
optimized = result.circuit
print(result.report["before"], result.report["after"])
```
