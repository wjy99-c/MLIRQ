"""End-to-end reporting, validation and experimental-policy boundaries."""
from copy import deepcopy
import json
from unittest.mock import patch
import unittest
from qiskit import ClassicalRegister, QuantumCircuit, transpile
from qiskit.circuit import Parameter
from qiskit.circuit.library import RZGate
from qiskit.transpiler import Target
from mlirq_qiskit import (ExportError, InputError, MitigationOptions, NativeCompiler,
                         NativeCompilerError, optimize_compiled_circuit, validate_target_instructions)
from mlirq_qiskit.experiments.common import equivalence
from mlirq_qiskit.matching import count_patterns
from test_adapter import catalog, imported, make_target


class OptimizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = NativeCompiler()

    def optimize(self, circuit, patterns=None, **kwargs):
        return optimize_compiled_circuit(circuit, backend_name="test_backend",
                                          target=make_target(max(1, circuit.num_qubits)),
                                          patterns=patterns or catalog(), compiler=self.compiler, **kwargs)

    def test_post_transpile_reduction_and_unchanged_context(self):
        target = make_target(6)
        logical = QuantumCircuit(1, 1, global_phase=.137)
        logical.z(0); logical.rz(.3, 0); logical.measure(0, 0)
        compiled = transpile(logical, target=target, initial_layout=[4], optimization_level=0, seed_transpiler=17)
        compiled.metadata = {"nested": {"a": [1, 2]}}
        before = deepcopy(compiled)
        with patch("qiskit.transpile", side_effect=AssertionError("Unexpected recompilation")):
            result = self.optimize(compiled, catalog(physical=4))
        self.assertEqual(compiled, before)
        self.assertEqual(result.report["before"], {"z-rz": 1})
        self.assertEqual(result.report["after"], {"z-rz": 0})
        self.assertEqual(equivalence(compiled, result.circuit)["status"], "passed")
        self.assertEqual(result.report["output_validation"]["valid"], True)
        self.assertEqual(len(result.report["target_sha256"]), 64)
        json.dumps(result.report, allow_nan=False)

    def test_output_validator_checks_direction_and_bound_values(self):
        target = Target(num_qubits=2)
        target.add_instruction(RZGate(.3), {(1,): None})
        good = QuantumCircuit(2); good.rz(.3, 1)
        validate_target_instructions(good, target)
        for q, angle in [(0, .3), (1, .7)]:
            bad = QuantumCircuit(2); bad.rz(angle, q)
            with self.assertRaises(ExportError):
                validate_target_instructions(bad, target)

    def test_effect_fences_and_classical_overwrites(self):
        circuit = QuantumCircuit(3, 2, global_phase=.17)
        circuit.h(0); circuit.z(0); circuit.barrier(2); circuit.rz(.3, 0)
        circuit.measure(0, 1); circuit.h(1); circuit.measure(1, 1)
        result = self.optimize(circuit)
        self.assertEqual(result.report["status"], "blocked")
        check = equivalence(circuit, result.circuit)
        self.assertEqual(check["status"], "passed")
        self.assertLess(check["full_instrument_max_error"], 1e-12)

    def test_backend_isolation_and_no_matches(self):
        circuit = QuantumCircuit(1); circuit.z(0); circuit.rz(.3, 0)
        result = self.optimize(circuit, catalog(backend="other"))
        self.assertEqual(result.report["status"], "no_backend_patterns")
        self.assertEqual(result.report["after"], {})
        empty = QuantumCircuit(1); empty.h(0)
        self.assertEqual(self.optimize(empty).report["status"], "no_matches")

    def test_scope_ablation_and_final_scoped_rescan(self):
        circuit = QuantumCircuit(2); circuit.z(0); circuit.h(1); circuit.rz(.3, 0)
        scoped = self.optimize(circuit)
        global_result = self.optimize(circuit, options=MitigationOptions(matching="global"))
        self.assertEqual(scoped.report["after"], {"z-rz": 0})
        self.assertEqual(global_result.report["before"], {"z-rz": 0})
        self.assertEqual(global_result.report["final_scoped_counts"], {"z-rz": 1})

    def test_global_guard_ablations_and_budgeted_local_cycles(self):
        patterns = catalog()
        forward = patterns["patterns"][0]
        duplicate = deepcopy(forward); duplicate["id"] = "duplicate"
        reverse = deepcopy(forward); reverse["id"] = "reverse"; reverse["gates"].reverse()
        patterns["patterns"] += [duplicate, reverse]
        circuit = QuantumCircuit(1); circuit.z(0); circuit.rz(.3, 0)
        default = self.optimize(circuit, patterns)
        total = self.optimize(circuit, patterns, options=MitigationOptions(acceptance="total"))
        local = self.optimize(circuit, patterns, options=MitigationOptions(acceptance="local", max_candidates=3))
        self.assertEqual(default.report["status"], "blocked")
        self.assertEqual(total.report["after"]["reverse"], 1)
        self.assertEqual(sum(total.report["after"].values()), 1)
        self.assertEqual(local.report["termination_reason"], "budget")
        self.assertEqual(local.report["native"]["circuits"][0]["candidates"], 3)
        self.assertEqual(equivalence(circuit, local.circuit)["status"], "passed")

    def test_rule_ablation(self):
        patterns = catalog()
        patterns["patterns"][0]["gates"] = [{"gate": "x", "qubits": [0]}, {"gate": "sx", "qubits": [0]}]
        circuit = QuantumCircuit(1); circuit.x(0); circuit.sx(0)
        self.assertEqual(self.optimize(circuit, patterns).report["status"], "eliminated")
        self.assertEqual(self.optimize(circuit, patterns, options=MitigationOptions(rules="diagonal")).report["status"], "blocked")

    def test_rewrite_budget_and_instrumentation(self):
        circuit = QuantumCircuit(1)
        for _ in range(3):
            circuit.z(0); circuit.rz(.3, 0)
        result = self.optimize(circuit, options=MitigationOptions(max_rewrites=1))
        detail = result.report["native"]["circuits"][0]
        self.assertEqual(result.report["termination_reason"], "budget")
        self.assertEqual(detail["rewrites"], 1)
        self.assertGreaterEqual(detail["scans"], 2)
        self.assertGreaterEqual(detail["scan_seconds"], 0)
        self.assertEqual(result.report["after"], count_patterns(result.circuit, catalog(), "test_backend"))

    def test_report_disagreement_fails_closed(self):
        circuit = QuantumCircuit(1); circuit.z(0); circuit.rz(.3, 0)
        with patch("mlirq_qiskit.optimizer.count_patterns", return_value={"z-rz": 99}):
            with self.assertRaises(ExportError) as caught:
                self.optimize(circuit)
        self.assertEqual(caught.exception.code, "report_count_mismatch")

    def test_invalid_options(self):
        for options in [dict(max_candidates=-1), dict(max_rewrites=True), dict(matching=[]),
                        dict(acceptance="local"), dict(rules="unsafe")]:
            with self.subTest(options=options), self.assertRaises(InputError):
                MitigationOptions(**options)


if __name__ == "__main__":
    unittest.main()
