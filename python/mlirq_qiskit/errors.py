"""Stable error codes plus contextual diagnostics for API callers."""

from __future__ import annotations


class MLIRQError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        instruction_index: int | None = None,
        operation: str | None = None,
        diagnostics: str | None = None,
        returncode: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.instruction_index = instruction_index
        self.operation = operation
        self.diagnostics = diagnostics
        self.returncode = returncode

    def as_dict(self) -> dict:
        fields = {
            "code": self.code,
            "message": str(self),
            "instruction_index": self.instruction_index,
            "operation": self.operation,
            "diagnostics": self.diagnostics,
            "returncode": self.returncode,
        }
        return {key: value for key, value in fields.items() if value is not None}


class InputError(MLIRQError):
    """The supplied circuit or backend context cannot be imported."""


class CatalogError(MLIRQError):
    """The pattern catalog cannot be read or is not a supported JSON envelope."""


class NativeCompilerError(MLIRQError):
    """The native executable is unavailable, timed out, or rejected the module."""


class ExportError(MLIRQError):
    """Native output cannot be reconciled with the imported Qiskit context."""
