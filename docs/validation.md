# Validation

## Qiskit metadata, barriers, and export (2026-09-22)

T3/T4 adds **22 tests** to the 23 adapter tests. All **45 adapter tests** pass
with Qiskit **2.4.2** and **2.5.2** on Python 3.12.14, using the built Python
wheel and the rebuilt LLVM/MLIR 18.1.3 native compiler. The **47 native/importer
regressions** also pass, for **92 distinct tests**.

Round-trip coverage checks every supported gate, complex operators including
global phase, actual transpiler layout/routing metadata, sparse physical wires,
idle/auxiliary wires, multiple/alias registers, loose bits and ancillas,
instruction labels, and exact binary64 parameters. Exact branch simulation
checks partially/fully measured distributions and repeated classical-bit
writes, including unwritten bits. Barriers retain scope, order, and labels,
including empty barriers and barriers after measurement; no rewrite crosses
them. Snapshot and output mutations remain isolated.

The native exporter rejects invalid barriers, incomplete wire bookkeeping,
missing/duplicate instruction IDs, unknown semantic attributes, old bundle
schemas, and incomplete measurement outputs. Python export rejects altered
operations, physical operands, parameter bits, phase/backend context, lost
instructions, and fence crossings. Malformed/missing JSON produces a structured
error. Export reconstructs from parsed native operations, not assembly regexes.

The historical Fez fixture goes from **2 occurrences to 0**, remains equivalent
as a complete Qiskit operator, and still has zero matches after export,
reimport, and native rescanning. The conversion example also passes on both
Qiskit versions. CI installs and validates the wheel before running the suites.

These results establish T1–T4 for the supported subset. The separate output
instruction validation, complete optimization/report API, and remaining M1
integration/demo tasks remain on the roadmap. They do not establish hardware
fidelity improvements.

## Qiskit importer and native bridge (2026-09-21)

The T1/T2 adapter adds **23 tests**, all passing with Qiskit **2.4.2** and
**2.5.2** on Python 3.12.14. Both environments installed the built Python wheel;
the tests invoked the native LLVM/MLIR 18.1.3 compiler. The existing 47 native
and importer regressions also passed, giving 70 distinct regression tests.

The new coverage includes an actually transpiled circuit with initial layout
`[4, 1]`, exact physical gate operands after routing, full/idle/auxiliary wire
width, all supported gate semantics, phase-sensitive numerical comparisons,
binary64 edge cases, terminal measurement destinations, isolated context/catalog
snapshots, unsupported inputs, backend-specific scan/mitigate behavior, and
structured native errors/timeouts. The historical Fez sequence runs through
the bridge with two occurrences before and zero after mitigation.

The offline import example passed in both environments. Wheel contents were
checked to contain only the Python adapter and distribution metadata. A
separate setuptools build directory prevents CMake artifacts from entering
the portable wheel. CI repeats wheel validation, installation, and adapter
tests for both Qiskit versions, alongside the native CTest suites.

These tests establish the importer/native bridge for its documented subset.
Export and barrier support were added in the T3/T4 work above. The complete M1
API and hardware-fidelity evaluation remain later work.
See [qiskit-adapter.md](qiskit-adapter.md).

## Native core (2026-09-18)

Validated locally on 2026-09-18 using Ubuntu 24.04 (x86-64), GNU C++ 13.3.0,
and LLVM/MLIR 18.1.3. TableGen generation, all C++ compilation units, and the
`mlirq-opt` link completed successfully in Release mode.

The CTest suite invokes **47 regression tests** across three suites: 19 core
compiler tests, 20 QRisk compiler tests, and 8 importer tests. All passed.
The core randomized oracle evaluates 8 circuits on all 8 input basis states;
the QRisk randomized oracle adds 12 circuits on all 8 basis states. The
QRisk suite also checks every supported commutation family, each included
upstream pattern, and the measured Fez example on all basis inputs. Comparisons
use complex amplitudes at tolerance 1e-10, preserving global and relative phase.

```text
Test project .../mlirq/build
    Start 1: mlirq-regression
1/3 Test #1: mlirq-regression ................. Passed
    Start 2: mlirq-qrisk
2/3 Test #2: mlirq-qrisk ...................... Passed
    Start 3: mlirq-qrisk-import
3/3 Test #3: mlirq-qrisk-import ............... Passed

100% tests passed, 0 tests failed out of 3
```

The first two CTest entries exercise the compiled native binary. Python
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
combination and executes all three CTest suites; per-commit results are
available in the repository's Actions tab. LLVM/MLIR versions other than 18.x
are not supported by this build configuration. Pattern observations do not
establish present-day backend faults or hardware improvement.
