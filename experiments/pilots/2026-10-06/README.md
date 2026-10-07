# Local pilot evidence — October 6, 2026 (America/Chicago)

These are smoke experiments and mechanism checks, not a completed paper
evaluation. No real hardware jobs were submitted.

| Question | Recorded trials | Outcome |
| --- | --- | --- |
| RQ1 | 16 circuits: 10 generated, 6 constructed | All passed; maximum phase-sensitive operator error 4.48e-16 and instrument error 8.89e-16 |
| RQ2 | 64 circuit/method rows | 62 eligible completed; 2 QRisk exclusions for barrier fixtures; no failures |
| RQ3 | 352 circuit/method/repeat rows | 346 completed, 4 QRisk barrier exclusions, 2 QRisk timeouts on the conflicting synthetic catalog |
| RQ4 | 32 paired local jobs, 1,024 shots per arm | All completed on IBM Runtime local mode with FakeFez/Aer; no hardware inference |

RQ1, RQ2 and RQ4 used clean revision
`5dc13a4d0b453d3e8ddd131147b444ef4b4856fd`. RQ3 used clean revision
`597607366afa6356e3af00d8a067c5f2a812121b`, which additionally terminates native
descendants when an experiment worker times out. The compiler binary is the
same in these runs. Manifests pin its SHA-256, LLVM/MLIR 18.1.3, Python 3.12.14,
Qiskit 2.5.2, Aer 0.17.2, Runtime 0.47.0, the configuration and catalog/Target
snapshots. CI separately passed on Qiskit 2.4.2 and 2.5.2. At the final code
revision, all 66 Python regressions and all three native CTest suites passed.

## What the pilot establishes

The ten independently generated workloads contained **zero matches** for the
small supplied historical catalog. They test integration and no-match behavior;
they do not establish reduction on independent workloads.

Across the six constructed cases, MLIRQ reduced 15 occurrences to 5. The five
remaining occurrences were behind conservative fences. On the four constructed
circuits eligible for every baseline, common scoped counts were 10→0 for MLIRQ,
10→5 for QRisk, 10→4 for random reordering and 10→10 for Qiskit-only. These are
deliberately constructed mechanism examples, not an efficacy ranking.

The conflicting synthetic catalog demonstrates why the acceptance guard
matters. Default MLIRQ blocks a reorder that introduces another pattern.
Total-only acceptance reduces total counts from 2 to 1 while introducing the
reverse pattern. Local-only acceptance exhausts its 10,000-candidate budget
while cycling. The unchanged upstream QRisk baseline reaches the 60-second
wall limit in both repetitions. Those timeouts are retained as failures; the
RQ3 command therefore exits with status 1. No MLIRQ correctness failure occurred.
The separate X/SX fixture exercises the non-diagonal rule ablation.

RQ4's mean paired TVD improvement (original minus MLIRQ) was 0.000435 for
generated workloads and −0.000326 for constructed cases. The circuit-bootstrap
95% intervals were [−0.004951, 0.005625] and [−0.006348, 0.006917], respectively.
Both cover zero. These local calibration-noise results validate the measurement
pipeline; Aer does not model QRisk's contextual hardware faults. There were no
independent hardware calibration windows, so these intervals are not hardware
effect estimates.

## Files and reproduction

Each `rq*-manifest.json` records the evaluated revision and environment.
Each `rq*-summary.json` includes all result groups and denominators.
RQ1/RQ2/RQ4 observation files retain compact per-circuit results. RQ3's
mechanism file contains all synthetic-ablation rows; its full summary covers
all 352 rows. No failed or excluded trial is imputed as a zero pattern count.

The full runners also generate raw JSONL, worker diagnostics, calibration
snapshots, complete environment locks and input/output QPY files. These larger
working outputs are not committed here. Reproduce them from the corresponding
evaluated revision with the commands in [the guide](../../../docs/experiments.md)
and the recorded smoke configuration. RQ3 timing measurements were run serially
after the simulator completed, but this small pilot is not a performance study.
Use larger held-out circuits/catalogs and later real calibration windows for
the paper evaluation.
