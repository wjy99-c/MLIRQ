"""Explicit, resumable IBM hardware workflow. No cloud jobs in local RQ runs."""
import argparse
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import uuid
import numpy as np
from qiskit import qpy
from mlirq_qiskit import NativeCompiler, optimize_compiled_circuit, validate_target_instructions
from .common import (ROOT, derived_seed, digest, equivalence, ideal_distribution, load_circuit,
                     manifest, paired_bootstrap, probabilities, read_json, save_circuit,
                     snapshot_backend, tvd, write_json)
from .workloads import cases


@contextmanager
def plan_lock(output):
    lock = output / "submission.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        yield
    finally:
        lock.unlink()


def service(args):
    from qiskit_ibm_runtime import QiskitRuntimeService
    # Uses the user's saved account. Never reads or prints its token.
    return QiskitRuntimeService(name=args.account_name) if args.account_name else QiskitRuntimeService()


def prepare(args):
    config = read_json(args.config)
    config["backend_name"] = args.backend
    if args.offline:
        from qiskit_ibm_runtime import fake_provider
        mapping = {"ibm_fez": "FakeFez", "ibm_marrakesh": "FakeMarrakesh", "ibm_kingston": "FakeKingston"}
        backend = getattr(fake_provider, mapping[args.backend])()
    else:
        backend = service(args).backend(args.backend, use_fractional_gates=False)
    catalog = read_json(args.catalog)
    compiler = NativeCompiler(args.mlirq_opt, timeout=config["timeout_seconds"])
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    run_id = f"mlirq-{uuid.uuid4().hex[:16]}"
    record = manifest(config, catalog, backend, compiler, "rq4-hardware")
    record.update(offline_prepare=args.offline, run_id=run_id, window_label=args.window,
                  primary_metric="paired TVD to exact ideal distribution; improvement = baseline - MLIRQ",
                  service_transforms="Runtime dynamical decoupling and gate/measurement twirling disabled; server artifacts may be unavailable")
    write_json(output / "manifest.json", record)
    write_json(output / "backend-snapshot.json", snapshot_backend(backend))
    write_json(output / "catalog.json", catalog)
    batches = []
    circuit_index = []
    for index, case in enumerate(cases(config, catalog, backend, input_dir=args.input_dir)):
        if case.group == "constructed_fixture" and not args.include_fixtures:
            continue
        folder = output / "circuits" / f"{index:04d}-{case.name}"
        folder.mkdir(parents=True)
        result = optimize_compiled_circuit(case.circuit, backend_name=args.backend, target=backend.target,
                                          patterns=case.catalog, compiler=compiler)
        checked = equivalence(case.circuit, result.circuit, config["max_oracle_qubits"])
        if checked["status"] != "passed":
            raise ValueError(f"Exact small-circuit oracle required before preparing {case.name}: {checked}")
        files = {}
        for name, circuit in (("qiskit", case.circuit), ("mlirq", result.circuit)):
            path = folder / f"{name}.qpy"
            files[name] = {"path": str(path.relative_to(output)), "sha256": save_circuit(path, circuit)}
        ideal_path = folder / "ideal.json"
        write_json(ideal_path, ideal_distribution(case.circuit))
        write_json(folder / "optimization.json", result.report)
        item = {"case": case.name, "group": case.group, "files": files,
                "ideal": str(ideal_path.relative_to(output)), "ideal_sha256": digest(ideal_path.read_bytes()),
                "before": result.report["before"], "after": result.report["after"]}
        circuit_index.append(item)
        for repeat in range(config["repeats"]):
            order = ["qiskit", "mlirq"]
            order_seed = derived_seed(case.seed, case.name, repeat, "pub-order")
            random.Random(order_seed).shuffle(order)
            batches.append({"batch": len(batches), "case": case.name, "repeat": repeat,
                            "order": order, "order_seed": order_seed, "state": "prepared", "job_id": None})
    if not batches:
        raise ValueError("No eligible hardware pairs were prepared")
    # Randomize order across circuits/repeats, with paired arms in each job.
    random.Random(config["seeds"][0]).shuffle(batches)
    plan = {"schema_version": 1, "run_id": run_id, "backend": args.backend,
            "offline_prepare": args.offline, "window_label": args.window,
            "shots_per_pub": config["shots"], "circuits": circuit_index, "batches": batches}
    write_json(output / "plan.json", plan)
    print(json.dumps({"prepared": str(output), "jobs": len(batches), "pubs": 2*len(batches),
                      "total_shots": 2*len(batches)*config["shots"], "hardware_submitted": False}))


def checked_circuit(output, item):
    path = output / item["path"]
    if digest(path.read_bytes()) != item["sha256"]:
        raise ValueError(f"Prepared circuit was modified: {path}")
    return load_circuit(path)


