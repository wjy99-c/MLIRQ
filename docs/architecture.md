# Design contract

## Connection to the proposal

The recovered MLIRQ design has four layers: L0 logical (IR-L), L1
architecture-aware (IR-A), L2 backend lowering (IR-B), and L3 executable
(IR-X). HALO supplies resource-sharing information. The July diagram called
the learned backend-pattern component QRisk; the later proposal summary calls
that component QLearn. This repository does not assume those research
components have already been implemented in full. The QRisk integration
described below implements the initial pattern-guided compilation path.

| Layer | Long-term responsibility | Current implementation |
| --- | --- | --- |
| L0 / IR-L | Logical semantics and architecture-independent optimization | Typed, closed, straight-line circuit; ownership verifier; adjacent inverse cancellation |
| L1 / IR-A | Placement, routing, resource constraints, coarse scheduling | Identity placement, topology verification, backend-specific QRisk matching and commuting rewrites |
| L2 / IR-B | Native-gate decomposition, direction correction, fine scheduling | Not implemented |
| L3 / IR-X | Backend output, timing/pulse validation, submission artifacts | Not implemented |

The first two stages share a small dialect, with a checked `mlirq.stage`
attribute distinguishing contracts. This is an incremental starting point;
four nominal dialects with identical semantics would add complexity without
providing the promised layer boundaries. Split representations as their
operation sets and invariants actually diverge.

## Backend compilation boundary

Qiskit's transpiler will supply placement, routing, and native-gate translation
through a planned adapter. The custom M1 router and its SWAP operation have
been removed. The L1/L2 contracts remain useful for describing and verifying
compiled circuits; they do not require MLIRQ to implement those algorithms.
The adapter is not yet implemented.

The adapter must preserve physical qubit indices, initial and final layouts,
ordered classical outputs, and global phase, and explicitly reject unsupported
operations. Backend instruction legality must be checked separately from the
current topology verifier. Round-trip and equivalence tests must account for
layout changes and any introduced auxiliary qubits.

QRisk mitigation should follow Qiskit's gate translation and optimization,
once the physical gate sequence matches the backend catalog. Any subsequent
gate rewrite requires another scan. MLIRQ owns the common IR, QRisk matching
and equivalent transformations, and verification across the conversion boundary.

## Quantum-state ownership

`!mlirq.qubit` names a wire in the joint quantum state. It does not imply
that the qubit has an independent statevector; entanglement is allowed.

Each quantum operation consumes its input SSA values and returns successor
wire values. Each quantum SSA result must have exactly one use. MLIR checks
definition-before-use and block dominance; the circuit verifier checks
linear use and circuit confinement. Every quantum wire ends in measurement
or explicit discard.

| Operation | Contract |
| --- | --- |
| `alloc` | Allocate a fresh wire in state zero; no qubit reuse in M0 |
| `h`, `x`, `z` | Consume one wire, produce its successor |
| `sx` | Positive square root of X: ((1+i) I + (1-i) X) / 2, including its global phase |
| `rz` | Apply diag(exp(-i angle/2), exp(i angle/2)); angle is a finite f64 in radians |
| `cx` | Consume distinct control and target wires; results retain that wire order |
| `cz` | Apply diag(1,1,1,-1) to distinct wires; results retain operand order |
| `measure` | Z-basis measurement returning i1 and consuming the wire |
| `discard` | Trace out a wire; this is not an assertion that the state is zero |
| `output` | Terminate the circuit, returning ordered classical bits |

Measurement is terminal for its wire. Mid-circuit measurement with quantum
continuation, feed-forward, reset/reuse, calls, block arguments, and control
flow need explicit future semantics and are rejected by the current operation
set or structural verifier.

Quantum operations deliberately lack `Pure` and speculative-execution
traits. Unknown effects conservatively prevent generic DCE/CSE from treating
allocations or state transitions as ordinary reusable classical expressions.
The test suite exercises generic CSE/canonicalization on two allocations.

## Target contract

An optional circuit attribute describes a static topology:

