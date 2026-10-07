"""Local, deterministic experiment orchestration and auditable raw results."""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import itertools
import json
import os
from pathlib import Path
import random
import signal
import subprocess
import sys
import time
import numpy as np
from mlirq_qiskit import (MLIRQError, NativeCompiler, MitigationOptions,
                         import_compiled_circuit, optimize_compiled_circuit,
                         validate_target_instructions)
from mlirq_qiskit.matching import count_patterns
from .common import (ROOT, derived_seed, digest, equivalence, ideal_distribution, load_circuit,
                     manifest, metrics, paired_bootstrap, probabilities, read_json,
                     save_circuit, snapshot_backend, tvd, write_json)
from .workloads import cases


def prepare(args):
    config = read_json(args.config)
    if config.get("schema_version") != 1:
        raise ValueError("Unsupported config schema")
    for key in ("seeds", "widths", "depths", "fixture_repetitions", "catalog_scales"):
        if not config[key] or any(type(v) is not int or v < (0 if key == "seeds" else 1) for v in config[key]):
            raise ValueError(f"Invalid {key}")
    for key in ("shots", "repeats", "max_oracle_qubits", "timeout_seconds", "random_attempts"):
        if config[key] <= 0:
            raise ValueError(f"Invalid {key}")
    if max(config["widths"]) > len(config["initial_layout"]):
        raise ValueError("Initial layout is shorter than the largest workload")
    if args.shots is not None:
        if args.shots <= 0:
            raise ValueError("shots must be positive")
        config["shots"] = args.shots
    from qiskit_ibm_runtime import fake_provider
    if config["backend_class"] not in {"FakeFez", "FakeMarrakesh", "FakeKingston"}:
        raise ValueError("Use a supported named fake backend; hardware has a separate command")
    backend = getattr(fake_provider, config["backend_class"])()
    expected_name = "ibm_" + config["backend_class"][4:].lower()
    if config["backend_name"] != expected_name:
        raise ValueError("Fake backend and exact catalog backend name disagree")
    catalog = read_json(args.catalog)
    compiler = NativeCompiler(args.mlirq_opt, timeout=config["timeout_seconds"])
    output = Path(args.output or ROOT / "results" / f"{args.rq}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}").resolve()
    output.mkdir(parents=True, exist_ok=False)
    record = manifest(config, catalog, backend, compiler, args.rq)
    record["invocation"] = sys.argv
    record["input_directory"] = str(Path(args.input_dir).resolve()) if args.input_dir else None
    record["limit"] = args.limit
    write_json(output / "manifest.json", record)
    write_json(output / "config.json", config)
    write_json(output / "catalog.json", catalog)
    write_json(output / "backend-snapshot.json", snapshot_backend(backend))
    freeze = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True, check=True)
    (output / "requirements.lock.txt").write_text(freeze.stdout)
    return config, catalog, backend, compiler, output


def write_row(output, row):
    with (output / "records.jsonl").open("a") as stream:
        stream.write(json.dumps(row, sort_keys=True, allow_nan=False, default=str) + "\n")


