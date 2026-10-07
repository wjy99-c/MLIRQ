"""Isolated baseline worker: every algorithm has the same wall-time cap."""
import argparse
import importlib.util
import random
import resource
import sys
import time
import numpy as np
from qiskit import QuantumCircuit
from qiskit.quantum_info import Operator
from mlirq_qiskit import NativeCompiler, MitigationOptions, optimize_compiled_circuit
from .common import ROOT, digest, load_circuit, read_json, save_circuit, write_json

QRISK_REVISION = "c51b860505a06618371fd400f7da097c16689f01"
QRISK_SHA256 = "1c21a1ceb379663fab72c2f55856c7059d64cc696712ef9fe31f063c40d9bc5f"


def load_qrisk():
    path = ROOT / ".cache/qrisk/pattern_transform.py"
    if not path.exists():
        raise RuntimeError("Pinned baseline missing: run python scripts/fetch-qrisk.py")
    if digest(path.read_bytes()) != QRISK_SHA256:
        raise RuntimeError("Pinned QRisk baseline hash mismatch")
    spec = importlib.util.spec_from_file_location("mlirq_pinned_qrisk", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def qrisk_transform(circuit, catalog, backend):
    # Preserve upstream behavior. Do not silently strip or reposition fences.
    terminal = False
    for item in circuit.data:
        if item.operation.name == "measure":
            terminal = True
        elif item.operation.name == "barrier" or terminal:
            raise ValueError("QRisk common-subset restriction: gates followed only by terminal measurements")
    patterns = []
    for pattern in catalog["patterns"]:
        if pattern["backend"] != backend:
            continue
        if any(g["gate"] == "rz" and "angle" not in g for g in pattern["gates"]):
            raise ValueError("QRisk baseline cannot represent wildcard Rz angles")
        patterns.append([(g["gate"], tuple(g["qubits"]),
                          (round(g["angle"], 6),) if "angle" in g else ()) for g in pattern["gates"]])
    upstream = load_qrisk()
    def counts(value):
        tokens = upstream._token_sequence(value)
        return [len(upstream._find_pattern_occurrences(tokens, p)) for p in patterns]
    before = counts(circuit)
    output = upstream.disrupt_patterns(circuit, patterns)
    return output, {"upstream_revision": QRISK_REVISION, "source_sha256": QRISK_SHA256,
                    "native_before": before, "native_after": counts(output),
                    "native_definition": "global gate tokens; angles rounded to six decimals"}


def random_legal(circuit, seed, attempts):
    rng = random.Random(seed)
    order = list(circuit.data)
    accepted = 0
    for _ in range(attempts):
        if len(order) < 2:
            break
        index = rng.randrange(len(order) - 1)
        first, second = order[index:index+2]
        if any(item.operation.name in {"barrier", "measure"} for item in (first, second)):
            continue
        wires = sorted({circuit.find_bit(q).index for item in (first, second) for q in item.qubits})
        pair = QuantumCircuit(len(wires))
        reverse = QuantumCircuit(len(wires))
        for destination, items in ((pair, (first, second)), (reverse, (second, first))):
            for item in items:
                destination.append(item.operation, [wires.index(circuit.find_bit(q).index) for q in item.qubits])
        if np.max(np.abs(Operator(pair).data - Operator(reverse).data)) < 1e-12:
            order[index:index+2] = [second, first]
            accepted += 1
    output = circuit.copy_empty_like()
    for item in order:
        output.append(item.operation, [output.qubits[circuit.find_bit(q).index] for q in item.qubits],
                      [output.clbits[circuit.find_bit(c).index] for c in item.clbits])
    return output, {"attempt_budget": attempts, "accepted_adjacent_swaps": accepted,
                    "semantics_check": "phase-sensitive exact small matrices, tolerance 1e-12"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("input", "output", "report", "catalog", "config", "compiler", "method"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    config, catalog = read_json(args.config), read_json(args.catalog)
    circuit = load_circuit(args.input)
    from qiskit_ibm_runtime import fake_provider
    backend = getattr(fake_provider, config["backend_class"])()
    compiler = NativeCompiler(args.compiler, timeout=config["timeout_seconds"])
    start = time.perf_counter()
    if args.method == "qiskit":
        output, detail = circuit.copy(), {"kind": "compiled input; no additional pass"}
    elif args.method == "qrisk":
        output, detail = qrisk_transform(circuit, catalog, config["backend_name"])
    elif args.method == "random":
        output, detail = random_legal(circuit, args.seed, config["random_attempts"])
    else:
        options = {"max_candidates": config["max_candidates"]}
        if args.method == "global":
            options["matching"] = "global"
        elif args.method in {"total", "local"}:
            options["acceptance"] = args.method
        elif args.method == "diagonal":
            options["rules"] = "diagonal"
        elif args.method != "mlirq":
            raise ValueError("Unknown method")
        result = optimize_compiled_circuit(circuit, backend_name=config["backend_name"], target=backend.target,
                                          patterns=catalog, compiler=compiler, options=MitigationOptions(**options))
        output, detail = result.circuit, result.report
    elapsed = time.perf_counter() - start
    # Each worker is fresh: these are process high-water marks, not RSS deltas.
    multiplier = 1 if sys.platform == "darwin" else 1024
    memory = {"worker_peak_rss_bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * multiplier),
              "native_child_peak_rss_bytes": int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * multiplier)}
    sha = save_circuit(args.output, output)
    write_json(args.report, {"wall_seconds": elapsed, "memory": memory,
                             "output_sha256": sha, "detail": detail})


if __name__ == "__main__":
    main()
