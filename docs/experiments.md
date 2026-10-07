# Running the MLIRQ evaluation

The runners implement the four questions in the paper outline. They consume
already compiled physical circuits, call MLIRQ, save the actual returned
circuits, and use a separate Qiskit-level checker for final occurrence counts.
Real hardware submission is a separate, explicit command.

## Install once

From a fresh repository checkout on Ubuntu 24.04:

```sh
sudo apt-get update
sudo apt-get install -y git cmake ninja-build g++ python3-venv \
  libmlir-18-dev mlir-18-tools llvm-18-dev
git clone https://github.com/wjy99-c/MLIRQ.git
cd MLIRQ
bash scripts/setup-experiments.sh
```

The setup script builds LLVM/MLIR 18 code, installs the adapter and simulator
dependencies, runs regressions, and downloads the pinned QRisk baseline into
an ignored cache. It does not require an IBM account. Existing LLVM 18 installs
can set `MLIR_DIR` and `LLVM_DIR`; use `BUILD_JOBS` to limit build parallelism.
Direct dependencies are pinned in `experiments/requirements.txt`; every run
also saves the complete resolved `pip freeze`. LLVM and MLIR must share major 18.
Random streams are derived deterministically from the base seed, case, repeat
and purpose; sampling and submission-order streams are distinct.

For a Linux container (also usable through Docker Desktop):

```sh
docker build -f experiments/Dockerfile -t mlirq-experiments .
mkdir -p results
docker run --rm -v "$PWD/results:/work/results" mlirq-experiments \
  rq1 --output /work/results/rq1
```

## One command per research question

These commands run the small smoke configuration. Output directories must be
new: existing experimental records are never overwritten.

```sh
scripts/run-rq1.sh --output results/rq1
scripts/run-rq2.sh --output results/rq2
scripts/run-rq3.sh --output results/rq3
scripts/run-rq4.sh --output results/rq4-simulator
```

| Runner | Evaluates | Main artifacts |
| --- | --- | --- |
| RQ1 | Correctness and integration | Phase-sensitive operator comparison; full quantum/classical instruments up to four active wires; exact measured distributions; metadata, Target and rejection checks; regression log |
| RQ2 | Pattern reduction | Total/per-pattern counts, all eligible and initially matching inputs, blocked/partial/eliminated outcomes, baseline restrictions and failures |
| RQ3 | Cost and ablations | Repeated timings, native scan/search/candidate statistics, process memory, gate/depth/duration estimates, policy ablations, downstream scheduling traces |
| RQ4 | Execution quality pilot | Paired original/MLIRQ shots using IBM Runtime `SamplerV2` local mode over `AerSimulator.from_backend(FakeFez())`, raw counts, TVD and paired differences |

Each run writes `manifest.json`, `backend-snapshot.json`, `catalog.json`,
`config.json`, `requirements.lock.txt`, append-only `records.jsonl`,
`summary.csv`, `summary.json`, and input/output QPY files. Per-method worker
reports and errors remain beside their circuits. Manifests record source
revision and dirty state, compiler binary hash/version, catalog and backend
snapshot hashes, software versions, seeds, configuration and thread settings.
Keep the complete directory, not only a summary table. For publication, use a
clean commit, record its SHA, archive the native binary/build environment, and
retain all raw artifacts. CI artifacts are smoke checks, not paper results.

### Larger runs and your own circuits

```sh
scripts/run-rq2.sh --config experiments/configs/full.json --output results/full-rq2
scripts/run-rq3.sh --config experiments/configs/full.json --output results/full-rq3
scripts/run-rq4.sh --config experiments/configs/full.json --output results/full-rq4-sim
```

The full profile increases seeds, widths, depths, repeats, catalog sizes and
fixture repetition. It can take hours. Edit a copy of the JSON to declare the
study budget before running. `--limit N` intentionally selects the first N
cases and is recorded as a pilot restriction; do not use it to estimate
workload-wide coverage.

Use held-out compiled circuits with one `QuantumCircuit` per QPY file:

```sh
scripts/run-rq2.sh --input-dir my-compiled-qpy --catalog my-catalog.json \
  --config my-config.json --output results/heldout-rq2
```

