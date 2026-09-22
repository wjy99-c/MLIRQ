# Implementation roadmap

## M1: post-Qiskit hardware-pattern mitigation

**Goal:** accept a circuit already compiled by Qiskit, reduce occurrences of
backend-specific QRisk patterns through equivalent transformations, and return
the optimized circuit in Qiskit form with an auditable report.

**Status: partially implemented.** The native pattern engine and T1–T4 Python
bridge/import/export are implemented. Output instruction validation and the
complete optimization/report API remain TODO. Status was updated on 2026-09-22.
Checked boxes below refer to code and tests already present. Unchecked boxes
are implementation TODOs.
This milestone supersedes the earlier routing/adapter roadmap.

### Input and output contract

| Item | Contract for M1 |
| --- | --- |
| Input circuit | Qiskit `QuantumCircuit` after backend layout, routing, native-gate translation, and optimization; static, with bound parameters, before timing scheduling |
| Backend context | Exact backend identifier plus the Qiskit `Target` used for compilation; preserve available target snapshot/provenance rather than inferring identity from gate names |
| Pattern data | Versioned QRisk catalog with backend identity, physical qubits, gate order/parameters, and observation provenance |
| Output circuit | A new Qiskit `QuantumCircuit` for the same target, preserving physical placement, circuit width, ideal semantics, global phase, layout metadata, classical registers, measurement destinations, and barriers |
| Output report | Backend and catalog provenance; total/per-pattern before/after counts; accepted rewrites; remaining matches and status; validation results and compiler versions |

The caller owns Qiskit compilation. MLIRQ imports the resulting physical
circuit directly into the existing architecture-stage IR, runs QRisk scanning
and mitigation, validates, and exports. The API must not invoke another
transpilation, layout, routing, identity-mapping, or native-lowering stage.
The proposed return contract is `optimized_circuit, report`; this is not an
available Python API yet.

The first adapter will support the current gate vocabulary (`h`, `x`, `z`,
`sx`, `rz`, `cx`, `cz`) only where each instruction is legal on the supplied
target, plus terminal measurements and preserved barriers. Circuits may
leave wires unmeasured. Idle and Qiskit-added auxiliary wires retain their
physical identities. Unbound parameters, reset/reuse, conditional/control-flow
operations, timing-scheduled circuits/delays, custom pulse semantics, and other
unsupported instructions must fail clearly. Additional native gates can be
added with explicit semantics and tests as needed.

The adapter accepts the standard gates, terminal measurements, and barriers
above, including instruction labels and repeated classical-bit writes in their
original order. It retains all input wires and snapshots the Qiskit context.
Import, native processing, and export are available as separate calls; see
[qiskit-adapter.md](qiskit-adapter.md).

An accepted rewrite must strictly reduce the total matched count without
increasing any active pattern count. If no supported equivalent rewrite helps,
return the unchanged circuit and report the remaining occurrences. Partial
reduction is valid; universal elimination is not an acceptance requirement.

### Implemented tasks

- [x] **Quantum IR and ownership verification.** Represent the supported gates
  with linear SSA wires and verify closed circuits. Evidence:
  [operation definitions](../include/mlirq/IR/MLIRQOps.td) and
  [verifiers](../lib/IR/MLIRQOps.cpp).
- [x] **QRisk pattern catalog and data importer.** Import reports/memories,
  bind them to exact backends, validate parameters, and retain observation
  provenance. Evidence: [importer](../tools/import_qrisk.py) and
  [historical catalog](../patterns/qrisk-imported.json). This imports pattern
  data, not Qiskit circuits.
- [x] **Backend-specific pattern detection.** Match the exact backend,
  physical operands, gate order, and parameters; count overlapping matches
  while ignoring disjoint spectators. Evidence:
  [QRisk passes](../lib/Transforms/QRiskPasses.cpp).
- [x] **Equivalent pattern disruption.** Apply whitelisted commuting reorders,
  accept only count-reducing candidates, and verify the transformed module
  before committing it. Unsupported recurrences remain reported. Evidence:
  [QRisk passes](../lib/Transforms/QRiskPasses.cpp).
- [x] **Native before/after reports.** Record total/per-pattern counts,
  accepted rewrites, remaining occurrences, and statuses such as `eliminated`,
  `partial`, `blocked`, `no_matches`, and `no_backend_patterns` in IR attributes.
- [x] **Native target topology checks.** Validate capacity, physical placement,
  CX direction, and CZ connectivity. Evidence: [target verifier](../lib/IR/Target.cpp).
  This does not yet validate every instruction against a Qiskit `Target`.
- [x] **Core correctness and pattern regressions.** Existing suites contain
  47 tests, including toy patterns, all three imported backend observations,
  phase-sensitive equivalence, and a constructed Fez example with 2 to 0 hits.
  Evidence: [QRisk tests](../test/test_qrisk.py),
  [import tests](../test/test_qrisk_import.py), and
  [validation record](validation.md). These are native-core tests, not Qiskit
  input/output or hardware-fidelity tests.

### Adapter tasks, in implementation order

- [x] **T1 — Python interface and native bridge.** The installable
  `mlirq-qiskit` package accepts the compiled circuit, backend identity/target,
  and catalog. A versioned MLIR/context bundle preserves the input; the native
  bridge provides verify/scan/mitigate modes, isolated catalog snapshots,
  timeouts, and structured errors. Qiskit 2.4.2 and 2.5.2 are covered in CI.
  Evidence: [package](../python/mlirq_qiskit), [packaging](../pyproject.toml),
  and [adapter tests](../test/python/test_adapter.py).
