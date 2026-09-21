# Qiskit importer and native bridge

M1 tasks **T1 and T2 are implemented** by the `mlirq-qiskit` Python package.
It imports a circuit already compiled by the caller and invokes the existing
native compiler. Its output is an `ImportedCircuit`/`NativeResult` containing
MLIR and preserved input context. Exporting the optimized result as a Qiskit
circuit is T4; the complete optimization API/report is T6.

## Install and try

Build `mlirq-opt` using the [README](../README.md), then install the Python
package. The dependency range is `qiskit>=2.4.2,<2.6`; CI tests 2.4.2 and 2.5.2
on Python 3.12. The package requires Python 3.10 or later. The native binary is
installed/built separately and is not bundled into the Python wheel.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python examples/import-qiskit.py --mlirq-opt build/bin/mlirq-opt
```

The [offline example](../examples/import-qiskit.py) compiles a two-qubit circuit
onto a synthetic six-qubit line, with initial layout `[4, 1]`, and imports the
result. It prints verified physical MLIR. All six compiled wires, including
Qiskit's auxiliary wires, retain their physical positions.

## API

```python
from mlirq_qiskit import NativeCompiler, import_compiled_circuit

# compiled_circuit, backend_name, and target are supplied by the caller.
module = import_compiled_circuit(
    compiled_circuit,
    backend_name=backend_name,
    target=target,
    patterns="patterns/qrisk-imported.json",
)
result = NativeCompiler("build/bin/mlirq-opt").run(module, mode="scan")
print(result.mlir)
```

`patterns` accepts a JSON file path or a mapping. The importer snapshots its
content, records its SHA-256, and checks the versioned JSON envelope. Detailed
pattern validation remains in the existing native QRisk loader and runs for
`scan`/`mitigate`. Neither importing nor native processing reads live backend
calibrations or submits jobs.

`NativeCompiler` resolves an explicit executable path, then (when omitted)
`MLIRQ_OPT`, or finally `mlirq-opt` on `PATH`. The default timeout is 30 seconds.
It invokes the executable without a shell and isolates each catalog in a
temporary working directory. Available modes are:

| Mode | Behavior |
| --- | --- |
| `verify` (default) | Parse and verify the imported IR and its projected topology |
| `scan` | Run the existing backend-specific QRisk scan and target verification |
| `mitigate` | Run the existing equivalent QRisk rewrites and target verification |

Results contain generic MLIR. QRisk reports remain native IR attributes for
now; the bridge does not parse them with regular expressions or expose a
completed Python report API. `result.module` carries the result and its input
context together and may be passed to another native call.

## Import and interchange contract

- Positions in `compiled_circuit.qubits` are physical qubit indices. The importer
  uses Qiskit's `find_bit()` positions and never reapplies `TranspileLayout`.
  It does not call the transpiler, identity mapper, router, or logical optimizer.
  A circuit without layout metadata is accepted if its physical instructions
  are legal; the caller is responsible for supplying already-compiled input.
- All input wires are allocated in the native module, including idle and
  auxiliary wires. Circuit width can be smaller than target capacity and is
  recorded separately. Allocation/discard operations are internal bookkeeping.
- Standard `h`, `x`, `z`, `sx`, `rz`, `cx`, `cz`, and terminal `measure` are
  supported. The importer checks instruction classes as well as names, operand
  roles, finite numeric parameters, target support, and qubit lifetime.
- Input instructions are checked against their exact ordered physical operands
  and parameter values in the supplied Qiskit `Target`. Native topology is a
  projection of the input's validated two-qubit edges. It is not a complete
  native-instruction target model; the original target is retained for T5.
- Quantum gate parameters use lossless binary64 MLIR attributes. Measurement
  results appear in instruction order, with explicit classical destination
  attributes and an immutable `(instruction_index, physical_qubit, clbit)`
  tuple list. Unwritten classical bits and register structure remain in the
  source snapshot for the later exporter.
- `ImportedCircuit` is the interchange unit: MLIR plus a deep-copied source
  circuit, target, and catalog snapshot. Accessing `source_circuit`, `target`,
  or `pattern_catalog` returns a fresh copy. Native passes retain this context
  and never mutate the caller's circuit, target, or catalog.
- Schema version 1 also records circuit width, classical width, and global phase
  as circuit attributes. Global phase is transported metadata; the initial
  native dialect does not interpret it as an executable phase operation.
  Keep the bundle together rather than treating the MLIR string alone as a
  complete Qiskit circuit serialization. Native tests compare the gate unitary
  with the retained phase included.

T3 still needs barrier conversion and full metadata preservation through
export. Until then, barriers, instruction labels, timing schedules/delays,
reset/reuse, control flow, classical variables/stretches, repeated classical-bit
writes, unbound/nonfinite parameters, and other operations fail explicitly.
They are never dropped or silently decomposed.

## Errors and tests

All adapter errors derive from `MLIRQError` and provide `as_dict()`. Input
errors include a stable code and, where applicable, instruction index/name.
`CatalogError` reports file/JSON-envelope problems. `NativeCompilerError`
distinguishes a missing executable, timeout, launch/I/O error, empty output,
and compiler rejection; compiler rejection retains stderr and the exit code.
Failed native calls return no partial result.

```sh
MLIRQ_OPT="$PWD/build/bin/mlirq-opt" \
  .venv/bin/python -m unittest discover -s test/python -v
```

The adapter suite covers real transpilation with an existing layout, sparse
physical indices, flat bit positions across registers, every supported gate,
binary64 edge cases, phase-sensitive numerical comparisons, partial measurement,
snapshot isolation, rejected inputs, and native scan/mitigate/error behavior.
The full Qiskit export/optimization workflow remains on the
[roadmap](roadmap.md).

Qiskit contracts: [Target instruction support](https://quantum.cloud.ibm.com/docs/en/api/qiskit/qiskit.transpiler.Target#instruction_supported)
and [TranspileLayout](https://quantum.cloud.ibm.com/docs/en/api/qiskit/qiskit.transpiler.TranspileLayout).