The external circuits must already use the exact physical Target represented
by the configuration. No hidden transpilation occurs on this path. For
generated workloads, GHZ, QFT, QAOA-style, Grover and seeded random families
are transpiled once with the declared Qiskit optimization level and layout.
Their selection does not depend on pattern counts. Constructed pattern
fixtures and synthetic scaling/ablation cases are always labeled separately.
They test mechanisms; they are not independent efficacy benchmarks. External
files replace the generated family inputs; constructed fixtures remain in a
separate result group.

The supplied catalog contains only three historical observations. In the
initial smoke run, the independently generated inputs had no matches. A larger
profile alone does not solve that coverage limitation. Supply a frozen,
backend-specific catalog and independently selected later circuits; keep
discovery and evaluation workloads/windows separate. Preserve observation
timestamps/provenance in the catalog to study freshness. To compare tolerance
or catalog order, run separate catalog files and retain their hashes.

## Baselines and ablations

RQ2 and RQ3 run Qiskit-only output, the actual pinned QRisk `disrupt_patterns`
function, random legal adjacent reorders, and MLIRQ. QRisk revision
`c51b860505a06618371fd400f7da097c16689f01` is downloaded unchanged and its file
SHA-256 is checked before execution. No upstream code is vendored. QRisk's
global rounded-token matching and native counts are retained in its report;
the shared scoped checker scores every exported baseline circuit. Do not
compare different counting definitions as if they were the same metric.

The QRisk adapter accepts a unitary body followed only by terminal
measurements. It rejects barriers, interleaved effects and wildcard Rz
patterns instead of silently moving or deleting them. These exclusions stay
in the denominator as `ineligible`. Native MLIRQ still supports its documented
broader barrier/measurement subset. Compare algorithms on their common
eligible subset as well as reporting overall applicability.

The random baseline makes the configured number of attempted adjacent swaps,
checking phase-sensitive equality on the small union of the two gates' wires.
It preserves ordered operands and never crosses effects. It does not optimize
the catalog objective. Attempt counts and accepted swaps are reported.

Every baseline runs in a fresh process under the same wall-clock timeout.
MLIRQ additionally has a configured candidate budget. These are declared
resource caps, not a claim of equal internal work between algorithms. A
timeout/error remains a result, never a zero occurrence count. The shell
command returns nonzero for failures or when no input is eligible.

RQ3 adds:

- `global`: replace scope projection with the full user instruction stream.
- `total`: retain strict total reduction but allow an individual pattern to grow.
- `local`: accept a reduction of the currently matched pattern without the
  catalog-wide guard. A mandatory finite candidate budget bounds possible
  cycles. It can increase total or individual counts and is for evaluation only.
- `diagonal`: retain diagonal and disjoint commutations; disable the other
  rule families.

All variants are evaluated again using the common scoped final checker.
Native objective counts and the common counts are both available, so the
global ablation cannot appear successful merely by hiding scoped matches.
Conflicting synthetic catalogs expose the acceptance-policy differences;
duplicate observation copies stress catalog size without claiming extra
independent patterns. The standard API defaults retain the full safety guard.

Costs include full worker algorithm time and MLIRQ's import/native/export/
validation breakdown. Native `scan_seconds` sums initial and candidate scans;
`search_seconds` includes candidate scans and legality/acceptance work, so
these times overlap and must not be added. Counters report attempted endpoint
moves, legal candidates, scans and accepted rewrites. Peak RSS is measured per
fresh worker and per native child process; it is not incremental memory and
the two peaks are not necessarily simultaneous. Common external correctness
checks, initial Qiskit transpilation and output file writing are outside the
worker timing. Qiskit-only therefore measures copying already compiled input,
not the cost of Qiskit compilation. Compare MLIRQ end-to-end and native costs
separately from Python-only baseline pass costs.

Gate count and depth use exported circuits. `estimated_asap_seconds` is a
snapshot-duration estimate, not an actual hardware schedule. RQ3 separately
runs Qiskit ALAP scheduling/padding when available, saves the scheduled QPY,
and rescans that instruction trace. Added delays can affect trace counts;
these are robustness observations, not proof of executed pulse order.
MLIRQ itself still rejects timing-scheduled inputs.

## Real hardware: run these commands yourself

Use your existing locally saved IBM Runtime account. Account selection can be
specified with `--account-name NAME`; no token is placed in the repository,
configuration or result files. The implementation was tested with local
simulation and mocked submission/recovery, not with a real QPU job.

Prepare against the real backend Target, with a fresh calibration snapshot:

