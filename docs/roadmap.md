# Implementation milestones

These are implementation increments, not promises that the research questions
are already solved. Each increment should land with a demonstrable compiler
path and a correctness oracle.

| Milestone | Work | Acceptance criteria |
| --- | --- | --- |
| M0: native foundation | Quantum SSA dialect, verifier, inverse cancellation, identity placement, topology contract | Native build; parser round trips; invalid programs rejected; optimized complex amplitudes preserved |
| QRisk compiler increment (implemented) | Import historical QRisk tokens; backend/physical-wire matching; exact commuting reorders; before/after reports | Toy and real-pattern regressions; all-basis complex amplitudes preserved; unsupported recurrences remain visible |
| M1: routing (implemented) | Identity or explicit initial placement; SWAP operation; deterministic BFS routing; auxiliary wires; verified final permutation | Non-neighbor CX/CZ/SWAP become legal; all-basis amplitudes agree after placement decoding; disconnected/directionally unsupported targets fail clearly |
| M2: native lowering | Distinct backend operation set and conversion legality; first documented native gate basis | No residual unsupported logical gates; every decomposition independently checked including global-phase convention |
| M3: frontend and output | A precisely documented OpenQASM subset or Qiskit adapter; one backend emitter | Import -> L0 -> L1 -> L2 -> executable format; supported subset round-trips against an independent simulator |
| M4: additional architecture | A genuinely different architecture model and lowering path | Same logical suite compiles to both targets; target-specific constraints checked; no vendor assumptions hidden in L0 |
| M5: HALO integration | Helper-qubit ownership, reset, lifetime, and process-isolation model | Illegal sharing rejected; legal reuse explicitly scheduled; state reset assumptions validated |
| M6: QRisk research integration | Extend the implemented compiler path with calibration/window policy, schedule-aware identity, and evaluation | Freshness rules; current target constraints; measured improvement assessed separately from ideal equivalence |

## Completed: first follow-up, routing

The five routing tasks are implemented: explicit physical-wire SWAP semantics;
an oracle that decodes initial/final permutations; deterministic shortest-path
routing; independent topology/permutation verification; and line/ring,
disconnected, and asymmetric-direction regression cases. See
[routing.md](routing.md) for the contract and scope.

## First outstanding task: native lowering

1. Define a backend operation set, native gate basis, and conversion legality.
2. Lower logical gates and SWAPs while retaining routing/measurement identity.
3. Specify direction correction and global-phase conventions explicitly.
4. Check each decomposition on all basis inputs and exercise the complete
   routing -> lowering pipeline. Reject unsupported residual gates.

## Research evaluation preparation

Track gate counts, inserted routing operations, depth, compile time, target
legality, and ideal-equivalence failures separately. Timing and fidelity need
their own explicitly defined models and later hardware evidence. Preserve
the input, target snapshot, pass pipeline, compiler revision, and intermediate
IR for each benchmark so failures can eventually be reduced using the
multi-layer testing workflow.