def submit(args):
    output = Path(args.output).resolve()
    plan = read_json(output / "plan.json")
    if not args.submit:
        print(json.dumps({"dry_run": True, "backend": plan["backend"],
                          "pending_jobs": sum(b["state"] == "prepared" for b in plan["batches"]),
                          "max_jobs_this_call": args.max_jobs,
                          "shots_per_pub": plan["shots_per_pub"], "pubs_per_job": 2}))
        return
    if plan["offline_prepare"]:
        raise ValueError("Offline plans cannot be submitted; prepare again against the real backend Target")
    if args.max_jobs <= 0:
        raise ValueError("max-jobs must be positive")
    with plan_lock(output):
        # Reload under the lock so two invocations cannot submit a stale plan.
        plan = read_json(output / "plan.json")
        from qiskit_ibm_runtime import SamplerV2
        backend = service(args).backend(plan["backend"], use_fractional_gates=False)
        backend.properties(refresh=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        write_json(output / f"backend-at-submit-{stamp}.json", snapshot_backend(backend))
        index = {c["case"]: c for c in plan["circuits"]}
        submitted = 0
        for batch in plan["batches"]:
            if submitted >= args.max_jobs:
                break
            if batch["state"] != "prepared":
                continue
            item = index[batch["case"]]
            circuits = [checked_circuit(output, item["files"][name]) for name in batch["order"]]
            for circuit in circuits:
                validate_target_instructions(circuit, backend.target)
            tag = f"batch-{batch['batch']}"
            sampler = SamplerV2(mode=backend, options={
                "dynamical_decoupling": {"enable": False},
                "twirling": {"enable_gates": False, "enable_measure": False},
                "environment": {"job_tags": [plan["run_id"], tag]},
            })
            # Persist BEFORE the network call. Interrupted/uncertain submissions
            # are never retried automatically (which could duplicate paid jobs).
            batch.update(state="submission_started", started_utc=datetime.now(timezone.utc).isoformat())
            write_json(output / "plan.json", plan)
            job = sampler.run(circuits, shots=plan["shots_per_pub"])
            batch.update(state="submitted", job_id=job.job_id())
            write_json(output / "plan.json", plan)
            record = read_json(output / "manifest.json")
            record["hardware_submitted"] = True
            write_json(output / "manifest.json", record)
            submitted += 1
            print(json.dumps({"batch": batch["batch"], "job_id": batch["job_id"]}), flush=True)


def _attach(args):
    """Recover an uncertain submission using an explicitly supplied job ID."""
    output = Path(args.output).resolve()
    plan = read_json(output / "plan.json")
    batch = next(b for b in plan["batches"] if b["batch"] == args.batch)
    if batch["state"] != "submission_started" or batch["job_id"] is not None:
        raise ValueError("Only an uncertain submission can be attached")
    job = service(args).job(args.job_id)
    if not {plan["run_id"], f"batch-{batch['batch']}"} <= set(job.tags):
        raise ValueError("Job tags do not match this prepared batch")
    if job.backend().name != plan["backend"]:
        raise ValueError("Job backend differs from the prepared backend")
    batch.update(state="submitted", job_id=args.job_id)
    write_json(output / "plan.json", plan)


def _collect(args):
    output = Path(args.output).resolve()
    plan = read_json(output / "plan.json")
    connection = service(args)
    index = {c["case"]: c for c in plan["circuits"]}
    for batch in plan["batches"]:
        if not batch["job_id"] or batch["state"] == "collected":
            continue
        job = connection.job(batch["job_id"])
        status = str(job.status())
        batch["remote_status"] = status
        if status != "DONE":
            write_json(output / "plan.json", plan)
            continue  # Rerun collect later; no resubmission.
        result = job.result()
        if len(result) != 2:
            raise ValueError("Expected two paired PUB results")
        item = index[batch["case"]]
        ideal_path = output / item["ideal"]
        if digest(ideal_path.read_bytes()) != item["ideal_sha256"]:
            raise ValueError("Prepared ideal distribution was modified")
        ideal = read_json(ideal_path)
        counts = {name: pub.join_data().get_counts() for name, pub in zip(batch["order"], result)}
        before, after = (tvd(probabilities(counts[name]), ideal) for name in ("qiskit", "mlirq"))
        record = {**batch, "group": item["group"], "window_label": plan["window_label"],
                  "counts": counts, "tvd_before": before, "tvd_after": after,
                  "tvd_improvement": before-after, "pub_metadata": [p.metadata for p in result],
                  "job_metadata": result.metadata, "job_metrics": job.metrics()}
        write_json(output / "jobs" / f"batch-{batch['batch']}.json", record)
        batch["state"] = "collected"
        write_json(output / "plan.json", plan)
    records = [read_json(path) for path in sorted((output / "jobs").glob("*.json"))]
    grouped = defaultdict(list)
    for row in records:
        grouped[(row["group"], row["case"])].append(row["tvd_improvement"])
    groups = defaultdict(list)
    for (group, _), values in grouped.items():
        groups[group].append(float(np.mean(values)))
    write_json(output / "hardware-summary.json", {
        "window_label": plan["window_label"], "collected_pairs": len(records),
        "planned_pairs": len(plan["batches"]),
        "states": {state: sum(b["state"] == state for b in plan["batches"])
                   for state in {b["state"] for b in plan["batches"]}},
        "paired_tvd_improvement": {group: paired_bootstrap(values) for group, values in groups.items()},
        "uncertainty_unit": "circuits within this window; analyze multiple windows separately",
        "primary_metric": "TVD to ideal; positive paired difference favors MLIRQ"})
    print(json.dumps({"collected_pairs": len(records), "planned_pairs": len(plan["batches"])}))


def attach(args):
    with plan_lock(Path(args.output).resolve()):
        _attach(args)


def collect(args):
    with plan_lock(Path(args.output).resolve()):
        _collect(args)


def summarize(args):
    """Hierarchical bootstrap across windows, then circuits within windows."""
    if not args.runs:
        raise ValueError("Supply --runs with the collected calibration-window directories")
    windows = defaultdict(dict)
    denominators = []
    seen = set()
    for folder in map(Path, args.runs):
        plan = read_json(folder / "plan.json")
        provenance = read_json(folder / "manifest.json")
        if plan["run_id"] in seen:
            raise ValueError("Duplicate run directory")
        seen.add(plan["run_id"])
        values = defaultdict(list)
        records = [read_json(p) for p in (folder / "jobs").glob("*.json")]
        for row in records:
            values[(row["group"], row["case"])].append(row["tvd_improvement"])
        # Same label deliberately merges runs belonging to one calibration window.
        for (group, case), samples in values.items():
            stratum = f"{plan['backend']}|{provenance['catalog_sha256']}|{group}"
            windows[stratum].setdefault(plan["window_label"], {}).setdefault(case, []).extend(samples)
        denominators.append({"run_id": plan["run_id"], "window": plan["window_label"],
                             "planned_pairs": len(plan["batches"]), "collected_pairs": len(records)})
    result = {}
    rng = np.random.default_rng(17)
    for group, by_window in windows.items():
        samples = [np.array([np.mean(v) for v in cases.values()]) for cases in by_window.values()]
        bootstrap = []
        if len(samples) >= 2:
            for _ in range(2000):
                chosen = rng.integers(0, len(samples), len(samples))
                bootstrap.append(np.mean([rng.choice(samples[i], len(samples[i]), replace=True).mean() for i in chosen]))
        result[group] = {"windows": len(samples), "mean": float(np.mean([v.mean() for v in samples])),
                         "ci95": np.quantile(bootstrap, [.025, .975]).tolist() if bootstrap else None}
    destination = Path(args.output)
    destination.mkdir(parents=True, exist_ok=False)
    write_json(destination / "summary.json", {"paired_tvd_improvement": result, "denominators": denominators,
                                              "uncertainty_unit": "windows, then circuit means; equal weight per window",
                                              "missing_results": "Excluded and counted, never imputed as improvements"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "submit", "collect", "attach", "summarize"])
    parser.add_argument("--output", required=True)
    parser.add_argument("--account-name")
    parser.add_argument("--backend", default="ibm_fez")
    parser.add_argument("--config", default=str(ROOT / "experiments/configs/smoke.json"))
    parser.add_argument("--catalog", default=str(ROOT / "patterns/qrisk-imported.json"))
    parser.add_argument("--mlirq-opt", default=str(ROOT / "build/bin/mlirq-opt"))
    parser.add_argument("--input-dir")
    parser.add_argument("--window", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    parser.add_argument("--offline", action="store_true", help="Prepare with FakeBackend, without an account; cannot submit")
    parser.add_argument("--include-fixtures", action="store_true")
    parser.add_argument("--submit", action="store_true", help="Actually submit hardware jobs; otherwise show the plan")
    parser.add_argument("--max-jobs", type=int, default=10)
    parser.add_argument("--batch", type=int)
    parser.add_argument("--job-id")
    parser.add_argument("--runs", nargs="+")
    args = parser.parse_args()
    {"prepare": prepare, "submit": submit, "collect": collect, "attach": attach, "summarize": summarize}[args.action](args)


if __name__ == "__main__":
    main()
