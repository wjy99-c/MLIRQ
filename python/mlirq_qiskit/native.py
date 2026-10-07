"""Invoke the existing compiler through stdin/stdout, without a shell."""

from __future__ import annotations

from dataclasses import replace
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Literal

from .errors import InputError, NativeCompilerError
from .model import ImportedCircuit, NativeResult
from .options import MitigationOptions


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
        options: MitigationOptions | None = None,
    ) -> NativeResult:
        """Return verified native IR with its preserved import context.

        The catalog is snapshotted in a private working directory, using a
        fixed relative filename so paths with spaces cannot alter pass options.
        Use optimize_compiled_circuit for the complete Qiskit-to-Qiskit API.
        """
        return self._invoke(module, mode=mode, options=options)[0]

    def _export(self, module: ImportedCircuit) -> dict:
        return self._invoke(module, mode="verify", export=True)[1]

    def _invoke(self, module, *, mode, export=False, options=None):
        if not isinstance(module, ImportedCircuit):
            raise InputError("invalid_module", "module must be returned by import_compiled_circuit")
        if not isinstance(mode, str) or mode not in {"verify", "scan", "mitigate"}:
            raise InputError("invalid_mode", "mode must be verify, scan, or mitigate")
        if options is not None and (not isinstance(options, MitigationOptions) or mode != "mitigate"):
            raise InputError("invalid_options", "MitigationOptions require mitigate mode")
        args = [self.executable, "--verify-each", "--mlir-print-op-generic"]
        if mode != "verify":
            settings = "patterns-file=patterns.json report-file=report.json"
            if options is not None:
                settings += " " + options.native_options()
            args.append(f"--mlirq-qrisk-{mode}={settings}")
        args.append("--mlirq-verify-target")
        if export:
            args.append("--mlirq-export-qiskit=output-file=circuit.json")
        payload = None
        report = None
        try:
            with tempfile.TemporaryDirectory(prefix="mlirq-") as work:
                Path(work, "patterns.json").write_text(module.catalog_json, encoding="utf-8")
                result = subprocess.run(
                    args, input=module.mlir, text=True, encoding="utf-8",
                    capture_output=True, cwd=work, timeout=self.timeout,
                    check=False,
                )
                if result.returncode:
                    raise NativeCompilerError(
                        "native_failure", "mlirq-opt rejected the module or pass inputs",
                        returncode=result.returncode, diagnostics=result.stderr,
                    )
                if not result.stdout.strip():
                    raise NativeCompilerError("native_empty_output", "mlirq-opt returned no module")
                if mode != "verify":
                    try:
                        report = json.loads(Path(work, "report.json").read_text(encoding="utf-8"))
                        if (type(report.get("schema_version")) is not int or report["schema_version"] != 1
                                or report.get("mode") != mode or not isinstance(report.get("circuits"), list)
                                or len(report["circuits"]) != 1):
                            raise ValueError("invalid report envelope")
                        entry = report["circuits"][0]
                        if (not isinstance(entry, dict) or entry.get("backend") != module.backend_name
                                or not isinstance(entry.get("pattern_counts"), list)
                                or any(type(entry.get(key)) is not int or entry[key] < 0 for key in
                                       ("before_total", "after_total", "rewrites", "candidates", "legal_candidates", "scans"))
                                or not isinstance(entry.get("status"), str)
                                or not isinstance(entry.get("termination_reason"), str)):
                            raise ValueError("invalid circuit report")
                    except (OSError, UnicodeError, ValueError, AttributeError) as exc:
                        raise NativeCompilerError("native_invalid_report", "Missing or malformed native JSON report") from exc
                if export:
                    try:
                        payload = json.loads(Path(work, "circuit.json").read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, ValueError) as exc:
                        raise NativeCompilerError(
                            "native_invalid_export", "mlirq-opt did not produce valid Qiskit interchange JSON",
                            diagnostics=result.stderr,
                        ) from exc
        except subprocess.TimeoutExpired as exc:
            raise NativeCompilerError(
                "native_timeout", f"mlirq-opt exceeded {self.timeout:g} seconds",
                diagnostics=_diagnostics(exc.stderr),
            ) from exc
        except (OSError, UnicodeError) as exc:
            raise NativeCompilerError("native_io_error", f"Cannot run mlirq-opt: {exc}") from exc
        return NativeResult(
            module=replace(module, mlir=result.stdout),
            mode=mode, diagnostics=result.stderr, report=report,
        ), payload
