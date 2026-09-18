# Validation

Validated locally on 2026-09-18 using Ubuntu 24.04 (x86-64), GNU C++ 13.3.0,
and LLVM/MLIR 18.1.3. TableGen generation, all C++ compilation units, and the
`mlirq-opt` link completed successfully in Release mode.

The CTest suite invokes **65 regression tests** across four suites: 19 core
compiler tests, 20 QRisk compiler tests, 8 importer tests, and 18 routing tests.
All passed.
The core randomized oracle evaluates 8 circuits on all 8 input basis states;
the QRisk randomized oracle adds 12 circuits on all 8 basis states. The
QRisk suite also checks every supported commutation family, each included
upstream pattern, and the Fez example with terminal measurements on all basis inputs.
The routing oracle adds 12 four-qubit circuits on all 16 basis inputs,
decoding initial/final permutations and checking zero-valued auxiliary states.
Comparisons
use complex amplitudes at tolerance 1e-10, preserving global and relative phase.

```text
Test project .../mlirq/build
    Start 1: mlirq-regression
1/4 Test #1: mlirq-regression ................. Passed
    Start 2: mlirq-qrisk
2/4 Test #2: mlirq-qrisk ...................... Passed
    Start 3: mlirq-qrisk-import
3/4 Test #3: mlirq-qrisk-import ............... Passed
    Start 4: mlirq-routing
4/4 Test #4: mlirq-routing ................... Passed

100% tests passed, 0 tests failed out of 4
```

The core, QRisk, and routing CTest entries exercise the compiled native binary. Python
supplies test drivers, the independent numerical oracle, and the offline
QRisk data importer; the compilation passes are C++ MLIR passes.

Coverage includes:

- Generic/custom IR round trips and Bell-state amplitudes.
- Removal of redundant H/H, X/X, and CX/CX pairs, with Z/Z also exercised by
  generated circuits; preservation of non-inverse sequences and annotations.
- Allocation safety under generic MLIR CSE/canonicalization.
- Rejection of repeated quantum uses, abandoned wires, use after measurement,
  and aliased CX operands.
- Valid identity mapping; rejection of insufficient capacity, disconnected
  or directionally illegal CX, malformed coupling data, and unsupported
  target policy fields.
- Pass-stage preconditions and verification of already-mapped input IR.
- Exact QRisk backend isolation, physical placement and operand roles,
  parameter tolerance, overlapping matches, and spectator-wire projection.
- Strict occurrence reduction without increasing another active pattern;
  blocked and partial results; idempotent gate order after mitigation.
- No false mitigation from disjoint-only reordering; annotation and
  measurement barriers; preserved terminal measurement order.
- SX phase convention, CZ symmetry/connectivity, and actual upstream SX/Rz/CZ
  patterns on Fez, Kingston, and Marrakesh physical indices.
- QRisk report and memory import, deterministic provenance, backend evidence
  separation, duplicate-run filtering, cleared observations, and malformed input.
- Deterministic line/ring routing, reverse CX, explicit starting layouts,
  shortest legal SWAP paths, and ordered terminal measurements.
- Distinct source/routing SWAP semantics, independent final-permutation
  validation, unused auxiliary sites, and their complete zero-state contract.
- Rejection of disconnected/asymmetric routes, forged layout records,
  physical reuse after termination, and use of future reserved allocations.
- QRisk mitigation after routing at the new physical gate locations, with
  the final permutation and full ideal behavior preserved.

`examples/routing-line.mlir` contains a non-neighbor CX on a three-qubit
line. Identity mapping rejects it. Routing inserts one SWAP, records final
layout `[1,0,2]`, and passes independent topology/permutation verification.
The terminal measurement output order remains logical order.

`examples/qrisk-fez.mlir` uses the imported historical Fez pattern twice.
The native mitigation pass reports **2 occurrences before, 0 after**, with
two accepted commuting reorders. All four input basis states agree, including
complex phase. Toy fixtures remain in the tests after replacing the public
example with upstream observations. Source bytes for the three imported
reports were checked against their upstream Git blob hashes; catalog
provenance stores their SHA-256 hashes and the pinned commit.

The tests do not establish general compiler correctness, backend executability,
hardware fidelity, noise mitigation, dynamic-circuit semantics, or performance.
Docker has not been run. GitHub Actions uses the same Ubuntu/MLIR major-version
combination and executes all four CTest suites; per-commit results are
available in the repository's Actions tab. LLVM/MLIR versions other than 18.x
are not supported by this build configuration. Pattern observations do not
establish present-day backend faults or hardware improvement.
