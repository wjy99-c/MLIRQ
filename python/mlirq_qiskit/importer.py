"""Translate already-physical Qiskit instructions into linear MLIRQ SSA."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct

import qiskit
from qiskit import QuantumCircuit
from qiskit.circuit import Barrier, Measure
from qiskit.circuit.library import CXGate, CZGate, HGate, RZGate, SXGate, XGate, ZGate
from qiskit.transpiler import Target

from .errors import CatalogError, InputError
from .model import ImportedCircuit

_Q = "!mlirq.qubit"
_PROTOTYPES = {
    "h": HGate(), "x": XGate(), "z": ZGate(), "sx": SXGate(),
    "rz": RZGate(0.0), "cx": CXGate(), "cz": CZGate(), "measure": Measure(),
    "barrier": Barrier(0),
}
# Qiskit uses singleton subclasses for many standard instructions. Mutable
# instances use their public base class. Do not trust arbitrary gates by name.
_TYPES = {
    name: (type(gate), gate.base_class) for name, gate in _PROTOTYPES.items()
}


def _catalog_json(patterns: Mapping | str | os.PathLike) -> str:
    try:
        if isinstance(patterns, Mapping):
            data = dict(patterns)
        elif isinstance(patterns, (str, os.PathLike)):
            data = json.loads(Path(patterns).read_text(encoding="utf-8"))
        else:
            raise CatalogError("invalid_catalog", "patterns must be a JSON mapping or file path")
        # Freeze the supplied data and reject non-JSON/nonfinite values.
        serialized = json.dumps(data, allow_nan=False, ensure_ascii=True, sort_keys=True)
        data = json.loads(serialized)
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        raise CatalogError("invalid_catalog", f"Cannot read QRisk catalog: {exc}") from exc
    if (
        not isinstance(data, dict)
        or type(data.get("schema_version")) is not int
        or data["schema_version"] != 1
        or not isinstance(data.get("source_url"), str)
        or not data["source_url"]
        or not isinstance(data.get("patterns"), list)
    ):
        raise CatalogError(
            "invalid_catalog",
            "Catalog requires schema_version=1, a nonempty source_url, and a patterns array",
        )
    # The native QRisk loader owns the detailed pattern schema and validates it
    # when scan/mitigate runs. This avoids maintaining a second pattern parser.
    return serialized


def _number(value, *, instruction_index=None, operation=None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise InputError(
            "invalid_parameter", "Expected a bound, finite real parameter",
            instruction_index=instruction_index, operation=operation,
        ) from exc
    if not math.isfinite(result):
        raise InputError(
            "invalid_parameter", "Expected a bound, finite real parameter",
            instruction_index=instruction_index, operation=operation,
        )
    return result


def _f64(value: float) -> str:
    # MLIR's hexadecimal float form is the IEEE-754 bit pattern, not float.hex().
    # This round-trips every accepted binary64 value, including signed zero.
    return "0x" + struct.pack(">d", value).hex().upper() + " : f64"


def import_compiled_circuit(
    circuit: QuantumCircuit,
    *,
    backend_name: str,
    target: Target,
    patterns: Mapping | str | os.PathLike,
) -> ImportedCircuit:
    """Import a static compiled circuit without transpiling or changing layout.

    Positions in circuit.qubits are physical indices. All wires are retained,
    including idle/auxiliary wires. Supported operations are h/x/z/sx/rz/cx/cz
    and one terminal measurement per wire, plus barriers (including after
    measurements). Instruction labels are preserved. Import itself requires
    no native executable; native processing and export require mlirq-opt.
    """
    if not isinstance(circuit, QuantumCircuit):
        raise InputError("invalid_circuit", "circuit must be a Qiskit QuantumCircuit")
    if not isinstance(target, Target):
        raise InputError("invalid_target", "target must be a Qiskit Target")
    if not isinstance(backend_name, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]*", backend_name
    ):
        raise InputError("invalid_backend", "Provide the exact backend identifier")
    capacity = target.num_qubits
    if type(capacity) is not int or not 0 < capacity < 2**63:
        raise InputError("invalid_target", "Target must have a finite positive qubit capacity")
    if circuit.num_qubits > capacity:
        raise InputError("target_capacity", "Circuit width exceeds target capacity")
    if circuit.parameters:
        raise InputError("unbound_parameters", "Bind all circuit parameters before importing")
    if circuit.num_vars or circuit.num_stretches:
        raise InputError("unsupported_classical_state", "Classical variables and stretches are unsupported")
    try:
        circuit.op_start_times
    except AttributeError:
        pass
    else:
        raise InputError("unsupported_schedule", "Timing-scheduled circuits require later scheduling support")

    phase = _number(circuit.global_phase)
    catalog = _catalog_json(patterns)
    try:
        source, target_copy = deepcopy(circuit), deepcopy(target)
    except Exception as exc:
        raise InputError("snapshot_failed", "Cannot make independent circuit/target snapshots") from exc

    wires = [f"%q{index}" for index in range(source.num_qubits)]
    operations = []
    measurements = []
    outputs = []
    retired = set()
    edges = set()
    for index, instruction in enumerate(source.data):
        operation = instruction.operation
        name = operation.name
        context = {"instruction_index": index, "operation": name}
        if name not in _TYPES or type(operation) not in _TYPES[name]:
            raise InputError(
                "unsupported_operation",
                f"Instruction {index} ({name}) is not a supported standard instruction",
                **context,
            )
        if getattr(operation, "condition", None) is not None:
            raise InputError("unsupported_annotation", "Conditional instructions are unsupported", **context)
        qubits = tuple(source.find_bit(bit).index for bit in instruction.qubits)
        clbits = tuple(source.find_bit(bit).index for bit in instruction.clbits)
        arity = len(qubits) if name == "barrier" else (2 if name in {"cx", "cz"} else 1)
        if (
            len(qubits) != arity or len(set(qubits)) != arity
            or len(clbits) != (1 if name == "measure" else 0)
            or len(operation.params) != (1 if name == "rz" else 0)
        ):
            raise InputError("invalid_instruction", "Invalid operand/parameter arity", **context)
        source_attr = f"mlirq.qiskit.source_index = {index} : i64"
        if name == "barrier":
            # Directives need no Target gate entry and do not revive a retired
            # quantum wire. Retain their ordered scope, including an empty one.
            scope = "array<i64: " + ", ".join(map(str, qubits)) + ">" if qubits else "array<i64>"
            operations.append(
                f'    "mlirq.barrier"() {{qubits = {scope}, {source_attr}}} : () -> ()'
            )
            continue
        if retired.intersection(qubits):
            raise InputError("use_after_measurement", "A measured physical wire cannot be used again", **context)
        params = tuple(_number(p, **context) for p in operation.params)
        try:
            prototype = target_copy.operation_from_name(name)
            supported = (
                type(prototype) in _TYPES[name]
                and target_copy.instruction_supported(
                    operation_name=name, qargs=qubits, parameters=list(params)
                )
            )
        except KeyError:
            supported = False
        if not supported:
            raise InputError(
                "unsupported_target_instruction",
                f"Target does not support {name}{qubits} with parameters {params}",
                **context,
            )

        inputs = ", ".join(wires[q] for q in qubits)
        if name == "measure":
            result = f"%m{index}"
            operations.append(
                f'    {result} = "mlirq.measure"({inputs}) '
                f'{{mlirq.qiskit.clbit = {clbits[0]} : i64, {source_attr}}} : ({_Q}) -> i1'
            )
            measurements.append((index, qubits[0], clbits[0]))
            outputs.append(result)
            retired.add(qubits[0])
        else:
            results = [f"%s{index}_{slot}" for slot in range(arity)]
            types = ", ".join([_Q] * arity)
            attrs = f" {{{source_attr}" + (f", angle = {_f64(params[0])}" if name == "rz" else "") + "}"
            operations.append(
                f'    {", ".join(results)} = "mlirq.{name}"({inputs}){attrs} '
                f': ({types}) -> ({types})'
            )
            for qubit, result in zip(qubits, results):
                wires[qubit] = result
            if arity == 2:
                edges.add(qubits)

    # The current native topology is a projection of input-validated edges,
    # not a replacement for the full per-instruction Qiskit Target snapshot.
    flat_edges = ", ".join(str(q) for edge in sorted(edges) for q in edge)
    coupling = f"array<i64: {flat_edges}>" if edges else "array<i64>"
    lines = [
        "module {",
        "  mlirq.circuit @qiskit_circuit attributes {",
        '    mlirq.stage = "architecture",',
        f'    mlirq.target = {{name = "{backend_name}", num_qubits = {capacity} : i64, '
        f"coupling = {coupling}, directed = true}},",
        "    mlirq.qiskit.import_version = 2 : i64,",
        f"    mlirq.qiskit.num_qubits = {source.num_qubits} : i64,",
        f"    mlirq.qiskit.num_clbits = {source.num_clbits} : i64,",
        f"    mlirq.qiskit.global_phase = {_f64(phase)}",
        "  } {",
    ]
    for qubit in range(source.num_qubits):
        lines.append(f'    %q{qubit} = "mlirq.alloc"() {{physical = {qubit} : i64}} : () -> {_Q}')
    lines.extend(operations)
    for qubit, value in enumerate(wires):
        if qubit not in retired:
            lines.append(f'    "mlirq.discard"({value}) : ({_Q}) -> ()')
    bit_types = ", ".join(["i1"] * len(outputs))
    lines.extend([
        f'    "mlirq.output"({", ".join(outputs)}) : ({bit_types}) -> ()',
        "  }",
        "}",
        "",
    ])
    return ImportedCircuit(
        mlir="\n".join(lines), backend_name=backend_name,
        num_qubits=source.num_qubits, num_clbits=source.num_clbits,
        global_phase=phase, measurements=tuple(measurements),
        catalog_json=catalog,
        catalog_sha256=hashlib.sha256(catalog.encode("utf-8")).hexdigest(),
        qiskit_version=qiskit.__version__, _source=source, _target=target_copy,
    )