def summary(output, rows, rq):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["group"], row["method"])].append(row)
    aggregated = []
    for (group, method), values in sorted(groups.items()):
        good = [r for r in values if r["status"] == "ok"]
        matched = [r for r in good if r["before_total"] > 0]
        times = [r["wall_seconds"] for r in good if "wall_seconds" in r]
        aggregated.append({"group": group, "method": method, "attempted_rows": len(values),
                           "unique_circuits": len({r["case"] for r in values}),
                           "status_counts": dict(Counter(r["status"] for r in values)),
                           "eligible_rows": len(good), "initial_match_rows": len(matched),
                           "before_total": sum(r["before_total"] for r in good),
                           "after_total": sum(r["after_total"] for r in good),
                           "outcomes": dict(Counter(r.get("outcome", "unknown") for r in good)),
                           "median_seconds": float(np.median(times)) if times else None,
                           "p90_seconds": float(np.quantile(times, .9)) if times else None})
    result = {"schema_version": 1, "rq": rq, "groups": aggregated,
              "rows": len(rows), "failures": sum(r["status"] in {"failed", "timeout"} for r in rows),
              "interpretation": "Smoke/pilot evidence, not a completed paper evaluation. Fixtures and scaling copies are synthetic."}
    if rq in {"rq2", "rq3"}:
        common_results = []
        for group in sorted({r["group"] for r in rows}):
            subset = [r for r in rows if r["group"] == group and r["method"] != "preflight"]
            methods = sorted({r["method"] for r in subset})
            eligible = [{(r["case"], r.get("repeat", 0)) for r in subset
                         if r["method"] == method and r["status"] == "ok"} for method in methods]
            common = set.intersection(*eligible) if eligible else set()
            for method in methods:
                selected = [r for r in subset if r["method"] == method
                            and (r["case"], r.get("repeat", 0)) in common]
                common_results.append({"group": group, "method": method, "paired_rows": len(selected),
                                       "unique_circuits": len({r["case"] for r in selected}),
                                       "before_total": sum(r["before_total"] for r in selected),
                                       "after_total": sum(r["after_total"] for r in selected)})
        result["common_eligible_subset"] = common_results
    if rq == "rq4":
        # Aggregate repetitions within each circuit before bootstrapping circuits.
        pairs = defaultdict(list)
        for row in rows:
            if row["status"] == "ok":
                pairs[(row["group"], row["case"])].append(row["tvd_improvement"])
        effects = defaultdict(list)
        for (group, _), values in pairs.items():
            effects[group].append(float(np.mean(values)))
        result["paired_tvd_improvement"] = {group: paired_bootstrap(values) for group, values in effects.items()}
        result["uncertainty_unit"] = "circuit mean across local simulator repeats; no calibration-window inference"
    result["eligible_rows"] = sum(r["status"] == "ok" for r in rows)
    write_json(output / "summary.json", result)
    columns = ["case", "group", "method", "repeat", "status", "outcome", "before_total", "after_total",
               "wall_seconds", "tvd_before", "tvd_after", "tvd_improvement", "reason"]
    with (output / "summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return result


def outcome(before, after, active):
    if not active:
        return "no_backend_patterns"
    if not before:
        return "no_matches"
    if not after:
        return "eliminated"
    if after < before:
        return "partial"
    return "blocked" if after == before else "increased"


def bounded_process(command, timeout, cwd):
    """Terminate the worker AND native descendants if its wall budget expires."""
    process = subprocess.Popen(command, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, cwd=cwd, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def run_worker(case, method, repeat, config, compiler, directory):
    directory.mkdir(parents=True)
    input_path = directory.parent / "input.qpy"
    command = [sys.executable, "-m", "mlirq_qiskit.experiments.baselines",
               "--input", str(input_path), "--output", str(directory / "output.qpy"),
               "--report", str(directory / "worker.json"), "--catalog", str(directory.parent / "catalog.json"),
               "--config", str(directory.parents[2] / "config.json"), "--compiler", compiler.executable,
               "--method", method, "--seed", str(derived_seed(case.seed, case.name, repeat, "baseline"))]
    # Independent process also bounds the unmodified upstream QRisk loop.
    try:
        completed = bounded_process(command, config["timeout_seconds"], ROOT)
    except subprocess.TimeoutExpired as error:
        (directory / "stderr.log").write_text(str(error))
        return None, {"status": "timeout", "reason": "worker_wall_time_limit"}
    (directory / "stderr.log").write_text(completed.stderr)
    if completed.returncode:
        return None, {"status": "failed", "reason": f"worker_exit_{completed.returncode}",
                      "diagnostic_file": str((directory / "stderr.log").relative_to(directory.parents[2]))}
    report = read_json(directory / "worker.json")
    return load_circuit(directory / "output.qpy"), {"status": "ok", **report}


def restrictions(circuit):
    terminal = False
    for item in circuit.data:
        if item.operation.name == "measure":
            terminal = True
        elif item.operation.name == "barrier" or terminal:
            return "QRisk baseline supports only unitary body followed by terminal measurements"
    return None


def evaluate(case, candidate, backend, config):
    validation = validate_target_instructions(candidate, backend.target)
    proof = equivalence(case.circuit, candidate, config["max_oracle_qubits"])
    before = count_patterns(case.circuit, case.catalog, config["backend_name"])
    after = count_patterns(candidate, case.catalog, config["backend_name"])
    b, a = sum(before.values()), sum(after.values())
    return {"status": "failed" if proof["status"] == "failed" else "ok",
            "before": before, "after": after, "before_total": b, "after_total": a,
            "outcome": outcome(b, a, bool(before)), "validation": validation, "equivalence": proof,
            "metrics_before": metrics(case.circuit, backend.target),
            "metrics_after": metrics(candidate, backend.target)}


def rejection_cases(backend, catalog, compiler, config):
    from qiskit import QuantumCircuit
    from qiskit.circuit import Parameter
    bad = []
    c = QuantumCircuit(1); c.reset(0); bad.append(("reset", c))
    c = QuantumCircuit(1); c.delay(12, 0); bad.append(("timing", c))
    c = QuantumCircuit(1); c.rz(Parameter("theta"), 0); bad.append(("unbound", c))
    c = QuantumCircuit(1, 1); c.measure(0, 0); c.x(0); bad.append(("reuse", c))
    c = QuantumCircuit(1, 1)
    with c.if_test((c.clbits[0], 1)):
        c.x(0)
    bad.append(("dynamic", c))
    c = QuantumCircuit(backend.num_qubits); c.cz(0, backend.num_qubits-1); bad.append(("illegal_edge", c))
    result = []
    for name, circuit in bad:
        try:
            optimize_compiled_circuit(circuit, backend_name=config["backend_name"], target=backend.target,
                                      patterns=catalog, compiler=compiler)
            result.append({"case": name, "rejected": False})
        except MLIRQError as error:
            result.append({"case": name, "rejected": True, "error": error.as_dict()})
    return result


def run(args):
    config, catalog, backend, compiler, output = prepare(args)
    rows = []
    generated = cases(config, catalog, backend, input_dir=args.input_dir, stress=args.rq == "rq3")
    if args.limit is not None:
        if args.limit <= 0:
            raise ValueError("limit must be positive")
        generated = itertools.islice(generated, args.limit)
    for index, case in enumerate(generated):
        print(f"{args.rq}: {index+1} {case.name}", flush=True)
        folder = output / "circuits" / f"{index:04d}-{case.name}"
        folder.mkdir(parents=True)
        sha = save_circuit(folder / "input.qpy", case.circuit)
        write_json(folder / "catalog.json", case.catalog)
        base = {"case": case.name, "group": case.group, "seed": case.seed, "input_sha256": sha,
                "catalog_sha256": digest(case.catalog), "input_file": str((folder / "input.qpy").relative_to(output))}
        try:
            # Native scan validates the full catalog even for non-MLIRQ baselines.
            imported = import_compiled_circuit(case.circuit, backend_name=config["backend_name"],
                                              target=backend.target, patterns=case.catalog)
            compiler.run(imported, mode="scan")
        except MLIRQError as error:
            row = {**base, "method": "preflight", "status": "ineligible", "reason": error.as_dict()}
            rows.append(row); write_row(output, row)
            continue
        if args.rq == "rq1":
            result = optimize_compiled_circuit(case.circuit, backend_name=config["backend_name"], target=backend.target,
                                              patterns=case.catalog, compiler=compiler)
            row = {**base, "method": "mlirq", **evaluate(case, result.circuit, backend, config),
                   "wall_seconds": result.report["timings_seconds"]["total"], "detail": result.report}
            if case.circuit != imported.source_circuit:
                row["status"] = "failed"; row["reason"] = "input mutated"
            save_circuit(folder / "output.qpy", result.circuit)
            rows.append(row); write_row(output, row)
        elif args.rq in {"rq2", "rq3"}:
            methods = ["qiskit", "qrisk", "random", "mlirq"]
            if args.rq == "rq3":
                methods += ["global", "total", "local", "diagonal"]
            for repeat in range(config["repeats"] if args.rq == "rq3" else 1):
                order = methods.copy()
                random.Random(derived_seed(case.seed, case.name, repeat, "method-order")).shuffle(order)
                for method in order:
                    row = {**base, "method": method, "repeat": repeat}
                    reason = restrictions(case.circuit) if method == "qrisk" else None
                    if reason:
                        row.update(status="ineligible", reason=reason)
                    else:
                        candidate, report = run_worker(case, method, repeat, config, compiler,
                                                       folder / f"{method}-{repeat}")
                        row.update(report)
                        if candidate is not None:
                            row.update(evaluate(case, candidate, backend, config))
                            if args.rq == "rq3" and method == "mlirq":
                                try:
                                    from qiskit.transpiler import PassManager
                                    from qiskit.transpiler.passes import ALAPScheduleAnalysis, PadDelay
                                    scheduled = PassManager([ALAPScheduleAnalysis(target=backend.target),
                                                             PadDelay(target=backend.target)]).run(candidate)
                                    row["scheduled_trace_counts"] = count_patterns(scheduled, case.catalog, config["backend_name"])
                                    save_circuit(folder / f"scheduled-{repeat}.qpy", scheduled)
                                except Exception as error:
                                    row["scheduling"] = {"status": "unavailable", "reason": str(error)}
                    rows.append(row); write_row(output, row)
        else:
            run_simulator_pairs(case, base, folder, rows, output, backend, compiler, config)
    if args.rq == "rq1":
        rejected = rejection_cases(backend, catalog, compiler, config)
        write_json(output / "rejections.json", rejected)
        test = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "test/python", "-v"],
                              cwd=ROOT, capture_output=True, text=True, env={**__import__("os").environ,
                              "MLIRQ_OPT": compiler.executable}, timeout=180)
        (output / "regression-tests.log").write_text(test.stdout + test.stderr)
        write_json(output / "regression-tests.json", {"passed": test.returncode == 0,
                                                     "all_invalid_inputs_rejected": all(r["rejected"] for r in rejected)})
        if test.returncode or not all(r["rejected"] for r in rejected):
            rows.append({"case": "regression_or_rejection", "group": "regression", "method": "mlirq", "status": "failed"})
    result = summary(output, rows, args.rq)
    print(json.dumps({"output": str(output), "rows": len(rows), "failures": result["failures"]}), flush=True)
    return 1 if result["failures"] or not result["eligible_rows"] else 0