```mlir
mlirq.target = {
  name = "synthetic-line-3",
  num_qubits = 3 : i64,
  coupling = array<i64: 0, 1, 1, 2>,
  directed = false
}
```

The flattened coupling array contains pairs. With `directed = true`, a
pair permits CX only in the listed control-to-target direction. With
`directed = false`, both directions are permitted. There are no implicit
all-to-all edges. Self-edges, invalid indices, incomplete pairs, unknown
fields, and malformed attribute types are errors.

CZ requires a listed coupling edge in either orientation because it is
symmetric. These are topology checks; accepting `sx` or `cz` does not assert
that every named backend natively supports the dialect's full gate set.

Identity placement assigns indices 0, 1, ... in allocation order separately
for each circuit. It checks capacity and every CX/CZ before applying any
placement to the module. Successfully mapped circuits receive
`mlirq.stage = "architecture"`, and each allocation receives `physical`.
Architecture-stage IR always revalidates target legality on parsing.
This utility cannot repair non-neighbor interactions; it remains useful for
small examples and tests while the Qiskit adapter is developed.

Physical qubit reuse is deliberately unsupported, even after discard or
measurement. This avoids silently assuming reset, isolation, or an ancilla
lifetime policy that HALO has not yet supplied.

## Pass contracts

| Pass | Preconditions | Result / preserved invariants |
| --- | --- | --- |
| `mlirq-logical-opt` | Verified L0 circuits, stage absent or `logical` | Identical ideal behavior; valid linear SSA; removes adjacent unannotated inverse pairs |
| `mlirq-map-identity` | Verified L0 circuits and complete topology | L1 placements satisfying capacity/connectivity/direction; wire order unchanged |
| `mlirq-verify-target` | L1 circuit | Read-only legality check |
| `mlirq-qrisk-scan` | L1 circuit and local pattern catalog | Report exact-backend, physical-wire occurrences; no gate changes |
| `mlirq-qrisk-mitigate` | L1 circuit and local pattern catalog | Exact commuting reorders; lower total occurrences without increasing any active pattern; unresolved hits reported |

The inverse-pair rewrite checks result/operand order and unique use for both
CX wires. It does not commute operations, fuse rotations, cross intervening
operations, or remove opaque annotations. Source locations remain attached
to the surviving operations. Future metadata-sensitive rewrites must define
how calibration, provenance, and learned-pattern annotations are preserved.

The QRisk pass runs on physically mapped IR, tracks physical wire identity
through SSA, and rebuilds the quantum operands after a reorder. Allocations,
measurements, discards, and opaque gate annotations are barriers. It verifies
the transformed module before committing any changes. Its matcher is a
scoped, ordered gate trace rather than a noise model or scheduler. See
[qrisk.md](qrisk.md) for the schema, upstream differences, and exact identities.

## Extensibility decisions still needed

- A generalized target interface needs explicit gate sets, duration units,
  calibration snapshots, constraints, and resource ownership. The current
  `TargetModel` is a topology contract, not that complete interface.
- HALO requires allocation/lifetime/reset and isolation contracts before
  physical reuse becomes legal.
- QRisk now has a versioned import schema with backend, physical qubits,
  parameters, and observation provenance. Schedule-aware identity, calibration
  freshness/invalidation, and hardware improvement evaluation remain open.
- Scheduling and pulse output must distinguish model estimates from measured
  hardware behavior. M0 reports no fidelity or hardware-performance claims.

## Upstream references

- [MLIR dialect organization and CMake](https://mlir.llvm.org/docs/Tutorials/CreatingADialect/)
- [MLIR operation definition specification](https://mlir.llvm.org/docs/DefiningDialects/Operations/)
- [MLIR pass infrastructure](https://mlir.llvm.org/docs/PassManagement/)
- [LLVM standalone example](https://github.com/llvm/llvm-project/tree/llvmorg-18.1.3/mlir/examples/standalone)
- [Qiskit transpiler stages](https://quantum.cloud.ibm.com/docs/en/guides/transpiler-stages)
