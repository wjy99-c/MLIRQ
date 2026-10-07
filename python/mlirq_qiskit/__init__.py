"""Post-Qiskit circuit conversion and native compiler bridge (M1 tasks T1–T8)."""

from .errors import CatalogError, ExportError, InputError, MLIRQError, NativeCompilerError
from .exporter import export_qiskit_circuit
from .importer import import_compiled_circuit
from .model import ImportedCircuit, NativeResult
from .native import NativeCompiler

__all__ = [
    "CatalogError",
    "ExportError",
    "ImportedCircuit",
    "InputError",
    "MLIRQError",
    "NativeCompiler",
    "NativeCompilerError",
    "NativeResult",
    "import_compiled_circuit",
    "export_qiskit_circuit",
]

from .optimizer import OptimizationResult, optimize_compiled_circuit
from .options import MitigationOptions
from .validation import validate_target_instructions

__all__ += ["MitigationOptions", "OptimizationResult", "optimize_compiled_circuit", "validate_target_instructions"]
