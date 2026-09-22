"""Invoke the existing compiler through stdin/stdout, without a shell."""

from __future__ import annotations

from dataclasses import replace
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Literal

from .errors import InputError, NativeCompilerError
from .model import ImportedCircuit, NativeResult


def _diagnostics(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


class NativeCompiler:
    def __init__(
        self,
        executable: str | os.PathLike | None = None,
        *,
        timeout: float = 30.0,
    ):
        """Resolve an explicit executable, MLIRQ_OPT, or mlirq-opt on PATH."""
        try:
            valid_timeout = (
                not isinstance(timeout, bool) and isinstance(timeout, (int, float))
                and timeout > 0 and math.isfinite(timeout)
            )
        except OverflowError:
            valid_timeout = False
        if not valid_timeout:
            raise InputError("invalid_timeout", "timeout must be a finite positive number of seconds")
        try:
            candidate = os.fspath(executable) if executable is not None else os.environ.get("MLIRQ_OPT", "mlirq-opt")
        except TypeError as exc:
            raise InputError("invalid_executable", "executable must be a file path or command name") from exc
        if not isinstance(candidate, str) or not candidate or "\0" in candidate:
            raise InputError("invalid_executable", "executable must be a nonempty string path or command name")
        resolved = shutil.which(candidate)
        if resolved is None:
            raise NativeCompilerError(
                "native_not_found",
                f"Cannot execute {candidate!r}; build mlirq-opt and pass its path or set MLIRQ_OPT",
            )
        self.executable = str(Path(resolved).resolve())
        self.timeout = float(timeout)

    def run(
        self,
        module: ImportedCircuit,
        *,
        mode: Literal["verify", "scan", "mitigate"] = "verify",
    ) -> NativeResult:
        """Return verified native IR with its preserved import context.

        The catalog is snapshotted in a private working directory, using a
        fixed relative filename so paths with spaces cannot alter pass options.
        This is a native bridge, not the later Qiskit-to-Qiskit optimizer API.
        """
        if not isinstance(module, ImportedCircuit):
            raise InputError("invalid_module", "module must be returned by import_compiled_circuit")
        if not isinstance(mode, str) or mode not in {"verify", "scan", "mitigate"}:
            raise InputError("invalid_mode", "mode must be verify, scan, or mitigate")
        args = [self.executable, "--verify-each", "--mlir-print-op-generic"]
        if mode != "verify":
            args.append(f"--mlirq-qrisk-{mode}=patterns-file=patterns.json")
        args.append("--mlirq-verify-target")
        try:
            with tempfile.TemporaryDirectory(prefix="mlirq-") as work:
                Path(work, "patterns.json").write_text(module.catalog_json, encoding="utf-8")
                result = subprocess.run(
                    args, input=module.mlir, text=True, encoding="utf-8",
                    capture_output=True, cwd=work, timeout=self.timeout,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise NativeCompilerError(
                "native_timeout", f"mlirq-opt exceeded {self.timeout:g} seconds",
                diagnostics=_diagnostics(exc.stderr),
            ) from exc
        except (OSError, UnicodeError) as exc:
            raise NativeCompilerError("native_io_error", f"Cannot run mlirq-opt: {exc}") from exc
        if result.returncode:
            raise NativeCompilerError(
                "native_failure", "mlirq-opt rejected the module or pass inputs",
                returncode=result.returncode, diagnostics=result.stderr,
            )
        if not result.stdout.strip():
            raise NativeCompilerError("native_empty_output", "mlirq-opt returned no module")
        return NativeResult(
            module=replace(module, mlir=result.stdout),
            mode=mode, diagnostics=result.stderr,
        )
