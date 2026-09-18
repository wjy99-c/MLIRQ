# Implementation milestones

These are implementation increments, not promises that the research questions
are already solved. Each increment should land with a demonstrable compiler
path and a correctness oracle.

The custom M1 routing implementation has been removed. The former M1 routing
and M2 native-lowering work is replaced by integration with Qiskit's existing
transpiler. The L1/L2 IR contracts and independent validation remain part of
MLIRQ; the adapter itself is still to be implemented.

| Milestone | Work | Acceptance criteria |
| --- | --- | --- |
| M0: native foundation | Quantum SSA dialect, verifier, inverse cancellation, identity placement, topology contract | Native build; parser round trips; invalid programs rejected; optimized complex amplitudes preserved |
| QRisk compiler increment (implemented) | Import historical QRisk tokens; backend/physical-wire matching; exact commuting reorders; before/after reports | Toy and real-pattern regressions; all-basis complex amplitudes preserved; unsupported recurrences remain visible |
| Qiskit backend integration (next; replaces M1/M2) | Documented circuit adapter; delegate placement, routing, and native-gate translation to Qiskit; preserve target/layout/provenance records | Supported subset round-trips; no unsupported native instructions; equivalence after decoding layouts, auxiliary wires, measurement order, and global phase; QRisk runs on the compiled physical gate sequence |
| M3: frontend and output | A precisely documented OpenQASM subset; one backend emitter using the adapter | Import -> L0 -> Qiskit compilation -> physical IR -> QRisk -> executable format; exported artifacts preserve verified semantics and target legality |
| M4: additional architecture | A genuinely different architecture model and lowering path | Same logical suite compiles to both targets; target-specific constraints checked; no vendor assumptions hidden in L0 |
| M5: HALO integration | Helper-qubit ownership, reset, lifetime, and process-isolation model | Illegal sharing rejected; legal reuse explicitly scheduled; state reset assumptions validated |
| M6: QRisk research integration | Extend the implemented compiler path with calibration/window policy, schedule-aware identity, and evaluation | Freshness rules; current target constraints; measured improvement assessed separately from ideal equivalence |

## First follow-up: Qiskit adapter

1. Define a supported circuit subset and explicit conversion rules between
   MLIRQ and Qiskit, including parameters, terminal measurements, and outputs.
2. Use Qiskit's target and transpiler for layout, routing, and native-gate
   translation. Record the compiler version, settings, and target snapshot.
3. Preserve physical indices, initial/final layouts, introduced auxiliary
   wires, classical output order, and global phase on import back into MLIRQ.
4. Verify topology and native instruction legality independently, and compare
   circuit semantics with an oracle that accounts for layout changes.
5. Run backend-specific QRisk mitigation after translation and optimization;
   rescan the final gate sequence if any later stage rewrites it. Cover
   non-neighbor gates, asymmetric connectivity, and unsupported operations.

## Research evaluation preparation

Track gate counts, inserted routing operations, depth, compile time, target
legality, and ideal-equivalence failures separately. Timing and fidelity need
their own explicitly defined models and later hardware evidence. Preserve
the input, target snapshot, pass pipeline, compiler revision, and intermediate
IR for each benchmark so failures can eventually be reduced using the
multi-layer testing workflow.
