# QRisk compilation passes

The integration implements backend-specific matching and equivalent
transformations that disrupt matched gate sequences. It uses the public
[qzydustin/qrisk](https://github.com/qzydustin/qrisk) project, pinned for the
included data to commit `c51b860505a06618371fd400f7da097c16689f01`.
The compiler and importer run offline and submit no hardware jobs.

## Run

```sh
# Read-only gate scan (adds a report attribute).
build/bin/mlirq-opt examples/qrisk-fez.mlir \
  --mlirq-qrisk-scan="patterns-file=patterns/qrisk-imported.json"

# Equivalent reordering, followed by target verification.
build/bin/mlirq-opt examples/qrisk-fez.mlir \
  --mlirq-qrisk-mitigate="patterns-file=patterns/qrisk-imported.json" \
  --mlirq-verify-target
```

The example repeats one actual Fez observation twice. Both occurrences are
removed by commuting Rz with CZ on their shared wire. Allocations remain on
physical qubits 3 and 4, and the ordered measurement outputs are preserved.
The repetition is constructed for a regression test; it is not an upstream
benchmark circuit. Its topology records only the observed edge and historical
156-qubit capacity, not a full or current device configuration.

The current pass requires already physically mapped IR whose gate
representation matches the catalog. `--mlirq-map-identity` supports simple
logical examples only when their interactions already fit the target topology;
the custom M1 router has been removed.

The planned Qiskit adapter will supply placement, routing, and native-gate
translation. Run QRisk after Qiskit translation and optimization. Later gate
rewrites can recreate patterns, so scan again after them. The adapter,
scheduling, and executable output are not implemented by this increment.

## Included observations and provenance

Toy fixtures were validated first and are retained in
`test/fixtures/qrisk-toy.json`. The public example now uses
`patterns/qrisk-imported.json`, containing these original token sequences:

| Backend | Report date | Physical gate sequence |
| --- | --- | --- |
| `ibm_fez` | 2026-06-17 | SX(3), CZ(3,4), Rz(3,-3.141593), SX(4) |
| `ibm_kingston` | 2026-05-27 | Rz(2,-1.570796), Rz(2,1.570796), Rz(3,-3.141593), CZ(2,3) |
| `ibm_marrakesh` | 2026-06-17 | SX(3), Rz(3,0.392699), Rz(16,3.141593), CZ(3,16) |

These are historical DDMin candidates, each sourced from one report. They
are not claimed to be independently rediscovered across windows or currently
problematic. Later reports can be empty. The catalog records the full source
commit, timestamp, path, and SHA-256 for each observation. The reports omit
backend metadata; the explicit backend binding follows their upstream
artifact directories and is recorded in provenance.

Pinned sources:

- [Fez report](https://github.com/qzydustin/qrisk/blob/c51b860505a06618371fd400f7da097c16689f01/artifacts/grover3-fez-O3-2_3_4_16/ddmin_reports/fez_20260617_150600.json)
- [Kingston report](https://github.com/qzydustin/qrisk/blob/c51b860505a06618371fd400f7da097c16689f01/artifacts/grover3-kingston-O3-2_3_4_16/ddmin_reports/kingston_20260527_151028.json)
- [Marrakesh report](https://github.com/qzydustin/qrisk/blob/c51b860505a06618371fd400f7da097c16689f01/artifacts/grover3-marrakesh-O3-2_3_4_16/ddmin_reports/marrakesh_20260617_150730.json)

## Import more QRisk data

The standard-library adapter reads both QRisk DDMin `pattern` token arrays
and `bad_pattern_memory.json` entries with `tokens` and `evidence`. It preserves
gate names, ordered physical operands, and parameters. Unsupported gates,
symbolic/nonfinite angles, malformed operands, mixed-backend memory evidence,
and simulated DDMin observations fail explicitly. Empty/cleared report
patterns are ignored, including originals retained under `_clean_override`.

To reproduce the shipped catalog from a checkout of the pinned QRisk revision:

```sh
python3 tools/import_qrisk.py \
  --qrisk-root /path/to/qrisk \
  --source-revision c51b860505a06618371fd400f7da097c16689f01 \
  --report ibm_fez:artifacts/grover3-fez-O3-2_3_4_16/ddmin_reports/fez_20260617_150600.json \
  --report ibm_kingston:artifacts/grover3-kingston-O3-2_3_4_16/ddmin_reports/kingston_20260527_151028.json \
  --report ibm_marrakesh:artifacts/grover3-marrakesh-O3-2_3_4_16/ddmin_reports/marrakesh_20260617_150730.json \
  --min-observations 1 \
  --output patterns/qrisk-imported.json
```

Use `--database ibm_fez:relative/path/bad_pattern_memory.json` to import a
pattern memory. Both input options may be repeated. By default, at least
two distinct observation timestamps are required; the example deliberately
selects one to demonstrate real historical candidates. Duplicate files or
repeated timestamps do not add evidence. Promotion groups exactly equal
tokens per backend, which is more conservative than upstream's tolerant
parameter grouping. It does not certify independent experiments or freshness.
The source revision is supplied by the caller; use the matching checkout.

## Catalog and matching contract

The compiler reads a required local JSON file with `schema_version: 1`, a
nonempty `source_url`, and a `patterns` array. Each pattern has a unique `id`,
an exact `backend`, and at least two `gates`. A gate has a supported `gate`
name and concrete nonnegative `qubits` in operand order. Rz may specify
`angle` in radians; omitting it is an explicit wildcard used by toy tests.
Unknown functional fields and malformed catalogs are errors. Optional
`description` and `provenance` are documentation, not executable rules.

The importer always supplies Rz angles and `angle_tolerance: 0.00001`, matching
QRisk's database comparison tolerance for its rounded tokens. Handwritten
catalogs default to exact f64 comparison; tolerance may be set from zero to
1e-5. Matching tolerance never rounds or modifies circuit angles and plays no
role in proving a commuting identity.

Only patterns whose backend equals `mlirq.target.name` participate. Matching
uses physical allocations, not SSA variable names or allocation order. A
pattern's scope is the union of its physical wires. The matcher projects the
circuit onto every operation touching those wires, then counts ordered,
contiguous occurrences in that projection, including overlaps. Disjoint
spectator gates do not hide a recurrence; any intervening operation touching
the scope interrupts a match. CX and CZ token operand order is retained to
match QRisk's stored tokens, even though CZ's ideal semantics are symmetric.

This is a scoped ordered matcher, not a complete moment-DAG/isomorphism
matcher. QRisk's online implementation matches a global linear token stream;
MLIRQ deliberately ignores unrelated spectators and preserves lifecycle
barriers. Equivalent serializations of disjoint gates within the pattern's
scope can still have different scan counts. The mitigation pass therefore
never accepts a purely disjoint reorder as a disruption.

## Equivalent rewrites and report

Candidate gate pairs inside a matched occurrence must share a physical wire.
The pass may move either gate across intervening gates only if each required
commutation is known to be exact, including global phase. The whitelist is:

- Diagonal Z, Rz, and CZ gates commute with each other.
- Identical unary gate kinds commute; X and SX commute on the same wire.
- Z/Rz on a CX control and X/SX on its target commute with CX.
- Two CX gates commute when neither control is the other's target.
- Disjoint gates may be crossed while implementing a shared-wire rewrite.

H/Z on the same wire, X on a CX control, SX/CZ on a shared wire, and other
unproven cases remain unchanged. Allocations, measurement, discard, and
opaque gate annotations are barriers, including on spectator wires.

Every candidate is rescanned against **all active backend patterns**. It is
accepted only if the total occurrence count strictly decreases and no active
pattern count increases. This bounds the number of accepted rewrites and
prevents oscillation. The pass rewires SSA successors by physical identity,
preserves placement and gate parameters, and verifies ownership, dominance,
and topology on a cloned module before replacing the original body.

`mlirq.qrisk.report` records backend, source URL, mode, before/after totals,
accepted rewrite count, per-pattern counts, and remaining occurrences.
`operation_indices` are zero-based positions in the current circuit block,
including allocations. Status is `no_backend_patterns`, `no_matches`,
`matched` (scan), `eliminated`, `partial`, or `blocked`.

This is a conservative local search, not a guarantee that every mathematically
possible disruption will be found. A swap that only moves a hit to a neighbor
is rejected. Remaining occurrences are reported. No gate insertion,
decomposition, resynthesis, timing change, or physical qubit remapping is
attempted. No calibration expiry policy or measured fidelity benefit is
claimed by these compiler tests.

Upstream contracts inspected: [pattern storage and tokens](https://github.com/qzydustin/qrisk/blob/c51b860505a06618371fd400f7da097c16689f01/quantum/pattern_db.py),
[pattern disruption](https://github.com/qzydustin/qrisk/blob/c51b860505a06618371fd400f7da097c16689f01/quantum/pattern_transform.py),
and [DDMin extraction](https://github.com/qzydustin/qrisk/blob/c51b860505a06618371fd400f7da097c16689f01/quantum/delta_debug.py).
