# Qiskit circuit conversion and native bridge

M1 tasks **T1–T4 are implemented** by the `mlirq-qiskit` Python package.
It imports a circuit already compiled by the caller and invokes the existing
native compiler. `ImportedCircuit`/`NativeResult` contain MLIR and preserved
input context; `export_qiskit_circuit()` reconstructs a new Qiskit circuit from
either bundle. Output instruction validation is T5; the complete optimization
API/report is T6. These calls provide the conversion round trip now.

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
result, verifies it, and exports it back to Qiskit. It checks the complete
operator including global phase, existing layout, metadata, and a labeled
barrier. All six compiled wires, including Qiskit's auxiliary wires, retain
their physical positions. This is a conversion example; guaranteed pattern
reduction is exercised separately in the tests.

## API

```python
from mlirq_qiskit import NativeCompiler, export_qiskit_circuit, import_compiled_circuit

# compiled_circuit, backend_name, and target are supplied by the caller.
module = import_compiled_circuit(
    compiled_circuit,
    backend_name=backend_name,
    target=target,
    patterns="patterns/qrisk-imported.json",
)
compiler = NativeCompiler("build/bin/mlirq-opt")
result = compiler.run(module, mode="mitigate")
optimized_circuit = export_qiskit_circuit(result, compiler=compiler)
print(result.mlir)  # Native before/after report; Python report API follows in T6.
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

`export_qiskit_circuit(module_or_result, *, compiler=None)` accepts an
`ImportedCircuit` or `NativeResult`. Omitting `compiler` uses the same executable
discovery defaults. It runs native verification and export only; it does not
invoke mitigation or Qiskit compilation. Repeated exports return independent
circuits. A blocked/no-match native result exports with its unchanged order.

## Import and interchange contract

- Positions in `compiled_circuit.qubits` are physical qubit indices. The importer
  uses Qiskit's `find_bit()` positions and never reapplies `TranspileLayout`.
  It does not call the transpiler, identity mapper, router, or logical optimizer.
  A circuit without layout metadata is accepted if its physical instructions
  are legal; the caller is responsible for supplying already-compiled input.
- All input wires are allocated in the native module, including idle and
  auxiliary wires. Circuit width can be smaller than target capacity and is
  recorded separately. Leading allocations declare input wires and trailing
  discards close unmeasured wires inside the IR. Export validates and omits
  these boundaries, leaving unmeasured output wires available. It emits no
  initialization, reset, or discard instruction.
- Standard `h`, `x`, `z`, `sx`, `rz`, `cx`, `cz`, and terminal `measure` are
  supported, together with standard `barrier` directives and instruction labels.
  The importer checks instruction classes as well as names, operand
  roles, finite numeric parameters, target support, and qubit lifetime.
- Input instructions are checked against their exact ordered physical operands
  and parameter values in the supplied Qiskit `Target`. Native topology is a
  projection of the input's validated two-qubit edges. It is not a complete
  native-instruction target model; the original target is retained for T5.
- Quantum gate parameters use lossless binary64 MLIR attributes. Measurement
  results appear in instruction order, with explicit classical destination
  attributes and an immutable `(instruction_index, physical_qubit, clbit)`
  tuple list. Export preserves unwritten bits, multiple registers, loose bits,
  aliases, ancillas, and register membership/order from the source snapshot.
  Repeated writes to a classical bit are supported in their original order.
  A measured quantum wire still cannot be measured again or used by a gate.
- `mlirq.barrier` carries the ordered physical scope without consuming SSA
  quantum state, so it also represents barriers after measurement and empty
  barriers. It requires previously allocated physical indices. The current
  mitigation pass conservatively refuses any gate motion across a barrier,
  including a disjoint or empty barrier. In matching, barriers interrupt the
  projected trace when they touch a pattern's scope. Barriers are directives
  and do not require a hardware gate entry in the target.
- `ImportedCircuit` is the interchange unit: MLIR plus a deep-copied source
  circuit, target, and catalog snapshot. Accessing `source_circuit`, `target`,
  or `pattern_catalog` returns a fresh copy. Native passes retain this context
  and never mutate the caller's circuit, target, or catalog.
- Bundle/import schema version 2 records circuit width, classical width, and global phase
  as circuit attributes. Global phase is transported metadata; the initial
  native dialect does not interpret it as an executable phase operation.
  Keep the bundle together rather than treating the MLIR string alone as a
  complete Qiskit circuit serialization. Version 2 adds original instruction
  IDs (`mlirq.qiskit.source_index`) to gates, measurements, and barriers.
  Rebuild the native compiler alongside this adapter; version 1 bundles do not
  contain the identity information required for export. The separate QRisk
  catalog schema remains version 1.

Timing schedules/delays, reset/reuse, control flow, classical variables/stretches,
unbound/nonfinite parameters, and other operations fail explicitly.
They are never dropped or silently decomposed.

## Export contract

`--mlirq-export-qiskit=output-file=circuit.json` walks verified native operations
and resolves SSA wires to physical indices. The bridge reads this structured
JSON in its private working directory; it does not parse printed MLIR with
regular expressions. Exact binary64 hexadecimal strings carry global phase
and parameters, including signed zero and subnormals.

Export requires one imported circuit, all input allocations at the start,
discards only at the end, valid measurement destinations, and complete ordered
measurement outputs. Unknown semantic attributes are rejected. The native pass
does not modify the IR.

The Python exporter checks metadata and a complete permutation of source IDs
against the snapshot, including gate names, ordered physical operands,
measurement destinations, and exact parameter bits. It rejects dropped/added
instructions and barrier/measurement fence crossings. Current transformations
only reorder existing gates; future gate synthesis/removal requires an explicit
extension of this contract. These checks do not prove arbitrary reordered IR
equivalent: equivalence comes from the native pass's whitelisted identities.

The output starts with an empty copy of the isolated source circuit. Original
instruction objects are appended in verified native order, preserving labels
and parameters. Layout, global phase, circuit name, arbitrary metadata, bit
ordering, and registers survive without remapping. The native global-phase
record must agree with the source. Export does not yet run the separate T5
Qiskit target-instruction checker or return the T6 structured report.

## Errors and tests

All adapter errors derive from `MLIRQError` and provide `as_dict()`. Input
errors include a stable code and, where applicable, instruction index/name.
`CatalogError` reports file/JSON-envelope problems. `NativeCompilerError`
distinguishes a missing executable, timeout, launch/I/O error, empty output,
and compiler rejection; compiler rejection retains stderr and the exit code.
Failed native calls return no partial result.
`ExportError` reports context/instruction mismatches, invalid interchange data,
or fence crossings. A missing or malformed native JSON file is reported as
`NativeCompilerError` with code `native_invalid_export`.

```sh
MLIRQ_OPT="$PWD/build/bin/mlirq-opt" \
  .venv/bin/python -m unittest discover -s test/python -v
```

The adapter suite covers real transpilation with an existing layout, sparse
physical indices, flat bit positions across registers, every supported gate,
binary64 edge cases, phase-sensitive numerical comparisons, partial measurement,
snapshot isolation, rejected inputs, and native scan/mitigate/error behavior.
The export suite adds phase-sensitive operator comparisons, exact measured
distributions, labels after reordering, barrier fences (including after
measurement), classical overwrites, metadata/bit/layout round trips, no-op
exports, malformed native output, and exported Fez mitigation followed by
reimport and rescanning. The complete optimization/report workflow remains on
the [roadmap](roadmap.md).

Qiskit contracts: [Target instruction support](https://quantum.cloud.ibm.com/docs/en/api/qiskit/qiskit.transpiler.Target#instruction_supported)
and [TranspileLayout](https://quantum.cloud.ibm.com/docs/en/api/qiskit/qiskit.transpiler.TranspileLayout).
Circuit reconstruction uses
[QuantumCircuit.copy_empty_like](https://quantum.cloud.ibm.com/docs/en/api/qiskit/qiskit.circuit.QuantumCircuit#copy_empty_like).
