# L1 placement and routing

`mlirq-route` implements the README's M1 task: physical routing with an
explicit final permutation and an equivalence oracle that decodes it.

```sh
build/bin/mlirq-opt examples/routing-line.mlir \
  --mlirq-logical-opt --mlirq-route --mlirq-verify-target

# Place the example's logical wires at physical sites 2, 1, and 0 initially.
build/bin/mlirq-opt examples/routing-line.mlir \
  --mlirq-route="initial-layout=2,1,0" --mlirq-verify-target
```

The pass starts from logical IR with `mlirq.target`. It performs placement
itself, so do not run `mlirq-map-identity` before it. The original mapper
remains available for circuits that already fit the topology. The router
works on a cloned module and commits only after all circuits pass the
independent IR, topology, and permutation verifiers.

## SWAP semantics

`%a1, %b1 = mlirq.swap %a, %b` exchanges the two quantum states. Its results
retain **physical operand order**: `%a1` is the successor of the physical
wire carrying `%a`, but now contains the former state of `%b`. The ideal
matrix sends `|a,b>` to `|b,a>` with no added phase. Each input and output
obeys the same one-use ownership rule as the other quantum gates.

An ordinary SWAP in the source is a requested logical operation. A SWAP
inserted by routing carries the unit attribute `mlirq.routing_swap`. For
inserted SWAPs, the compiler also exchanges the logical-to-physical placement
entries so later gates continue operating on the intended logical states.
Source SWAPs change the desired circuit behavior and do not change that
placement bookkeeping. Tests distinguish the two cases.

## Placement and path selection

By default, logical allocation `i` starts on physical site `i`. An optional
`initial-layout` list supplies one distinct, in-range site per logical
allocation. The option applies to each circuit in the module; all must have
the matching number of allocations. Placement is deterministic, not a
calibration-aware optimization heuristic.

For each CX, CZ, or source SWAP, breadth-first search explores the pair of
operand locations. Each transition is a legal SWAP touching either operand;
the search stops at a pair where the requested gate is legal. It finds a
minimum number of such SWAPs for that gate under the current live-wire
constraints. Moving either operand is necessary for some directed targets.
Control/left moves precede target/right moves, and neighbor indices are
sorted to make ties reproducible regardless of coupling-array order.

All displaced logical states are tracked, including spectators. This is
greedy per-gate routing, not a globally minimum-SWAP circuit optimizer.
The search has at most N(N-1) distinct operand-placement states for a target
with N available sites. It neither schedules gates nor consults hardware.

CX obeys the target's directed coupling. CZ accepts an edge in either
orientation. SWAP requires both CX directions, corresponding to
`CX(a,b); CX(b,a); CX(a,b)`. A one-way edge can still support a direct CX/CZ,
but cannot carry a routing SWAP. Direction correction with additional gates
belongs to the next native-lowering milestone. Missing paths, insufficient
capacity, and unsupported direction requirements produce errors.

## Unused and retired physical sites

If a selected path uses an unallocated site, the router creates a fresh
zero-state allocation with `mlirq.routing_ancilla`. These auxiliary states
participate only in routing SWAPs and are discarded at their final sites.
They are never measured or used as operands of logical gates. The independent
verifier enforces this restriction by tracking the original auxiliary state
through SWAPs, even when it occupies a different physical wire.

Sites reserved for logical allocations that occur later in the source are
unavailable until allocated. Sites consumed by measurement/discard remain
unavailable; the router never resets or reuses them. It preserves operation
order except for inserted routing operations, and preserves the order of
classical outputs. Opaque annotations on source quantum gates are rejected
because the router has no calibration/timing relocation contract.

This is fresh, within-circuit auxiliary allocation. Cross-circuit sharing,
reset, and HALO resource policies remain outside this milestone.

## Final permutation artifact

For `examples/routing-line.mlir`, the circuit receives:

```mlir
mlirq.routing = {
  algorithm = "bfs",
  initial_layout = array<i64: 0, 1, 2>,
  final_layout = array<i64: 1, 0, 2>,
  auxiliary_initial_layout = array<i64>,
  auxiliary_final_layout = array<i64>
}
```

Entry `i` in `initial_layout` is the initial physical location of the `i`th
original logical allocation; entry `i` in `final_layout` is where that
logical wire ends, including its measurement/discard site. Auxiliary arrays
use the order in which auxiliary states were allocated. Combined final
locations permute the combined initial locations. This artifact explicitly
records the wire permutation; SWAP insertion need not return wires home.

The verifier in `lib/IR/RoutingMetadata.cpp` is separate from the search. It
checks allocation agreement, unique sites, marker types, auxiliary-state
uses, and final locations by replaying marked SWAPs. Merely editing the final
layout to another permutation is rejected. Topology is independently checked
in `Target.cpp`; verification runs on parsing and through
`--mlirq-verify-target`.

## Numerical and pipeline validation

The Python oracle simulates SWAP as an explicit matrix action on physical
wire slots. For each logical basis input, it encodes the initial placement,
initializes auxiliary states to zero, runs the emitted gate sequence, and
decodes the final placement. It compares the entire complex statevector
against the original logical circuit tensored with zero auxiliary states.
No postselection, phase normalization, or probability-only comparison is used.

The suite includes 12 random four-qubit circuits on all 16 basis states,
line/ring topologies, reversed CX, source SWAPs, nonidentity initial layouts,
unused sites, ordered measurements, and negative topology/permutation cases.
This checks arbitrary-input linear behavior for the tested circuits, not a
proof for every possible input program. Numerical cases use terminal
measurement/discard; lifecycle rejection cases are checked structurally.

QRisk scanning and mitigation may follow routing. They see the gates' actual
physical indices, and cannot commute through a SWAP. The integration test
routes a circuit, removes a QRisk occurrence at its new physical location,
rechecks the routing record, and compares all logical input basis states.
Later native lowering will need its own contract for preserving this mapping
when decomposing SWAP operations.
