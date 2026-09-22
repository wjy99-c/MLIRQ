"""T3/T4 round trips, measured semantics, rewrite fences, and export failures."""

from collections import defaultdict
from copy import deepcopy
from dataclasses import replace
import math
from pathlib import Path
import struct
import subprocess
import unittest
from unittest.mock import patch

import numpy as np
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister, transpile
from qiskit.circuit import AncillaQubit, Barrier, Clbit, Measure, Qubit
from qiskit.circuit.library import RZGate, ZGate
from qiskit.quantum_info import Operator, Statevector

from mlirq_qiskit import (
    ExportError, InputError, NativeCompiler, NativeCompilerError,
    export_qiskit_circuit,
)
from test_adapter import ROOT, catalog, imported, make_target


def signature(circuit):
    return [
        (item.operation.name, item.operation.label,
         tuple(circuit.find_bit(q).index for q in item.qubits),
         tuple(circuit.find_bit(c).index for c in item.clbits),
         tuple(struct.pack(">d", float(p)) for p in item.operation.params))
        for item in circuit.data
    ]


def ideal_distribution(circuit):
    """Exact branch simulation; retain classical overwrites and unwritten bits."""
    branches = [(0, Statevector.from_int(0, 2**circuit.num_qubits).data)]
    indices = np.arange(2**circuit.num_qubits)
    for item in circuit.data:
        name = item.operation.name
        qubits = [circuit.find_bit(q).index for q in item.qubits]
        if name == "barrier":
            continue
        if name != "measure":
            branches = [(bits, Statevector(state).evolve(item.operation, qubits).data)
                        for bits, state in branches]
            continue
        bit = circuit.find_bit(item.clbits[0]).index
        following = []
        for bits, state in branches:
            for outcome in (0, 1):
                projected = state.copy()
                projected[((indices >> qubits[0]) & 1) != outcome] = 0
                if np.vdot(projected, projected).real > 1e-16:
                    following.append(((bits & ~(1 << bit)) | (outcome << bit), projected))
        branches = following
    probabilities = defaultdict(float)
    for bits, state in branches:
        probabilities[bits] += float(np.vdot(state, state).real)
    return dict(probabilities)


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = NativeCompiler()

    def roundtrip(self, circuit, *, mode="verify", **kwargs):
        module = imported(circuit, **kwargs)
        native = self.compiler.run(module, mode=mode)
        return export_qiskit_circuit(native, compiler=self.compiler), native

    def assert_metadata(self, original, output):
        self.assertIsNot(original, output)
        self.assertEqual(original.name, output.name)
        self.assertEqual(original.metadata, output.metadata)
        self.assertEqual(original.qregs, output.qregs)
        self.assertEqual(original.cregs, output.cregs)
        self.assertEqual(original.qubits, output.qubits)
        self.assertEqual(original.clbits, output.clbits)
        self.assertEqual(original.ancillas, output.ancillas)
        self.assertEqual(original.global_phase, output.global_phase)
        self.assertEqual(original.layout, output.layout)
        for before, after in zip(original.qubits + original.clbits, output.qubits + output.clbits):
            self.assertEqual(original.find_bit(before), output.find_bit(after))

    def test_all_gates_and_phase_sensitive_operator_roundtrip(self):
        circuit = QuantumCircuit(3, name="all-gates", global_phase=0.381)
        circuit.metadata = {"experiment": ["retained", {"run": 12}]}
        circuit.h(2)
        circuit.x(0)
        circuit.z(1)
        circuit.sx(2)
        circuit.rz(-0.719, 1)
        circuit.cx(2, 0)
        circuit.cz(1, 2)
        output, _ = self.roundtrip(circuit)
        self.assertEqual(signature(output), signature(circuit))
        self.assert_metadata(circuit, output)
        np.testing.assert_allclose(Operator(output).data, Operator(circuit).data, atol=1e-12, rtol=0)

    def test_real_transpile_layout_is_retained_and_never_reapplied(self):
        target = make_target(6, line=True)
        logical = QuantumCircuit(2, 2, name="placed", global_phase=0.41)
        logical.h(0)
        logical.cx(0, 1)
        logical.rz(0.319, 1)
        logical.barrier(label="caller-fence")
        logical.measure([0, 1], [1, 0])
        compiled = transpile(logical, target=target, initial_layout=[4, 1],
                             optimization_level=1, seed_transpiler=21)
        self.assertIsNotNone(compiled.layout)
        self.assertIsNotNone(compiled.layout.final_layout)
        before = deepcopy(compiled)
        with (
            patch("qiskit.transpile", side_effect=AssertionError("unexpected transpilation")),
            patch("qiskit.transpiler.preset_passmanagers.generate_preset_pass_manager",
                  side_effect=AssertionError("unexpected pass-manager construction")),
        ):
            output, _ = self.roundtrip(compiled, target=target)
        self.assertEqual(signature(output), signature(before))
        self.assert_metadata(before, output)
        self.assertEqual(compiled, before)
        self.assertEqual(output.layout.final_index_layout(filter_ancillas=False),
                         before.layout.final_index_layout(filter_ancillas=False))
        self.assertIsNot(output.layout, compiled.layout)
        self.assertEqual(ideal_distribution(output), ideal_distribution(before))

    def test_sparse_physical_wires_and_idle_width(self):
        circuit = QuantumCircuit(20, 3)
        circuit.sx(3)
        circuit.cz(3, 16)
        circuit.rz(0.3, 16)
        circuit.barrier(16, 3)
        circuit.measure(16, 2)
        output, _ = self.roundtrip(circuit, target=make_target(24))
        self.assertEqual(output.num_qubits, 20)
        self.assertEqual(signature(output), signature(circuit))
        self.assert_metadata(circuit, output)

    def test_registers_loose_bits_aliases_and_ancillas(self):
        circuit = QuantumCircuit(name="mixed-bits", global_phase=0.17)
        loose_q, ancilla, loose_c = Qubit(), AncillaQubit(), Clbit()
        circuit.add_bits([loose_q, ancilla, loose_c])
        q, a, b = QuantumRegister(2, "q"), ClassicalRegister(2, "a"), ClassicalRegister(1, "b")
        circuit.add_register(q, a, b)
        circuit.add_register(QuantumRegister(bits=[q[1], loose_q], name="alias"))
        circuit.add_register(ClassicalRegister(bits=[b[0], loose_c], name="alias_c"))
        circuit.cx(q[1], loose_q)
        circuit.barrier(ancilla, q[0], loose_q, label="scoped")
        circuit.measure(loose_q, b[0])
        circuit.measure(q[1], loose_c)
        output, _ = self.roundtrip(circuit)
        self.assertEqual(signature(output), signature(circuit))
        self.assert_metadata(circuit, output)

    def test_empty_idle_and_zero_qubit_barriers(self):
        for width in [0, 4]:
            for barrier in [False, True]:
                with self.subTest(width=width, barrier=barrier):
                    circuit = QuantumCircuit(width, 2, global_phase=0.31)
                    if barrier:
                        circuit.append(Barrier(0, label="empty-fence"), [])
                        circuit.barrier(label="all-wires")
                    output, _ = self.roundtrip(circuit)
                    self.assertEqual(signature(output), signature(circuit))
                    self.assert_metadata(circuit, output)
                    self.assertFalse({"reset", "initialize", "discard"} & output.count_ops().keys())

    def test_binary64_export_is_lossless(self):
        circuit = QuantumCircuit(1)
        for angle in [0.0, -0.0, 0.1, -math.pi, 1e-300, 5e-324,
                      float.fromhex("0x1.fffffffffffffp+1023")]:
            circuit.rz(angle, 0)
        output, _ = self.roundtrip(circuit)
        self.assertEqual(signature(output), signature(circuit))

    def test_partial_and_full_measurements_preserve_bit_distributions(self):
        for complete in [False, True]:
            with self.subTest(complete=complete):
                a, b = ClassicalRegister(2, "a"), ClassicalRegister(2, "b")
                circuit = QuantumCircuit(QuantumRegister(4, "q"), a, b, global_phase=0.217)
                circuit.h(0)
                circuit.cx(0, 2)
                circuit.x(1)
                circuit.z(0)
                circuit.rz(0.3, 0)
                circuit.barrier(0, 2)
                circuit.measure(2, a[1])
                circuit.measure(1, b[0])
                circuit.h(3)
                if complete:
                    circuit.measure(0, b[1])
                    circuit.measure(3, a[0])
                circuit.barrier(label="after-measurement")
                output, native = self.roundtrip(circuit, mode="mitigate")
                self.assertIn("before_total = 1 : i64", native.mlir)
                self.assertIn("after_total = 0 : i64", native.mlir)
                self.assert_metadata(circuit, output)
                expected = {4: 0.5, 6: 0.5} if not complete else {4: 0.25, 5: 0.25, 14: 0.25, 15: 0.25}
                for observed in (ideal_distribution(circuit), ideal_distribution(output)):
                    self.assertEqual(set(observed), set(expected))
                    for bits, probability in expected.items():
                        self.assertAlmostEqual(observed[bits], probability, places=12)

    def test_measurement_overwrites_keep_last_write_and_order(self):
        circuit = QuantumCircuit(3, 2)
        circuit.h(0)
        circuit.cx(0, 1)
        circuit.append(Measure(label="first-write"), [0], [0])
        circuit.x(2)
        circuit.measure(2, 0)
        circuit.barrier(0, label="retired-wire")
        circuit.measure(1, 1)
        output, _ = self.roundtrip(circuit, mode="mitigate")
        self.assertEqual(signature(circuit), signature(output))
        for observed in (ideal_distribution(circuit), ideal_distribution(output)):
            self.assertEqual(set(observed), {1, 3})
            self.assertAlmostEqual(observed[1], 0.5)
            self.assertAlmostEqual(observed[3], 0.5)

    def test_labels_follow_reordered_gates_and_outputs_are_isolated(self):
        circuit = QuantumCircuit(1, name="labeled", global_phase=0.719)
        circuit.metadata = {"nested": ["original"]}
        circuit.append(ZGate(label="Z α"), [0])
        circuit.append(RZGate(0.3, label='RZ "β"'), [0])
        module = imported(circuit)
        result = self.compiler.run(module, mode="mitigate")
        circuit.metadata["nested"].append("caller edit")
        output = export_qiskit_circuit(result, compiler=self.compiler)
        self.assertEqual([i.operation.label for i in output.data], ['RZ "β"', "Z α"])
        self.assertEqual(output.metadata, {"nested": ["original"]})
        np.testing.assert_allclose(Operator(output).data, Operator(module.source_circuit).data, atol=1e-12, rtol=0)
        output.metadata["nested"].append("output edit")
        output.data[0].operation.params[0] = 0.9
        again = export_qiskit_circuit(result, compiler=self.compiler)
        self.assertEqual(again.metadata, {"nested": ["original"]})
        self.assertEqual(float(again.data[0].operation.params[0]), 0.3)
        self.assertEqual(signature(module.source_circuit), signature(imported(circuit).source_circuit))

    def test_barriers_split_matches_and_never_move(self):
        circuit = QuantumCircuit(1)
        circuit.z(0)
        circuit.rz(0.3, 0)
        circuit.barrier(label="left")
        circuit.z(0)
        circuit.barrier(label="middle")
        circuit.rz(0.3, 0)
        circuit.barrier(label="right")
        circuit.z(0)
        circuit.rz(0.3, 0)
        output, native = self.roundtrip(circuit, mode="mitigate")
        self.assertIn("before_total = 2 : i64", native.mlir)
        self.assertIn("after_total = 0 : i64", native.mlir)
        self.assertEqual([i.operation.name for i in output.data],
                         ["rz", "z", "barrier", "z", "barrier", "rz", "barrier", "rz", "z"])
        self.assertEqual([i.operation.label for i in output.data if i.operation.name == "barrier"],
                         ["left", "middle", "right"])
        np.testing.assert_allclose(Operator(output).data, Operator(circuit).data, atol=1e-12, rtol=0)

    def test_disjoint_and_empty_barriers_conservatively_block_crossing(self):
        for empty in [False, True]:
            with self.subTest(empty=empty):
                circuit = QuantumCircuit(2)
                circuit.z(0)
                circuit.append(Barrier(0 if empty else 1), [] if empty else [1])
                circuit.rz(0.3, 0)
                output, native = self.roundtrip(circuit, mode="mitigate")
                self.assertIn('status = "blocked"', native.mlir)
                self.assertIn("after_total = 1 : i64", native.mlir)
                self.assertEqual(signature(output), signature(circuit))

    def test_real_qrisk_reduction_survives_export_and_reimport(self):
        circuit = QuantumCircuit(5, global_phase=0.19)
        for _ in range(2):
            circuit.sx(3)
            circuit.cz(3, 4)
            circuit.rz(-3.141593, 3)
            circuit.sx(4)
        kwargs = {"backend": "ibm_fez", "target": make_target(6),
                  "patterns": ROOT / "patterns/qrisk-imported.json"}
        output, native = self.roundtrip(circuit, mode="mitigate", **kwargs)
        self.assertIn("before_total = 2 : i64", native.mlir)
        self.assertIn("after_total = 0 : i64", native.mlir)
        self.assertNotEqual(signature(output), signature(circuit))
        np.testing.assert_allclose(Operator(output).data, Operator(circuit).data, atol=1e-12, rtol=0)
        self.assertIn("before_total = 0 : i64",
                      self.compiler.run(imported(output, **kwargs), mode="scan").mlir)

    def test_no_matches_and_backend_mismatch_export_unchanged(self):
        for patterns in [catalog("other"), {"schema_version": 1, "source_url": "unknown", "patterns": []}]:
            circuit = QuantumCircuit(1)
            circuit.z(0)
            circuit.rz(0.3, 0)
            output, _ = self.roundtrip(circuit, mode="mitigate", patterns=patterns)
            self.assertEqual(signature(output), signature(circuit))

    def test_imported_bundle_can_export_directly_and_invalid_arguments_fail(self):
        module = imported(QuantumCircuit(1))
        self.assertEqual(export_qiskit_circuit(module), QuantumCircuit(1))
        with self.assertRaises(InputError):
            export_qiskit_circuit("module")
        with self.assertRaises(InputError):
            export_qiskit_circuit(module, compiler="bad")

    def test_native_barrier_verifier_rejects_invalid_physical_scopes(self):
        circuit = QuantumCircuit(2)
        circuit.barrier(0, 1)
        module = imported(circuit)
        for scope in ["array<i64: 0, 0>", "array<i64: -1, 0>", "array<i64: 0, 2>"]:
            with self.subTest(scope=scope), self.assertRaises(NativeCompilerError):
                self.compiler.run(replace(module, mlir=module.mlir.replace("array<i64: 0, 1>", scope)))

    def test_export_rejects_changed_operations_operands_parameters_and_metadata(self):
        circuit = QuantumCircuit(2, 2)
        circuit.z(0)
        circuit.rz(-0.0, 1)
        module = imported(circuit, target=make_target(3))
        changes = [
            module.mlir.replace('"mlirq.z"', '"mlirq.x"'),
            module.mlir.replace("0x8000000000000000", "0x0000000000000000"),
            module.mlir.replace('name = "test_backend"', 'name = "changed"'),
            module.mlir.replace("mlirq.qiskit.num_clbits = 2", "mlirq.qiskit.num_clbits = 3"),
            module.mlir.replace("mlirq.qiskit.global_phase = 0x0000000000000000",
                                "mlirq.qiskit.global_phase = 0x3FF0000000000000"),
            module.mlir.replace("physical = 0", "physical = PLACEHOLDER")
                       .replace("physical = 1", "physical = 0")
                       .replace("physical = PLACEHOLDER", "physical = 1"),
        ]
        for ir in changes:
            with self.subTest(ir=ir), self.assertRaises(ExportError):
                export_qiskit_circuit(replace(module, mlir=ir), compiler=self.compiler)

    def test_export_rejects_missing_duplicate_ids_opaque_attributes_and_old_schema(self):
        circuit = QuantumCircuit(1)
        circuit.z(0)
        circuit.rz(0.3, 0)
        module = imported(circuit)
        changes = [
            module.mlir.replace("source_index = 1", "source_index = 0"),
            module.mlir.replace("source_index = 0", "source_index = -1"),
            module.mlir.replace("mlirq.qiskit.source_index = 0 : i64", ""),
            module.mlir.replace("mlirq.qiskit.source_index = 0 : i64",
                                "mlirq.qiskit.source_index = 0 : i64, timing = 5 : i64"),
            module.mlir.replace("import_version = 2", "import_version = 1"),
        ]
        for ir in changes:
            with self.subTest(ir=ir), self.assertRaises(NativeCompilerError):
                export_qiskit_circuit(replace(module, mlir=ir), compiler=self.compiler)

    def test_export_rejects_instruction_loss_and_missing_idle_wire(self):
        circuit = QuantumCircuit(2)
        circuit.z(0)
        module = imported(circuit)
        lines = module.mlir.splitlines()
        lost_gate = "\n".join(line for line in lines if '"mlirq.z"' not in line).replace("%s0_0", "%q0")
        with self.assertRaises(ExportError):
            export_qiskit_circuit(replace(module, mlir=lost_gate), compiler=self.compiler)
        lost_wire = "\n".join(line for line in lines if "%q1" not in line)
        with self.assertRaises(NativeCompilerError):
            export_qiskit_circuit(replace(module, mlir=lost_wire), compiler=self.compiler)

    def test_export_rejects_barrier_and_measurement_crossing(self):
        circuit = QuantumCircuit(1)
        circuit.z(0)
        circuit.barrier(0)
        circuit.rz(0.3, 0)
        module = imported(circuit)
        lines = module.mlir.splitlines()
        index = next(i for i, line in enumerate(lines) if '"mlirq.barrier"' in line)
        lines[index], lines[index + 1] = lines[index + 1], lines[index]
        with self.assertRaises(ExportError) as caught:
            export_qiskit_circuit(replace(module, mlir="\n".join(lines)), compiler=self.compiler)
        self.assertEqual(caught.exception.code, "export_fence_violation")

        circuit = QuantumCircuit(2, 1)
        circuit.measure(0, 0)
        circuit.measure(1, 0)
        module = imported(circuit)
        lines = module.mlir.splitlines()
        indices = [i for i, line in enumerate(lines) if '"mlirq.measure"' in line]
        lines[indices[0]], lines[indices[1]] = lines[indices[1]], lines[indices[0]]
        ir = "\n".join(lines).replace('(%m0, %m1)', '(%m1, %m0)')
        with self.assertRaises(ExportError) as caught:
            export_qiskit_circuit(replace(module, mlir=ir), compiler=self.compiler)
        self.assertEqual(caught.exception.code, "export_fence_violation")

    def test_export_rejects_non_boundary_discard_and_incomplete_classical_output(self):
        circuit = QuantumCircuit(2, 1)
        circuit.measure(0, 0)
        module = imported(circuit)
        no_result = module.mlir.replace('"mlirq.output"(%m0) : (i1)', '"mlirq.output"() : ()')
        with self.assertRaises(NativeCompilerError):
            export_qiskit_circuit(replace(module, mlir=no_result), compiler=self.compiler)
        lines = module.mlir.splitlines()
        measure = next(i for i, line in enumerate(lines) if '"mlirq.measure"' in line)
        discard = next(i for i, line in enumerate(lines) if '"mlirq.discard"' in line)
        lines[measure], lines[discard] = lines[discard], lines[measure]
        with self.assertRaises(NativeCompilerError):
            export_qiskit_circuit(replace(module, mlir="\n".join(lines)), compiler=self.compiler)

    def test_missing_or_malformed_export_file_has_structured_error(self):
        module = imported(QuantumCircuit(1))
        for contents in [None, "not JSON"]:
            def fake_run(*args, **kwargs):
                if contents is not None:
                    Path(kwargs["cwd"], "circuit.json").write_text(contents, encoding="utf-8")
                return subprocess.CompletedProcess(args[0], 0, stdout=module.mlir, stderr="diagnostic")
            with self.subTest(contents=contents), patch("mlirq_qiskit.native.subprocess.run", fake_run):
                with self.assertRaises(NativeCompilerError) as caught:
                    export_qiskit_circuit(module, compiler=self.compiler)
                self.assertEqual(caught.exception.code, "native_invalid_export")

    def test_malformed_payloads_are_rejected(self):
        circuit = QuantumCircuit(1)
        circuit.z(0)
        module = imported(circuit)
        valid = self.compiler._export(module)
        changes = [None, [], {"schema_version": 2}]
        for key, value in [("instructions", [{}]), ("schema_version", True)]:
            changed = deepcopy(valid)
            changed[key] = value
            changes.append(changed)
        for key, value in [("source_index", True), ("source_index", 8), ("qubits", [False])]:
            changed = deepcopy(valid)
            changed["instructions"][0][key] = value
            changes.append(changed)
        for payload in changes:
            with self.subTest(payload=payload), patch.object(self.compiler, "_export", return_value=payload):
                with self.assertRaises(ExportError):
                    export_qiskit_circuit(module, compiler=self.compiler)


if __name__ == "__main__":
    unittest.main()