- [x] **T2 — Compiled-circuit import.** Supported standard instructions become
  already-physical MLIRQ IR with exact binary64 parameters and operand roles.
  All input wires, including idle/auxiliary wires, retain their original flat
  physical positions; existing Qiskit layouts are never reapplied.
  Evidence: [importer](../python/mlirq_qiskit/importer.py) and
  [adapter tests](../test/python/test_adapter.py).
- [x] **T3 — Metadata and barrier preservation.** Preserve layout, global
  phase, register membership, bit order, measurement destinations, and labels.
  Native barriers retain ordered physical scopes, including measured wires,
  and conservatively fence all commuting reorders. Allocation/discard mark
  conversion boundaries; export emits no initialization, reset, or discard.
  Unsupported operations and timing semantics fail explicitly.
  Evidence: [barrier definition](../include/mlirq/IR/MLIRQOps.td),
  [importer](../python/mlirq_qiskit/importer.py), and
  [round-trip tests](../test/python/test_export.py).
- [x] **T4 — Qiskit circuit export.** `export_qiskit_circuit()` reconstructs
  a new circuit in native order without transpiling. A native JSON pass
  resolves verified SSA operands to physical indices; the exporter checks
  instruction identities, exact parameter bits, metadata, and fences against
  the source snapshot. Unitary, partially measured, and fully measured
  circuits round-trip, including repeated classical-bit writes.
  Evidence: [native export](../lib/Transforms/QiskitExport.cpp),
  [Qiskit export](../python/mlirq_qiskit/exporter.py), and
  [round-trip tests](../test/python/test_export.py).
- [ ] **T5 — Input/output instruction validation.** Check each instruction's
  operation, ordered physical operands, and parameter values against the
  supplied target before import and after export. Keep this distinct from
  MLIRQ's existing topology-only checks; handle barriers as directives.
- [ ] **T6 — Complete optimization API and report.** Connect import, initial
  scan, mitigation, verification, and export. Return the circuit plus a
  machine-readable report with catalog/target provenance and compiler
  versions. Rescan the exported circuit so reported counts describe what the
  caller actually receives. Preserve no-match/blocked/partial outcomes.
- [ ] **T7 — Qiskit integration tests.** Verify import/export without rewrites
  first, then mitigation. Test sparse physical indices, nontrivial existing
  layouts, idle/auxiliary wires, global phase, multiple classical registers,
  partial measurements, barriers, backend mismatch, unsupported instructions,
  illegal target instructions, and unchanged inputs. Compare complex amplitudes
  on small unitary cases and ideal measured distributions with exact bit
  mappings. Check final pattern counts independently in test fixtures.
  T1–T4 tests now cover round trips, phase-sensitive operators, exact measured
  distributions, barriers, layouts, metadata isolation, and exported Fez
  mitigation/rescanning. Complete optimization/report API coverage awaits T6.
- [ ] **T8 — Reproducible example and CI.** Add a Python example in which the
  caller compiles with Qiskit, passes the result into MLIRQ, and receives an
  equivalent target-legal circuit with fewer occurrences. Use deterministic
  offline targets and toy catalogs for the guaranteed-reduction integration
  test; retain the historical QRisk examples separately. Document installation,
  supported inputs, API usage, and error behavior; run adapter tests in CI.
  A conversion round-trip example and adapter CI now exist. This task still
  requires the complete optimization/report example with a deterministic
  post-compilation reduction fixture.

T1–T4 establish the conversion round trip. T5–T6 make it a validated optimization
API. T7–T8 provide the evidence and usable example needed to complete M1.

### M1 completion criteria

1. A user can supply an already-compiled Qiskit circuit and receive a Qiskit
   circuit plus report through one documented API call.
2. The supported circuit subset round-trips without changing physical identity,
   global phase, measurement results/bit interpretation, or barrier constraints.
3. A deterministic post-Qiskit test demonstrates strictly fewer occurrences;
   all accepted rewrites preserve ideal semantics, and no active catalog
   pattern count increases. Blocked and no-match inputs are handled correctly.
4. Input and output pass target instruction validation. No hidden recompilation
   or qubit remapping occurs, and final reported counts match the exported circuit.
5. Native and Qiskit integration tests pass in CI, and the documented example
   runs offline. Lower pattern counts establish this compiler milestone;
   measured hardware-fidelity improvements require separate experiments.

### Later milestones

| Milestone | Work after M1 |
| --- | --- |
| M2: evaluation and pattern policy | Compare post-Qiskit circuits before/after MLIRQ; measure occurrence counts, compile time, gate counts/depth, and hardware results separately; define calibration freshness and pattern promotion rules |
| M3: broader circuit/backend support | Add needed native operations and backend interfaces with explicit semantics; address scheduling-aware patterns and final-sequence validation |
| M4: resource-sharing integration | Add HALO ownership, lifetime, reset, and isolation contracts before introducing physical-qubit reuse |

Custom routing and native-gate lowering are outside this roadmap's M1. The
existing logical optimizer and identity mapper remain foundation/test utilities.

### Qiskit references

The input boundary follows Qiskit's documented
[transpiler stages](https://quantum.cloud.ibm.com/docs/en/guides/transpiler-stages).
Instruction validation should use the supplied
[Target](https://quantum.cloud.ibm.com/docs/en/api/qiskit/qiskit.transpiler.Target#instruction_supported).
Conversion must retain existing
[TranspileLayout](https://quantum.cloud.ibm.com/docs/en/api/qiskit/qiskit.transpiler.TranspileLayout)
information without performing those permutations again.