```sh
.venv/bin/python -m mlirq_qiskit.experiments.hardware prepare \
  --backend ibm_fez --window window-01 --output hardware-runs/window-01
```

This compiles and verifies circuits, saves original/transformed QPY and ideal
distributions, randomizes paired submission order, and prints job/PUB/shot
counts. It submits nothing. By default only independently generated (or
`--input-dir`) cases are included; `--include-fixtures` explicitly adds the
separate constructed group. Use `--config` and `--catalog` for your frozen
study configuration. Each job contains the original and transformed arms of
one circuit/repeat, and jobs are randomized across circuits/repeats.

Inspect `plan.json`. Preview pending submissions, then submit the desired
number explicitly:

```sh
.venv/bin/python -m mlirq_qiskit.experiments.hardware submit \
  --output hardware-runs/window-01
.venv/bin/python -m mlirq_qiskit.experiments.hardware submit \
  --output hardware-runs/window-01 --submit --max-jobs 10
.venv/bin/python -m mlirq_qiskit.experiments.hardware collect \
  --output hardware-runs/window-01
```

The first submit command is a dry run. The second can consume QPU quota.
Rerunning it submits only remaining prepared batches, up to the new cap.
`collect` polls recorded jobs and writes available results; rerun it later for
unfinished jobs. Job IDs are persisted immediately after each submission.
Gate/measurement twirling and dynamical decoupling are explicitly disabled.
The service may still schedule or transform circuits: preserve accessible
metadata and do not equate the submitted QPY trace with an observed pulse trace.

If a network interruption leaves `submission_started` without an ID, it is
never resubmitted automatically. Find the job by the run/batch tags in your
IBM account, then attach the ID:

```sh
.venv/bin/python -m mlirq_qiskit.experiments.hardware attach \
  --output hardware-runs/window-01 --batch 0 --job-id YOUR_JOB_ID
```

The command checks backend and tags before attachment. If the process was
forcibly killed, inspect its status before removing a stale `submission.lock`.
Keep uncertain submissions until you have resolved them with the service.

Repeat preparation in later calibration windows using distinct labels and
directories. Analyze collected windows together:

```sh
.venv/bin/python -m mlirq_qiskit.experiments.hardware summarize \
  --runs hardware-runs/window-01 hardware-runs/window-02 \
  --output hardware-runs/combined
```

The primary metric is preregistered by the runner as TVD to the exact ideal
distribution, with `TVD(original) - TVD(MLIRQ)` positive for improvement.
Both neutral and harmful changes are retained. Local summaries aggregate
repetitions within a circuit before bootstrapping circuits. Multi-window
summaries resample windows, then circuit means within windows; they give no
cross-window interval for a single window. Shots are not treated as
independent experimental repetitions. Missing jobs remain in the reported
denominators. Use multiple independent windows and enough circuits before
interpreting these intervals as paper evidence.

For a completely offline rehearsal of preparation and dry-run submission:

```sh
.venv/bin/python -m mlirq_qiskit.experiments.hardware prepare \
  --offline --output hardware-runs/offline
.venv/bin/python -m mlirq_qiskit.experiments.hardware submit \
  --output hardware-runs/offline
```

An offline-prepared plan cannot be submitted to hardware. Prepare a new plan
against the real Target first.

## Interpretation limits

Aer uses a gate/local calibration noise model. It does not reproduce QRisk's
context-dependent hardware failures. The simulator experiment validates
execution, bit interpretation, pairing, metrics and artifacts; it cannot
establish the hardware efficacy claim. No real jobs are submitted by setup,
CI, RQ1–RQ4 local scripts, or offline preparation.

Full phase-sensitive operators scale exponentially and are limited by the
configured active-wire cap. Full instrument comparison is capped at four
active wires. Other supported cases retain structural/Target checks but
explicitly report unperformed exact checks. Oracle-only wire compression does
not change the saved physical circuit or its layout. Reset/reuse, dynamic
control, unbound parameters and timing inputs remain outside MLIRQ's contract.

Official interfaces used:
[IBM local testing](https://quantum.cloud.ibm.com/docs/en/guides/local-testing-mode),
[Aer simulation](https://quantum.cloud.ibm.com/docs/en/guides/simulate-with-qiskit-aer),
[Target validation](https://quantum.cloud.ibm.com/docs/en/api/qiskit/qiskit.transpiler.Target).
