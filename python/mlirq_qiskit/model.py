"""An immutable IR bundle with isolated copies of its original context."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json

from qiskit import QuantumCircuit
from qiskit.transpiler import Target


@dataclass(frozen=True)
class ImportedCircuit:
    """Native MLIR plus the Qiskit context required for export.

    MLIR is not the complete Qiskit interchange by itself: the source snapshot
    retains register/layout metadata and the circuit's global phase. Accessors
    return independent copies. Native passes consume only the MLIR and catalog.
    """

    mlir: str
    backend_name: str
    num_qubits: int
    num_clbits: int
    global_phase: float
    # Triples: original instruction index, physical qubit, classical bit index.
    measurements: tuple[tuple[int, int, int], ...]
    catalog_json: str
    catalog_sha256: str
    qiskit_version: str
    _source: QuantumCircuit = field(repr=False, compare=False)
    _target: Target = field(repr=False, compare=False)
    schema_version: int = field(default=2, init=False)

    @property
    def physical_qubits(self) -> tuple[int, ...]:
        return tuple(range(self.num_qubits))

    @property
    def source_circuit(self) -> QuantumCircuit:
        return deepcopy(self._source)

    @property
    def target(self) -> Target:
        return deepcopy(self._target)

    @property
    def pattern_catalog(self) -> dict:
        return json.loads(self.catalog_json)


@dataclass(frozen=True)
class NativeResult:
    """Verified native output with export context; a Python report is a later task."""

    module: ImportedCircuit
    mode: str
    diagnostics: str

    @property
    def mlir(self) -> str:
        return self.module.mlir
