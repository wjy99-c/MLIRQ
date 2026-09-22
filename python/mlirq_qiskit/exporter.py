"""Reconstruct Qiskit circuits from the native, verified interchange format."""

from __future__ import annotations

import struct

from qiskit import QuantumCircuit

from .errors import ExportError, InputError
from .model import ImportedCircuit, NativeResult
from .native import NativeCompiler


def _bits(value) -> str:
    return struct.pack(">d", float(value)).hex()


def _reconstruct(module: ImportedCircuit, payload: dict) -> QuantumCircuit:
    source = module.source_circuit
    header = {
        "schema_version": 2,
        "backend": module.backend_name,
        "num_qubits": source.num_qubits,
        "num_clbits": source.num_clbits,
        "target_num_qubits": module.target.num_qubits,
        "global_phase_bits": _bits(source.global_phase),
    }
    if (
        module.schema_version != 2
        or module.num_qubits != source.num_qubits
        or module.num_clbits != source.num_clbits
        or _bits(module.global_phase) != _bits(source.global_phase)
        or not isinstance(payload, dict)
        or set(payload) != set(header) | {"instructions"}
        or any(type(payload[key]) is not type(value) or payload[key] != value
               for key, value in header.items())
        or not isinstance(payload["instructions"], list)
    ):
        raise ExportError("export_context_mismatch", "Native export metadata differs from the import context")

    # Rewrites currently only reorder existing gates. Requiring a complete
    # permutation of original IDs also prevents silent instruction/label loss.
    original = list(source.data)
    instructions = payload["instructions"]
    if len(instructions) != len(original):
        raise ExportError("export_instruction_mismatch", "Native output added or removed instructions")
    epochs, epoch = [], 0
    for item in original:
        epochs.append(epoch)
        if item.operation.name in {"barrier", "measure"}:
            epoch += 1
    seen, order, epoch = set(), [], 0
    fields = {"source_index", "name", "qubits", "clbits", "parameter_bits"}
    for item in instructions:
        if not isinstance(item, dict) or set(item) != fields:
            raise ExportError("invalid_export", "Malformed instruction in native export")
        index = item["source_index"]
        if type(index) is not int or not 0 <= index < len(original) or index in seen:
            raise ExportError("export_instruction_mismatch", "Missing, duplicate, or invalid source instruction ID")
        seen.add(index)
        instruction = original[index]
        expected = {
            "source_index": index,
            "name": instruction.operation.name,
            "qubits": [source.find_bit(q).index for q in instruction.qubits],
            "clbits": [source.find_bit(c).index for c in instruction.clbits],
            "parameter_bits": [_bits(p) for p in instruction.operation.params],
        }
        if (
            item != expected
            or any(type(item[key]) is not list for key in ("qubits", "clbits", "parameter_bits"))
            or any(type(q) is not int for key in ("qubits", "clbits") for q in item[key])
        ):
            raise ExportError(
                "export_instruction_mismatch",
                "Native output changed an instruction's operation, physical operands, or parameters",
                instruction_index=index, operation=instruction.operation.name,
            )
        if epochs[index] != epoch:
            raise ExportError("export_fence_violation", "Native output crossed a barrier or measurement fence")
        if instruction.operation.name in {"barrier", "measure"}:
            epoch += 1
        order.append(index)

    # source is an isolated deep copy. copy_empty_like retains layout, register
    # membership/bit ordering, metadata, name, and global phase. Appending uses
    # already-physical positions; layout permutations are never applied again.
    output = source.copy_empty_like()
    for index in order:
        item = original[index]
        output.append(
            item.operation,
            [output.qubits[source.find_bit(q).index] for q in item.qubits],
            [output.clbits[source.find_bit(c).index] for c in item.clbits],
        )
    return output


def export_qiskit_circuit(
    module: ImportedCircuit | NativeResult,
    *,
    compiler: NativeCompiler | None = None,
) -> QuantumCircuit:
    """Export a new circuit, preserving imported context and native gate order.

    Runs native IR/topology verification and the structured export pass. Only
    the current reorder-only transformation contract is supported. This does
    not transpile, run mitigation, or provide the later T5/T6 validation/report API.
    """
    if isinstance(module, NativeResult):
        module = module.module
    if not isinstance(module, ImportedCircuit):
        raise InputError("invalid_module", "Export requires an ImportedCircuit or NativeResult")
    if compiler is None:
        compiler = NativeCompiler()
    if not isinstance(compiler, NativeCompiler):
        raise InputError("invalid_compiler", "compiler must be a NativeCompiler")
    return _reconstruct(module, compiler._export(module))
