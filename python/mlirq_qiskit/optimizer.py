"""Complete post-Qiskit optimization with an independent final rescan."""
from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
import time
from qiskit import QuantumCircuit
from .errors import ExportError, InputError
from .exporter import export_qiskit_circuit
from .importer import import_compiled_circuit
from .matching import count_patterns
from .native import NativeCompiler
from .options import MitigationOptions
from .validation import target_fingerprint, validate_target_instructions


@dataclass(frozen=True)
class OptimizationResult:
    circuit: QuantumCircuit
    report: dict


def optimize_compiled_circuit(circuit, *, backend_name, target, patterns,
                              compiler=None, options=None) -> OptimizationResult:
    """Return a new physical circuit and a JSON-serializable report.

    Fail closed on invalid input, compiler failure, invalid export, unsupported
    output instructions, or disagreement with the independent final rescan.
    The default acceptance rule never increases any active pattern count.
    Experimental options explicitly relax this rule or change matching.
    """
    compiler = NativeCompiler() if compiler is None else compiler
    options = MitigationOptions() if options is None else options
    if not isinstance(compiler, NativeCompiler) or not isinstance(options, MitigationOptions):
        raise InputError("invalid_options", "Expected NativeCompiler and MitigationOptions instances")
    start = time.perf_counter()
    module = import_compiled_circuit(circuit, backend_name=backend_name, target=target, patterns=patterns)
    imported = time.perf_counter()
    native = compiler.run(module, mode="mitigate", options=options)
    optimized = time.perf_counter()
    output = export_qiskit_circuit(native, compiler=compiler)
    exported = time.perf_counter()
    validation = validate_target_instructions(output, module.target)
    catalog = module.pattern_catalog
    before = count_patterns(module.source_circuit, catalog, backend_name, matching=options.matching)
    after = count_patterns(output, catalog, backend_name, matching=options.matching)
    entry = native.report["circuits"][0]
    try:
        native_before = {p["id"]: p["before"] for p in entry["pattern_counts"]}
        native_after = {p["id"]: p["after"] for p in entry["pattern_counts"]}
        consistent = (before == native_before and after == native_after
                      and sum(before.values()) == entry["before_total"]
                      and sum(after.values()) == entry["after_total"])
    except (KeyError, TypeError):
        consistent = False
    if not consistent:
        raise ExportError("report_count_mismatch", "Native report disagrees with the exported-circuit oracle")
    if (options.acceptance != "local" and sum(after.values()) > sum(before.values()) or
            options.acceptance == "componentwise" and any(after[k] > before[k] for k in before)):
        raise ExportError("report_acceptance_violation", "Final counts violate the requested acceptance rule")
    final_scoped = count_patterns(output, catalog, backend_name)
    finished = time.perf_counter()
    report = {
        "schema_version": 1, "backend": backend_name,
        "catalog_sha256": module.catalog_sha256, "qiskit_version": module.qiskit_version,
        "interchange_schema_version": module.schema_version,
        "target_num_qubits": module.target.num_qubits,
        "target_operation_names": sorted(module.target.operation_names),
        "options": asdict(options), "native": native.report,
        "before": before, "after": after, "final_scoped_counts": final_scoped,
        "status": entry["status"], "termination_reason": entry["termination_reason"],
        "output_validation": validation, "final_rescan_agrees": True,
        "timings_seconds": {"import": imported-start, "native": optimized-imported,
                            "export": exported-optimized, "validation_and_rescan": finished-exported,
                            "total": finished-start},
    }
    # Provenance hashing is outside the measured optimization interval.
    report["native_binary_sha256"] = hashlib.sha256(Path(compiler.executable).read_bytes()).hexdigest()
    report["target_sha256"] = target_fingerprint(module.target)
    return OptimizationResult(output, report)
