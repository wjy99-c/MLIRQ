# Implementation milestones

These are implementation increments, not promises that the research questions
are already solved. Each increment should land with a demonstrable compiler
path and a correctness oracle.

| Milestone | Work | Acceptance criteria |
| --- | --- | --- |
| M0: native foundation | Quantum SSA dialect, verifier, inverse cancellation, identity placement, topology contract | Native build; parser round trips; invalid programs rejected; optimized complex amplitudes preserved |
| QRisk compiler increment (implemented) | Import historical QRisk tokens; backend/physical-wire matching; exact commuting reorders; before/after reports | Toy and real-pattern regressions; all-basis complex amplitudes preserved; unsupported recurrences remain visible |
| M1: routing | Initial placement selection, explicit SWAP operation, path-based routing, final permutation artifact | Non-neighbor CX on a line becomes legal; arbitrary inputs agree after decoding the final permutation; disconnected targets fail clearly |
| M2: native lowering | Distinct backend operation set and conversion legality; first documented native gate basis | No residual unsupported logical gates; every decomposition independently checked including global-phase convention |
| M3: frontend and output | A precisely documented OpenQASM subset or Qiskit adapter; one backend emitter | Import -> L0 -> L1 -> L2 -> executable format; supported subset round-trips against an independent simulator |
| M4: additional architecture | A genuinely different architecture model and lowering path | Same logical suite compiles to both targets; target-specific constraints checked; no vendor assumptions hidden in L0 |
| M5: HALO integration | Helper-qubit ownership, reset, lifetime, and process-isolation model | Illegal sharing rejected; legal reuse explicitly scheduled; state reset assumptions validated |
| M6: QRisk research integration | Extend the implemented compiler path with calibration/window policy, schedule-aware identity, and evaluation | Freshness rules; current target constraints; measured improvement assessed separately from ideal equivalence |

## First follow-up: routing

1. Specify SWAP semantics at both the wire and placement levels. Decide
   whether logical identities are tracked explicitly or via a permutation.
2. Extend the oracle to compare the complete unitary under initial and final
   placement permutations. Include nontrivial input states and reversed CX.
3. Implement deterministic shortest-path routing for a static topology.
4. Keep the topology legality verifier independent of the router.
5. Add positive tests on line/ring graphs and negative tests on disconnected
   components and asymmetric directed connectivity.

## Research evaluation preparation

Track gate counts, inserted routing operations, depth, compile time, target
legality, and ideal-equivalence failures separately. Timing and fidelity need
their own explicitly defined models and later hardware evidence. Preserve
the input, target snapshot, pass pipeline, compiler revision, and intermediate
IR for each benchmark so failures can eventually be reduced using the
multi-layer testing workflow.
