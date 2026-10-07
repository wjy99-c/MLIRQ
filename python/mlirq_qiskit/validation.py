"""Post-export instruction validation against the supplied Qiskit Target."""
import math
import hashlib
import json
from qiskit import QuantumCircuit
from qiskit.transpiler import Target
from .errors import ExportError
from .importer import _TYPES


def target_fingerprint(target):
    """Hash the supplied Target's operations, qargs, parameters and properties."""
    entries = []
    for name in sorted(target.operation_names):
        operation = target.operation_from_name(name)
        base = operation if isinstance(operation, type) else type(operation)
        entries.append({"name": name, "type": f"{base.__module__}.{base.__qualname__}",
                        "parameters": [str(p) for p in getattr(operation, "params", [])]
                        if not isinstance(operation, type) else [],
                        "qargs": [{"qubits": qargs, "duration": prop.duration if prop else None,
                                   "error": prop.error if prop else None}
                                  for qargs, prop in target[name].items()]})
    payload = {"num_qubits": target.num_qubits, "dt": target.dt, "operations": entries}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()


def validate_target_instructions(circuit: QuantumCircuit, target: Target) -> dict:
    """Check actual operations, ordered physical qargs and bound parameters.

    Barriers are directives. This is independent of native topology checks;
    it does not transpile or modify either argument.
    """
    if (not isinstance(circuit, QuantumCircuit) or not isinstance(target, Target)
            or type(target.num_qubits) is not int or target.num_qubits <= 0
            or circuit.num_qubits > target.num_qubits):
        raise ExportError("output_target_capacity", "Invalid circuit or target capacity")
    if circuit.parameters or circuit.num_vars or circuit.num_stretches:
        raise ExportError("output_dynamic_state", "Output must be static with bound parameters")
    try:
        circuit.op_start_times
    except AttributeError:
        pass
    else:
        raise ExportError("output_schedule", "Output must precede timing scheduling")
    checked = directives = 0
    retired = set()
    for index, item in enumerate(circuit.data):
        op = item.operation
        context = dict(instruction_index=index, operation=op.name)
        qubits = tuple(circuit.find_bit(q).index for q in item.qubits)
        if (op.name not in _TYPES or type(op) not in _TYPES[op.name]
                or getattr(op, "condition", None) is not None):
            raise ExportError("output_unsupported_operation", "Unsupported output instruction", **context)
        if op.name == "barrier":
            directives += 1
            continue
        if retired.intersection(qubits):
            raise ExportError("output_use_after_measurement", "Measured wire reused", **context)
        try:
            params = [float(p) for p in op.params]
            supported = (all(math.isfinite(p) for p in params)
                         and type(target.operation_from_name(op.name)) in _TYPES[op.name]
                         and target.instruction_supported(operation_name=op.name,
                                                          qargs=qubits, parameters=params))
        except (KeyError, TypeError, ValueError, OverflowError):
            supported = False
        if not supported:
            raise ExportError("output_target_instruction", "Target rejects output instruction", **context)
        if op.name == "measure":
            retired.update(qubits)
        checked += 1
    return {"valid": True, "instructions_checked": checked, "barrier_directives": directives}
