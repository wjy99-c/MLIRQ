"""Deterministic, separately labeled generated workloads and constructed cases."""
from copy import deepcopy
from dataclasses import dataclass
import math
from pathlib import Path
import random
from qiskit import QuantumCircuit, transpile
from .common import load_circuit


@dataclass
class Case:
    name: str
    group: str
    circuit: QuantumCircuit
    seed: int
    catalog: dict


def logical_circuit(family, width, depth, seed):
    rng = random.Random(seed)
    circuit = QuantumCircuit(width, width, name=f"{family}-w{width}-d{depth}-s{seed}")
    circuit.metadata = {"family": family, "generator_seed": seed}
    circuit.global_phase = 0.137
    for _ in range(depth):
        if family == "ghz":
            circuit.h(0)
            for q in range(width - 1):
                circuit.cx(q, q + 1)
        elif family == "qft":
            for q in range(width):
                circuit.h(q)
                for other in range(q + 1, width):
                    circuit.cp(math.pi / 2 ** (other - q), other, q)
        elif family == "qaoa":
            for q in range(width):
                circuit.h(q)
            for q in range(width - 1):
                circuit.rzz(rng.uniform(-math.pi, math.pi), q, q + 1)
            for q in range(width):
                circuit.rx(rng.uniform(-math.pi, math.pi), q)
        elif family == "grover":
            for q in range(width):
                circuit.h(q)
            circuit.h(width - 1)
            circuit.mcx(list(range(width - 1)), width - 1)
            circuit.h(width - 1)
            for q in range(width):
                circuit.h(q)
                circuit.x(q)
            circuit.h(width - 1)
            circuit.mcx(list(range(width - 1)), width - 1)
            circuit.h(width - 1)
            for q in range(width):
                circuit.x(q)
                circuit.h(q)
        elif family == "random":
            for q in range(width):
                circuit.ry(rng.uniform(-math.pi, math.pi), q)
                circuit.rz(rng.uniform(-math.pi, math.pi), q)
            for q in range(width - 1):
                circuit.cx(q, q + 1)
        else:
            raise ValueError(f"Unknown workload family {family}")
    # Nontrivial destination ordering tests physical/classical interpretation.
    circuit.measure(range(width), list(reversed(range(width))))
    return circuit


def fixture(catalog, backend, backend_name, repetitions, *, blocked=False, distractors=False):
    active = [p for p in catalog["patterns"] if p["backend"] == backend_name]
    if not active:
        return None
    pattern = active[0]
    scope = sorted({q for gate in pattern["gates"] for q in gate["qubits"]})
    circuit = QuantumCircuit(backend.num_qubits, len(scope), name="constructed-pattern")
    circuit.global_phase = 0.137
    for _ in range(repetitions):
        for index, gate in enumerate(pattern["gates"]):
            args = ([gate.get("angle", 0.37)] if gate["gate"] == "rz" else []) + gate["qubits"]
            getattr(circuit, gate["gate"])(*args)
            if distractors:
                other = next(q for q in range(backend.num_qubits) if q not in scope)
                circuit.sx(other)
            if blocked and index == 1:
                # Disjoint/empty barriers are invisible to scoped matching,
                # but conservatively prevent a rewrite crossing their epoch.
                circuit.barrier(next(q for q in range(backend.num_qubits) if q not in scope))
    circuit.measure(scope, range(len(scope)))
    return circuit


def cases(config, catalog, backend, *, input_dir=None, stress=False):
    if input_dir:
        paths = sorted(Path(input_dir).glob("*.qpy"))
        if not paths:
            raise ValueError("Input directory contains no QPY circuits")
        for index, path in enumerate(paths):
            yield Case(f"external-{index:04d}-{path.stem}", "external_compiled", load_circuit(path),
                       config["seeds"][0], catalog)
    else:
        for family in config["families"]:
            for width in config["widths"]:
                for depth in config["depths"]:
                    for seed in config["seeds"]:
                        logical = logical_circuit(family, width, depth, seed)
                        compiled = transpile(logical, backend=backend, seed_transpiler=seed,
                                             optimization_level=config["optimization_level"],
                                             initial_layout=config["initial_layout"][:width])
                        yield Case(logical.name, "generated_workload", compiled, seed, catalog)
    for count in config["fixture_repetitions"]:
        for kind in ("plain", "fenced", "distractors"):
            circuit = fixture(catalog, backend, config["backend_name"], count,
                              blocked=kind == "fenced", distractors=kind == "distractors")
            if circuit is not None:
                yield Case(f"fixture-{kind}-r{count}", "constructed_fixture", circuit,
                           config["seeds"][0], catalog)
    if stress:
        # Adversarial catalogs distinguish acceptance/rule ablations. These
        # are mechanism tests, never claimed hardware observations.
        if backend.target.instruction_supported("rz", (3,), parameters=[0.3]):
            def rz_gate(angle):
                return {"gate": "rz", "qubits": [3], "angle": angle}
            forward, reverse = [rz_gate(0.3), rz_gate(0.7)], [rz_gate(0.7), rz_gate(0.3)]
            conflict = {"schema_version": 1, "source_url": "synthetic:acceptance-stress",
                        "patterns": [{"id": name, "backend": config["backend_name"], "gates": gates}
                                     for name, gates in [("forward-a", forward), ("forward-b", forward), ("reverse", reverse)]]}
            circuit = QuantumCircuit(backend.num_qubits, 1)
            circuit.rz(0.3, 3); circuit.rz(0.7, 3); circuit.measure(3, 0)
            yield Case("stress-conflicting-patterns", "synthetic_ablation", circuit, config["seeds"][0], conflict)
            rules = {"schema_version": 1, "source_url": "synthetic:rule-stress",
                     "patterns": [{"id": "x-sx", "backend": config["backend_name"],
                                   "gates": [{"gate": "x", "qubits": [3]}, {"gate": "sx", "qubits": [3]}]}]}
            circuit = QuantumCircuit(backend.num_qubits, 1)
            circuit.x(3); circuit.sx(3); circuit.measure(3, 0)
            yield Case("stress-axis-rule", "synthetic_ablation", circuit, config["seeds"][0], rules)
        # Synthetic duplicate observations stress K, not independent evidence.
        active = [p for p in catalog["patterns"] if p["backend"] == config["backend_name"]]
        for scale in config["catalog_scales"]:
            for count in config["fixture_repetitions"]:
                expanded = deepcopy(catalog)
                expanded["patterns"] = []
                for copy in range(scale):
                    for pattern in active:
                        item = deepcopy(pattern)
                        item["id"] += f"-stress-copy-{copy}"
                        expanded["patterns"].append(item)
                circuit = fixture(expanded, backend, config["backend_name"], count)
                if circuit is not None:
                    yield Case(f"stress-k{scale}-r{count}", "synthetic_scaling", circuit,
                               config["seeds"][0], expanded)
