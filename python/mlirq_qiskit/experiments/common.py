"""Artifacts, provenance and small-circuit semantic oracles."""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import numpy as np
from qiskit import QuantumCircuit, qpy
from qiskit.quantum_info import Operator, Statevector


def repository_root():
    candidates = [Path(os.environ.get("MLIRQ_REPO", Path.cwd())), Path.cwd(), *Path(__file__).resolve().parents]
    for path in candidates:
        if (path / "pyproject.toml").is_file() and (path / "patterns/qrisk-imported.json").is_file():
            return path.resolve()
    # Experiment data lives in the source repository, not the portable wheel.
    return Path.cwd().resolve()


ROOT = repository_root()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n")
    temporary.replace(path)


def digest(data):
    if not isinstance(data, bytes):
        data = json.dumps(data, sort_keys=True, allow_nan=False, default=str).encode()
    return hashlib.sha256(data).hexdigest()


def save_circuit(path, circuit):
    buffer = io.BytesIO()
    qpy.dump(circuit, buffer)
    data = buffer.getvalue()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(data)
    return digest(data)


def load_circuit(path):
    with Path(path).open("rb") as stream:
        circuits = qpy.load(stream)
    if len(circuits) != 1:
        raise ValueError("Expected exactly one compiled circuit per QPY file")
    return circuits[0]


def snapshot_backend(backend):
    properties = backend.properties()
    return {"name": backend.name, "class": type(backend).__name__,
            "num_qubits": backend.num_qubits, "dt": backend.dt,
            "properties": properties.to_dict() if properties else None,
            "configuration": backend.configuration().to_dict(),
            "target": [{"name": name, "qargs": qargs,
                        "duration": prop.duration if prop else None,
                        "error": prop.error if prop else None}
                       for name in backend.target.operation_names
                       for qargs, prop in backend.target[name].items()]}


def manifest(config, catalog, backend, compiler, rq):
    def git(*args):
        result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None
    snapshot = snapshot_backend(backend)
    return {"schema_version": 1, "rq": rq, "created_utc": datetime.now(timezone.utc).isoformat(),
            "git_revision": git("rev-parse", "HEAD"), "git_dirty": bool(git("status", "--porcelain")),
            "config": config, "catalog_sha256": digest(catalog),
            "backend_snapshot_sha256": digest(snapshot),
            "native_binary_sha256": digest(Path(compiler.executable).read_bytes()),
            "native_version": subprocess.run([compiler.executable, "--version"],
                                             capture_output=True, text=True, check=True).stdout.strip(),
            "platform": platform.platform(), "python": sys.version,
            "cpu_count": os.cpu_count(),
            "thread_environment": {key: os.environ.get(key) for key in
                                   ["OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"]},
            "packages": {name: importlib.metadata.version(name) for name in
                         ["mlirq-qiskit", "qiskit", "qiskit-aer", "qiskit-ibm-runtime", "numpy"]},
            "hardware_submitted": False, "simulator_interpretation":
            "Calibration-based local noise; not a model of QRisk contextual hardware faults."}


def compact(circuit, active=None, *, unitary=False):
    """Oracle-only wire compression. The saved/exported circuit stays physical."""
    active = sorted(active if active is not None else
                    {circuit.find_bit(q).index for item in circuit.data
                     if item.operation.name != "barrier" for q in item.qubits})
    positions = {q: i for i, q in enumerate(active)}
    result = QuantumCircuit(len(active), 0 if unitary else circuit.num_clbits,
                            global_phase=circuit.global_phase)
    for item in circuit.data:
        if item.operation.name == "barrier" or unitary and item.operation.name == "measure":
            continue
        result.append(item.operation, [positions[circuit.find_bit(q).index] for q in item.qubits],
                      [] if unitary else [circuit.find_bit(c).index for c in item.clbits])
    return result


def ideal_distribution(circuit):
    """Exact branching, including partial measurements and classical overwrites."""
    circuit = compact(circuit)
    branches = [(0, Statevector.from_int(0, 2**circuit.num_qubits).data)]
    indices = np.arange(2**circuit.num_qubits)
    for item in circuit.data:
        qubits = [circuit.find_bit(q).index for q in item.qubits]
        if item.operation.name != "measure":
            branches = [(bits, Statevector(state).evolve(item.operation, qubits).data)
                        for bits, state in branches]
            continue
        bit = circuit.find_bit(item.clbits[0]).index
        following = []
        for bits, state in branches:
            for outcome in (0, 1):
                projected = state.copy()
                projected[((indices >> qubits[0]) & 1) != outcome] = 0
                if np.vdot(projected, projected).real > 1e-16:
                    following.append(((bits & ~(1 << bit)) | (outcome << bit), projected))
        branches = following
    probabilities = defaultdict(float)
    for bits, state in branches:
        probabilities[format(bits, f"0{circuit.num_clbits}b")] += float(np.vdot(state, state).real)
    return dict(probabilities)


