"""T1/T2 tests against real Qiskit circuits and the compiled native binary."""

import cmath
from copy import deepcopy
from dataclasses import replace
import json
import math
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister, transpile
from qiskit.circuit import Gate, Measure, Parameter
from qiskit.circuit.library import CXGate, CZGate, HGate, RZGate, SXGate, XGate, YGate, ZGate
from qiskit.quantum_info import Operator
from qiskit.transpiler import Target

from mlirq_qiskit import (
    CatalogError, InputError, NativeCompiler, NativeCompilerError,
    import_compiled_circuit,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "test"))
from test_cli import parse_generic_unitary, statevector


def make_target(n=6, *, line=False):
    target = Target(num_qubits=n)
    for gate in [HGate(), XGate(), ZGate(), SXGate(), RZGate(Parameter("theta")), Measure()]:
        target.add_instruction(gate)
    edges = (
        [(i, j) for i in range(n) for j in range(n) if abs(i - j) == 1]
        if line else [(i, j) for i in range(n) for j in range(n) if i != j]
    )
    for gate in [CXGate(), CZGate()]:
        target.add_instruction(gate, {edge: None for edge in edges})
    return target


def catalog(backend="test_backend", physical=0):
    return {
        "schema_version": 1,
        "source_url": "unknown",
        "patterns": [{
            "id": "z-rz", "backend": backend,
            "gates": [
                {"gate": "z", "qubits": [physical]},
                {"gate": "rz", "qubits": [physical], "angle": 0.3},
            ],
        }],
    }


def imported(circuit, *, target=None, patterns=None, backend="test_backend"):
    return import_compiled_circuit(
        circuit, backend_name=backend,
        target=target if target is not None else make_target(max(1, circuit.num_qubits)),
        patterns=patterns if patterns is not None else catalog(),
    )


class ImporterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Missing native compiler is a test failure, never a silent skip.
        cls.compiler = NativeCompiler()

    def verified(self, circuit, **kwargs):
        return self.compiler.run(imported(circuit, **kwargs))

    def assert_input_error(self, circuit, code, **kwargs):
        with self.assertRaises(InputError) as caught:
            imported(circuit, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_all_gate_semantics_and_operand_roles(self):
        circuit = QuantumCircuit(3, global_phase=0.217)
        circuit.h(2)
        circuit.x(0)
        circuit.z(1)
        circuit.sx(2)
        circuit.rz(-0.719, 1)
        circuit.cx(2, 0)
        circuit.cz(1, 2)
        result = self.verified(circuit)
        n, events = parse_generic_unitary(result.mlir)
        self.assertEqual(n, 3)
        expected = Operator(circuit).data
        for basis in range(1 << n):
            actual = np.array(statevector(n, events, basis)) * cmath.exp(1j * result.module.global_phase)
            np.testing.assert_allclose(actual, expected[:, basis], atol=1e-12, rtol=0)
        self.assertIn(("cx", (2, 0), None), events)
        self.assertIn(("cz", (1, 2), None), events)

    def test_real_transpilation_keeps_existing_physical_positions(self):
        target = make_target(6, line=True)
        logical = QuantumCircuit(2)
        logical.h(0)
        logical.cx(0, 1)
        logical.rz(0.319, 1)
        compiled = transpile(
            logical, target=target, initial_layout=[4, 1],
            optimization_level=1, seed_transpiler=21,
        )
        self.assertIsNotNone(compiled.layout)
        before = deepcopy(compiled)
        expected = [
            (item.operation.name, tuple(compiled.find_bit(q).index for q in item.qubits))
            for item in compiled.data
        ]
        with (
            patch("qiskit.transpile", side_effect=AssertionError("unexpected transpilation")),
            patch("qiskit.transpiler.preset_passmanagers.generate_preset_pass_manager",
                  side_effect=AssertionError("unexpected pass-manager construction")),
        ):
            result = self.verified(compiled, target=target)
        n, events = parse_generic_unitary(result.mlir)
        self.assertEqual([(name, qs) for name, qs, _ in events], expected)
        self.assertEqual(n, compiled.num_qubits)
        self.assertEqual(result.module.physical_qubits, tuple(range(6)))
        self.assertEqual(compiled, before)
        self.assertEqual(
            result.module.source_circuit.layout.final_index_layout(filter_ancillas=False),
            before.layout.final_index_layout(filter_ancillas=False),
        )
        # Compare directly in compiled physical-wire order; do not apply layout again.
        matrix = Operator(compiled).data
        for basis in [0, 1, 17, 63]:
            actual = np.array(statevector(n, events, basis)) * cmath.exp(1j * result.module.global_phase)
            np.testing.assert_allclose(actual, matrix[:, basis], atol=1e-12, rtol=0)

    def test_sparse_active_wires_preserve_full_width(self):
        circuit = QuantumCircuit(20)
        circuit.sx(3)
        circuit.cz(3, 16)
        circuit.rz(0.3, 16)
        result = self.verified(circuit, target=make_target(24))
        ids = [int(q) for q in re.findall(r"physical = (\d+) : i64", result.mlir)]
        self.assertEqual(ids, list(range(20)))
        self.assertEqual(result.module.num_qubits, 20)
        self.assertIn("num_qubits = 24 : i64", result.mlir)
        _, events = parse_generic_unitary(result.mlir)
        self.assertEqual(events, [("sx", (3,), None), ("cz", (3, 16), None), ("rz", (16,), 0.3)])

    def test_flat_bit_positions_ignore_register_names_and_offsets(self):
        left, right = QuantumRegister(2, "left"), QuantumRegister(3, "right")
        circuit = QuantumCircuit(left, right)
        circuit.cx(right[2], left[1])
        n, events = parse_generic_unitary(self.verified(circuit).mlir)
        self.assertEqual(n, 5)
        self.assertEqual(events, [("cx", (4, 1), None)])

    def test_empty_and_idle_circuits(self):
        for width in [0, 4]:
            with self.subTest(width=width):
                circuit = QuantumCircuit(width, 2)
                result = self.verified(circuit)
                self.assertEqual(result.mlir.count('"mlirq.alloc"'), width)
                self.assertEqual(result.mlir.count('"mlirq.discard"'), width)
                self.assertEqual(result.module.num_clbits, 2)

    def test_terminal_measurement_destinations_and_partial_measurement(self):
        a, b = ClassicalRegister(2, "a"), ClassicalRegister(2, "b")
        circuit = QuantumCircuit(QuantumRegister(3, "q"), a, b)
        circuit.x(2)
        circuit.measure(2, b[1])
        circuit.h(0)  # Other wires may continue after a terminal measurement.
        circuit.measure(0, a[1])
        result = self.verified(circuit)
        self.assertEqual(result.module.measurements, ((1, 2, 3), (3, 0, 1)))
        self.assertIn("mlirq.qiskit.clbit = 3 : i64", result.mlir)
        self.assertIn("mlirq.qiskit.clbit = 1 : i64", result.mlir)
        self.assertEqual(result.mlir.count('"mlirq.discard"'), 1)
        self.assertEqual(result.module.source_circuit, circuit)

    def test_binary64_parameters_round_trip(self):
        values = [0.0, -0.0, 0.1, -math.pi, 1e-300, 5e-324, float.fromhex("0x1.fffffffffffffp+1023")]
        circuit = QuantumCircuit(1)
        for value in values:
            circuit.rz(value, 0)
        text = self.verified(circuit).mlir
        encoded = re.findall(r"angle = ([^ ]+) : f64", text)
        self.assertEqual(len(encoded), len(values))
        for token, value in zip(encoded, values):
            actual = struct.unpack(">d", bytes.fromhex(token[2:]))[0] if token.startswith("0x") else float(token)
            self.assertEqual(struct.pack(">d", actual), struct.pack(">d", value))

    def test_import_snapshots_are_isolated_from_caller_mutation(self):
        circuit = QuantumCircuit(1, global_phase=0.2)
        circuit.metadata = {"notes": ["original"]}
        circuit.h(0)
        target, patterns = make_target(1), catalog()
        module = imported(circuit, target=target, patterns=patterns)
        old_ir, old_digest = module.mlir, module.catalog_sha256
        circuit.x(0)
        circuit.metadata["notes"].append("changed")
        target.add_instruction(YGate())
        patterns["patterns"].clear()
        detached = module.source_circuit
        detached.metadata["notes"].append("detached")
        module.pattern_catalog["patterns"].clear()
        self.assertEqual(module.source_circuit.metadata, {"notes": ["original"]})
        self.assertEqual(len(module.source_circuit.data), 1)
        self.assertNotIn("y", module.target.operation_names)
        self.assertEqual(len(module.pattern_catalog["patterns"]), 1)
        self.assertEqual(module.catalog_sha256, old_digest)
        self.compiler.run(module)
        self.assertEqual(module.mlir, old_ir)

    def test_rejects_symbolic_nonfinite_and_unknown_gate_semantics(self):
        circuit = QuantumCircuit(1)
        circuit.rz(Parameter("theta"), 0)
        self.assert_input_error(circuit, "unbound_parameters")
        circuit = QuantumCircuit(1, global_phase=Parameter("phase"))
        self.assert_input_error(circuit, "unbound_parameters")
        for value in [float("nan"), float("inf")]:
            circuit = QuantumCircuit(1)
            circuit.rz(value, 0)
            self.assert_input_error(circuit, "invalid_parameter")
        circuit = QuantumCircuit(1)
        impostor = Gate("x", 1, [])
        definition = QuantumCircuit(1)
        definition.z(0)
        impostor.definition = definition
        circuit.append(impostor, [0])
        error = self.assert_input_error(circuit, "unsupported_operation")
        self.assertEqual(error.as_dict()["instruction_index"], 0)
        self.assertEqual(error.as_dict()["operation"], "x")

    def test_rejects_reset_and_delay(self):
        for kind in ["reset", "delay"]:
            circuit = QuantumCircuit(1)
            if kind == "delay":
                circuit.delay(5, 0, unit="dt")
            else:
                getattr(circuit, kind)(0)
            with self.subTest(kind=kind):
                self.assert_input_error(circuit, "unsupported_operation")

    def test_rejects_timing_scheduled_circuit_even_without_delays(self):
        circuit = QuantumCircuit(1)
        circuit.x(0)
        # Qiskit's schedule analysis populates this backing value.
        circuit._op_start_times = [0]
        self.assert_input_error(circuit, "unsupported_schedule")

    def test_rejects_control_flow(self):
        circuit = QuantumCircuit(1, 1)
        with circuit.if_test((circuit.clbits[0], True)):
            circuit.x(0)
        self.assert_input_error(circuit, "unsupported_operation")

    def test_rejects_quantum_reuse(self):
        circuit = QuantumCircuit(2, 1)
        circuit.measure(0, 0)
        circuit.x(0)
        self.assert_input_error(circuit, "use_after_measurement")

    def test_rejects_invalid_backend_capacity_and_target_instruction(self):
        circuit = QuantumCircuit(2)
        self.assert_input_error(circuit, "invalid_backend", backend="test*")
        self.assert_input_error(circuit, "target_capacity", target=make_target(1))
        self.assert_input_error(circuit, "invalid_target", target=Target(num_qubits=None))
        directed = Target(num_qubits=2)
        directed.add_instruction(CXGate(), {(0, 1): None})
        circuit.cx(1, 0)
        self.assert_input_error(circuit, "unsupported_target_instruction", target=directed)
        fixed = Target(num_qubits=1)
        fixed.add_instruction(RZGate(0.5))
        circuit = QuantumCircuit(1)
        circuit.rz(0.3, 0)
        self.assert_input_error(circuit, "unsupported_target_instruction", target=fixed)
        wrong_class = Target(num_qubits=1)
        wrong_class.add_instruction(ZGate(), name="x")
        circuit = QuantumCircuit(1)
        circuit.x(0)
        self.assert_input_error(circuit, "unsupported_target_instruction", target=wrong_class)

    def test_target_union_does_not_authorize_wrong_instruction(self):
        target = Target(num_qubits=3)
        target.add_instruction(CXGate(), {(0, 1): None})
        target.add_instruction(CZGate(), {(1, 2): None})
        circuit = QuantumCircuit(3)
        circuit.cx(1, 2)
        self.assert_input_error(circuit, "unsupported_target_instruction", target=target)

    def test_catalog_envelope_and_non_json_errors(self):
        for data in [[], {"schema_version": True, "source_url": "x", "patterns": []},
                     {"schema_version": 1, "source_url": "", "patterns": []},
                     {"schema_version": 1, "source_url": "x", "patterns": [], "bad": float("nan")}]:
            with self.subTest(data=data), self.assertRaises(CatalogError) as caught:
                imported(QuantumCircuit(1), patterns=data)
            self.assertEqual(caught.exception.code, "invalid_catalog")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "bad.json")
            path.write_text("{invalid", encoding="utf-8")
            with self.assertRaises(CatalogError):
                imported(QuantumCircuit(1), patterns=path)


class NativeBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = NativeCompiler()

    def test_scan_mitigate_and_rescan_with_catalog_snapshot(self):
        circuit = QuantumCircuit(1)
        circuit.z(0)
        circuit.rz(0.3, 0)
        with tempfile.TemporaryDirectory(prefix="catalog with spaces ") as directory:
            path = Path(directory, "patterns with spaces.json")
            path.write_text(json.dumps(catalog()), encoding="utf-8")
            module = imported(circuit, patterns=path)
            path.write_text("not json any more", encoding="utf-8")
            scanned = self.compiler.run(module, mode="scan")
            result = self.compiler.run(module, mode="mitigate")
        self.assertIn("before_total = 1 : i64", scanned.mlir)
        self.assertIn("before_total = 1 : i64", result.mlir)
        self.assertIn("after_total = 0 : i64", result.mlir)
        self.assertIn('status = "eliminated"', result.mlir)
        self.assertIn("before_total = 0 : i64", self.compiler.run(result.module, mode="scan").mlir)
        self.assertEqual(module.source_circuit, circuit)
        self.assertEqual(result.module.source_circuit, circuit)
        self.assertNotEqual(result.mlir, module.mlir)

    def test_real_qrisk_catalog_runs_on_sparse_physical_qubits(self):
        circuit = QuantumCircuit(5)
        for _ in range(2):
            circuit.sx(3)
            circuit.cz(3, 4)
            circuit.rz(-3.141593, 3)
            circuit.sx(4)
        module = imported(
            circuit, backend="ibm_fez", target=make_target(6),
            patterns=ROOT / "patterns/qrisk-imported.json",
        )
        result = self.compiler.run(module, mode="mitigate")
        self.assertIn("before_total = 2 : i64", result.mlir)
        self.assertIn("after_total = 0 : i64", result.mlir)

    def test_backend_mismatch_is_explicit_noop(self):
        circuit = QuantumCircuit(1)
        circuit.z(0)
        circuit.rz(0.3, 0)
        module = imported(circuit, patterns=catalog("another_backend"))
        result = self.compiler.run(module, mode="mitigate")
        self.assertIn('status = "no_backend_patterns"', result.mlir)

    def test_invalid_native_ir_and_catalog_return_structured_diagnostics(self):
        module = imported(QuantumCircuit(1))
        for bad in [
            replace(module, mlir="this is not MLIR"),
            replace(module, catalog_json='{"schema_version":1,"source_url":"x","patterns":[{}]}'),
        ]:
            with self.subTest(bad=bad.mlir[:20]), self.assertRaises(NativeCompilerError) as caught:
                self.compiler.run(bad, mode="scan")
            error = caught.exception.as_dict()
            self.assertEqual(error["code"], "native_failure")
            self.assertNotEqual(error["returncode"], 0)
            self.assertTrue(error["diagnostics"])

    def test_missing_compiler_invalid_arguments_and_timeout(self):
        with self.assertRaises(NativeCompilerError) as caught:
            NativeCompiler("/nonexistent/mlirq-opt")
        self.assertEqual(caught.exception.code, "native_not_found")
        for timeout in [0, -1, True, float("nan"), 10**1000]:
            with self.subTest(timeout=timeout), self.assertRaises(InputError):
                NativeCompiler(self.compiler.executable, timeout=timeout)
        for executable in ["", b"bytes", 42, "bad\0path"]:
            with self.subTest(executable=executable), self.assertRaises(InputError):
                NativeCompiler(executable)
        module = imported(QuantumCircuit(1))
        for mode in ["route", []]:
            with self.subTest(mode=mode), self.assertRaises(InputError):
                self.compiler.run(module, mode=mode)
        with patch("mlirq_qiskit.native.subprocess.run", side_effect=subprocess.TimeoutExpired(
            ["mlirq-opt"], 0.01, stderr=b"partial diagnostic"
        )), self.assertRaises(NativeCompilerError) as caught:
            self.compiler.run(module)
        self.assertEqual(caught.exception.code, "native_timeout")
        self.assertEqual(caught.exception.diagnostics, "partial diagnostic")

    def test_executable_and_working_directory_with_spaces(self):
        module = imported(QuantumCircuit(1))
        with tempfile.TemporaryDirectory(prefix="native path with spaces ") as directory:
            executable = Path(directory, "mlirq opt ; no shell")
            executable.symlink_to(self.compiler.executable)
            original = tempfile.TemporaryDirectory
            with patch("mlirq_qiskit.native.tempfile.TemporaryDirectory",
                       side_effect=lambda **kw: original(dir=directory, **kw)):
                result = NativeCompiler(executable).run(module, mode="scan")
            self.assertIn('"mlirq.circuit"', result.mlir)

    def test_process_launch_failure_has_a_structured_error(self):
        module = imported(QuantumCircuit(1))
        with patch("mlirq_qiskit.native.subprocess.run", side_effect=OSError("launch failed")):
            with self.assertRaises(NativeCompilerError) as caught:
                self.compiler.run(module)
        self.assertEqual(caught.exception.code, "native_io_error")


if __name__ == "__main__":
    unittest.main()