def run_simulator_pairs(case, base, folder, rows, output, backend, compiler, config):
    from qiskit_aer import AerSimulator
    from qiskit_ibm_runtime import SamplerV2
    result = optimize_compiled_circuit(case.circuit, backend_name=config["backend_name"], target=backend.target,
                                      patterns=case.catalog, compiler=compiler)
    checks = evaluate(case, result.circuit, backend, config)
    if checks["equivalence"]["status"] != "passed":
        row = {**base, "method": "mlirq", "status": "ineligible", "reason": "exact oracle required before simulator run"}
        rows.append(row); write_row(output, row)
        return
    ideal = ideal_distribution(case.circuit)
    write_json(folder / "ideal.json", ideal)
    write_json(folder / "optimization.json", result.report)
    save_circuit(folder / "output.qpy", result.circuit)
    aer = AerSimulator.from_backend(backend, method="density_matrix", max_parallel_threads=1)
    for repeat in range(config["repeats"]):
        order = ["qiskit", "mlirq"]
        seed = derived_seed(case.seed, case.name, repeat, "simulator")
        order_seed = derived_seed(case.seed, case.name, repeat, "pub-order")
        random.Random(order_seed).shuffle(order)
        sampler = SamplerV2(mode=aer, options={"simulator": {"seed_simulator": seed}})
        start = time.perf_counter()
        job = sampler.run([case.circuit if name == "qiskit" else result.circuit for name in order],
                          shots=config["shots"])
        pubs = job.result()
        counts = {name: pub.join_data().get_counts() for name, pub in zip(order, pubs)}
        before, after = (tvd(probabilities(counts[name]), ideal) for name in ("qiskit", "mlirq"))
        row = {**base, **checks, "method": "mlirq", "repeat": repeat, "shots": config["shots"],
               "submission_order": order, "order_seed": order_seed, "seed_simulator": seed, "counts": counts,
               "tvd_before": before, "tvd_after": after, "tvd_improvement": before-after,
               "wall_seconds": time.perf_counter()-start, "execution": "local_ibm_runtime_aer",
               "simulation_method": "density_matrix", "job_metadata": [p.metadata for p in pubs]}
        rows.append(row); write_row(output, row)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rq", choices=["rq1", "rq2", "rq3", "rq4"])
    parser.add_argument("--config", default=str(ROOT / "experiments/configs/smoke.json"))
    parser.add_argument("--catalog", default=str(ROOT / "patterns/qrisk-imported.json"))
    parser.add_argument("--output")
    parser.add_argument("--mlirq-opt", default=str(ROOT / "build/bin/mlirq-opt"))
    parser.add_argument("--input-dir", help="One already physical, unscheduled compiled circuit per QPY file")
    parser.add_argument("--limit", type=int, help="Pilot only: first N cases, recorded in manifest")
    parser.add_argument("--shots", type=int)
    return run(parser.parse_args())