def tvd(left, right):
    return 0.5 * sum(abs(left.get(k, 0) - right.get(k, 0)) for k in left.keys() | right.keys())


def probabilities(counts):
    total = sum(counts.values())
    return {key.replace(" ", ""): value / total for key, value in counts.items()}


def instrument(circuit, active):
    """Choi blocks of the full quantum/classical instrument (small circuits)."""
    circuit = compact(circuit, active)
    dimension = 2**circuit.num_qubits
    branches = [(0, np.exp(1j*float(circuit.global_phase))*np.eye(dimension, dtype=complex))]
    indices = np.arange(dimension)
    for item in circuit.data:
        qubits = [circuit.find_bit(q).index for q in item.qubits]
        if item.operation.name != "measure":
            branches = [(bits, Operator(matrix).compose(Operator(item.operation), qargs=qubits).data)
                        for bits, matrix in branches]
            continue
        bit = circuit.find_bit(item.clbits[0]).index
        following = []
        for bits, matrix in branches:
            for value in (0, 1):
                projected = matrix.copy()
                projected[((indices >> qubits[0]) & 1) != value, :] = 0
                if np.linalg.norm(projected) > 1e-14:
                    following.append(((bits & ~(1 << bit)) | (value << bit), projected))
        branches = following
    blocks = {}
    for bits, matrix in branches:
        vector = matrix.reshape(-1, order="F")
        block = np.outer(vector, vector.conj())
        blocks[bits] = blocks.get(bits, 0) + block
    return blocks


def equivalence(before, after, max_qubits=8):
    active = {c.find_bit(q).index for c in (before, after) for item in c.data
              if item.operation.name != "barrier" for q in item.qubits}
    if len(active) > max_qubits:
        return {"status": "not_checked", "reason": "oracle_width_limit", "active_qubits": len(active)}
    # Entrywise equality is sensitive to global phase; Operator.equiv is not.
    error = float(np.max(np.abs(Operator(compact(before, active, unitary=True)).data -
                               Operator(compact(after, active, unitary=True)).data)))
    distance = tvd(ideal_distribution(before), ideal_distribution(after))
    channel_error = None
    if len(active) <= 4:
        left, right = instrument(before, active), instrument(after, active)
        channel_error = max((float(np.max(np.abs(left.get(k, 0) - right.get(k, 0))))
                             for k in left.keys() | right.keys()), default=0.0)
    metadata = (before.num_qubits == after.num_qubits and before.num_clbits == after.num_clbits
                and before.metadata == after.metadata and before.layout == after.layout
                and before.qregs == after.qregs and before.cregs == after.cregs
                and before.global_phase == after.global_phase and before.name == after.name)
    return {"status": "passed" if error < 1e-10 and distance < 1e-10 and metadata
            and (channel_error is None or channel_error < 1e-10) else "failed",
            "phase_sensitive_max_error": error, "ideal_tvd": distance,
            "full_instrument_max_error": channel_error, "instrument_qubit_limit": 4,
            "metadata_preserved": metadata, "active_qubits": len(active),
            "oracle_scope": "phase-sensitive unitary, zero-input distribution; full quantum/classical instrument up to 4 active qubits"}


def metrics(circuit, target):
    """ASAP estimate uses snapshot gate durations; no timing circuit is emitted."""
    available = [0.0] * circuit.num_qubits
    missing = False
    for item in circuit.data:
        qargs = tuple(circuit.find_bit(q).index for q in item.qubits)
        start = max((available[q] for q in qargs), default=0.0)
        if item.operation.name == "barrier":
            for q in qargs:
                available[q] = start
            continue
        prop = target[item.operation.name].get(qargs)
        if prop is None or prop.duration is None:
            missing = True
            continue
        for q in qargs:
            available[q] = start + prop.duration
    return {"instructions": len(circuit.data), "depth": circuit.depth(),
            "gate_counts": dict(circuit.count_ops()),
            "estimated_asap_seconds": None if missing else max(available, default=0),
            "duration_kind": "snapshot estimate; excludes alignment, classical latency and service transforms"}


def paired_bootstrap(values, seed=0, samples=2000):
    """Resample independent paired units, never individual shots."""
    values = np.asarray(values, dtype=float)
    if not len(values):
        return {"n": 0, "mean": None, "ci95": None}
    result = {"n": len(values), "mean": float(values.mean()), "ci95": None}
    if len(values) >= 2:
        rng = np.random.default_rng(seed)
        boot = rng.choice(values, (samples, len(values)), replace=True).mean(axis=1)
        result["ci95"] = np.quantile(boot, [0.025, 0.975]).tolist()
    return result
