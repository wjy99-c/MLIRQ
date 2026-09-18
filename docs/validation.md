# Validation

Validated locally on 2026-09-18 using Ubuntu 24.04 (x86-64), GNU C++ 13.3.0,
and LLVM/MLIR 18.1.3. TableGen generation, all C++ compilation units, and the
`mlirq-opt` link completed successfully in Release mode.

The CTest suite invokes **19 native regression tests**. All passed. The
phase-sensitive randomized test additionally evaluates **8 circuits on all
8 input basis states: 64 numerical comparisons**, each at an absolute
complex-amplitude tolerance of 1e-10. This checks all unitary columns for
those generated circuits, including relative phase.

```text
Test project .../mlirq/build
    Start 1: mlirq-regression
1/1 Test #1: mlirq-regression ................. Passed

100% tests passed, 0 tests failed out of 1
```

The single CTest entry runs the 19-case Python unittest suite against the
compiled native binary. Python is a test driver and independent numerical
oracle; it is not the compiler implementation.

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

The tests do not establish general compiler correctness, backend executability,
hardware fidelity, noise mitigation, dynamic-circuit semantics, or performance.
Docker and remote GitHub Actions execution have not been run; the supplied
configurations use the same Ubuntu/MLIR major-version combination as the local
build. LLVM/MLIR versions other than 18.x are not supported by this initial
build configuration.
