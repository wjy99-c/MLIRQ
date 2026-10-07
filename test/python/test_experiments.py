"""Independent oracle sensitivity, baseline behavior, and hardware dry runs."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from qiskit import QuantumCircuit
from mlirq_qiskit.experiments.baselines import random_legal, qrisk_transform
from mlirq_qiskit.experiments.common import (derived_seed, digest, equivalence, ideal_distribution, instrument,
                                            paired_bootstrap, save_circuit, write_json, read_json)
from mlirq_qiskit.experiments import hardware
from mlirq_qiskit.experiments.runner import summary
from test_adapter import make_target, catalog


class ExperimentTests(unittest.TestCase):
    def test_random_streams_are_reproducible_and_distinct(self):
        keys = [(seed, case, repeat, stream) for seed in [17, 29] for case in ["a", "b"]
                for repeat in [0, 1] for stream in ["simulator", "pub-order", "baseline"]]
        values = [derived_seed(*key) for key in keys]
        self.assertEqual(values, [derived_seed(*key) for key in keys])
        self.assertEqual(len(values), len(set(values)))

    def test_oracle_detects_phase_and_measurement_map_errors(self):
        a = QuantumCircuit(2, 2); a.h(0); a.h(1); a.measure([0, 1], [0, 1])
        phase = a.copy(); phase.global_phase = .1
        self.assertEqual(equivalence(a, phase)["status"], "failed")
        b = QuantumCircuit(2, 2); b.h(0); b.h(1); b.measure([0, 1], [1, 0])
        self.assertEqual(ideal_distribution(a), ideal_distribution(b))
        self.assertGreater(equivalence(a, b)["full_instrument_max_error"], .1)

    def test_random_baseline_keeps_control_target_roles_and_fences(self):
        c = QuantumCircuit(4, 1, global_phase=.13)
        c.h(3); c.cx(3, 1); c.z(1); c.barrier(0); c.rz(.7, 3); c.measure(3, 0)
        before = c.copy()
        output, report = random_legal(c, seed=17, attempts=40)
        self.assertEqual(equivalence(c, output)["status"], "passed")
        self.assertEqual(c, before)
        self.assertEqual(report["attempt_budget"], 40)

    def test_qrisk_rejects_unsupported_fences_before_loading(self):
        c = QuantumCircuit(2); c.z(0); c.barrier(1); c.rz(.3, 0)
        with patch("mlirq_qiskit.experiments.baselines.load_qrisk") as load:
            with self.assertRaises(ValueError):
                qrisk_transform(c, catalog(), "test_backend")
            load.assert_not_called()

    def test_bootstrap_and_summary_keep_denominators(self):
        self.assertIsNone(paired_bootstrap([.2])["ci95"])
        self.assertEqual(paired_bootstrap([0., 0.])["ci95"], [0., 0.])
        with tempfile.TemporaryDirectory() as work:
            rows = [{"case": "a", "group": "generated", "method": "qrisk", "status": "timeout"},
                    {"case": "b", "group": "generated", "method": "qrisk", "status": "ineligible"}]
            result = summary(Path(work), rows, "rq2")
            self.assertEqual(result["groups"][0]["attempted_rows"], 2)
            self.assertEqual(result["eligible_rows"], 0)

    def test_hardware_dry_run_and_offline_submission_do_not_connect(self):
        with tempfile.TemporaryDirectory() as work:
            write_json(Path(work)/"plan.json", {"backend": "ibm_fez", "offline_prepare": True,
                                                "shots_per_pub": 16, "batches": [{"state": "prepared"}]})
            args = SimpleNamespace(output=work, submit=False, max_jobs=1)
            with patch.object(hardware, "service") as connection:
                hardware.submit(args)
                args.submit = True
                with self.assertRaises(ValueError):
                    hardware.submit(args)
                connection.assert_not_called()

    def test_prepared_artifact_hash_check(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work)/"input.qpy"
            sha = save_circuit(path, QuantumCircuit(1))
            item = {"path": path.name, "sha256": sha}
            hardware.checked_circuit(Path(work), item)
            path.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                hardware.checked_circuit(Path(work), item)

    def test_hardware_collection_uses_saved_pub_order_and_is_resumable(self):
        with tempfile.TemporaryDirectory() as work:
            output = Path(work)
            write_json(output/"ideal.json", {"0": 1.0})
            plan = {"window_label": "window-1", "circuits": [{"case": "a", "group": "generated",
                    "ideal": "ideal.json", "ideal_sha256": digest((output/"ideal.json").read_bytes())}],
                    "batches": [{"case": "a", "batch": 0, "repeat": 0, "state": "submitted",
                                 "job_id": "fake-job", "order": ["mlirq", "qiskit"]}]}
            write_json(output/"plan.json", plan)
            class Result(list):
                metadata = {"mock": True}
            pubs = Result([SimpleNamespace(join_data=lambda: SimpleNamespace(get_counts=lambda: {"0": 16}), metadata={}),
                           SimpleNamespace(join_data=lambda: SimpleNamespace(get_counts=lambda: {"0": 8, "1": 8}), metadata={})])
            with patch.object(hardware, "service") as connection:
                job = connection.return_value.job.return_value
                job.status.return_value = "DONE"
                job.result.return_value = pubs
                job.metrics.return_value = {}
                args = SimpleNamespace(output=work)
                hardware.collect(args)
                hardware.collect(args)
                self.assertEqual(job.result.call_count, 1)
            record = read_json(output/"jobs/batch-0.json")
            self.assertEqual(record["tvd_improvement"], .5)
            self.assertEqual(read_json(output/"hardware-summary.json")["collected_pairs"], 1)

    def test_window_summary_retains_harmful_changes(self):
        with tempfile.TemporaryDirectory() as work:
            folders = []
            for index, effect in enumerate([.2, -.2]):
                folder = Path(work)/str(index)
                write_json(folder/"plan.json", {"run_id": str(index), "backend": "ibm_fez", "window_label": str(index), "batches": [{}]})
                write_json(folder/"manifest.json", {"catalog_sha256": "catalog"})
                write_json(folder/"jobs/batch-0.json", {"case": "a", "group": "generated", "tvd_improvement": effect})
                folders.append(str(folder))
            destination = Path(work)/"summary"
            hardware.summarize(SimpleNamespace(runs=folders, output=str(destination)))
            result = read_json(destination/"summary.json")["paired_tvd_improvement"]["ibm_fez|catalog|generated"]
            self.assertEqual(result["mean"], 0)
            self.assertEqual(result["windows"], 2)
            self.assertEqual(result["ci95"], [-.2, .2])

    @unittest.skipUnless(__import__("importlib.util").util.find_spec("qiskit_ibm_runtime"), "experiment extra not installed")
    def test_uncertain_submission_is_not_retried(self):
        with tempfile.TemporaryDirectory() as work:
            output = Path(work)
            c = QuantumCircuit(1, 1); c.x(0); c.measure(0, 0)
            item = {"path": "input.qpy", "sha256": save_circuit(output/"input.qpy", c)}
            plan = {"run_id": "test-run", "backend": "ibm_fez", "offline_prepare": False,
                    "shots_per_pub": 16, "circuits": [{"case": "a", "files": {"qiskit": item, "mlirq": item}}],
                    "batches": [{"case": "a", "batch": 0, "order": ["qiskit", "mlirq"],
                                 "state": "prepared", "job_id": None}]}
            write_json(output/"plan.json", plan)
            args = SimpleNamespace(output=work, submit=True, max_jobs=1)
            backend = SimpleNamespace(target=make_target(1), properties=lambda **kw: None)
            with patch.object(hardware, "service") as connection, \
                    patch.object(hardware, "snapshot_backend", return_value={}), \
                    patch("qiskit_ibm_runtime.SamplerV2") as sampler:
                connection.return_value.backend.return_value = backend
                sampler.return_value.run.side_effect = TimeoutError("uncertain network response")
                with self.assertRaises(TimeoutError):
                    hardware.submit(args)
                self.assertEqual(read_json(output/"plan.json")["batches"][0]["state"], "submission_started")
                hardware.submit(args)
                self.assertEqual(sampler.return_value.run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
