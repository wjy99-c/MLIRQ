"""QRisk pass contracts and independent, phase-sensitive equivalence checks."""
import argparse
import json
from pathlib import Path
import random
import re
import sys
import tempfile
import unittest

import test_cli as oracle

ROOT = Path(__file__).resolve().parents[1]
TOY = ROOT / "test" / "fixtures" / "qrisk-toy.json"
IMPORTED = ROOT / "patterns" / "qrisk-imported.json"
BACKEND = "toy_backend_alpha"


def circuit(events, backend=BACKEND, n=3):
    edges = ", ".join(str(q) for a in range(n) for b in range(a + 1, n) for q in (a, b))
    coupling = f"array<i64: {edges}>" if edges else "array<i64>"
    attrs = (f'attributes {{mlirq.target = {{name = "{backend}", '
             f'num_qubits = {n} : i64, coupling = {coupling}, directed = false}}}}')
    return oracle.generated_circuit(events, n=n).replace("@generated {", f"@generated {attrs} {{")


def pattern(events, ident="test-pattern", backend=BACKEND, angles=False):
    gates = []
    for gate, qubits, angle in events:
        spec = {"gate": gate, "qubits": list(qubits)}
        if angles and gate == "rz":
            spec["angle"] = angle
        gates.append(spec)
    return {"id": ident, "backend": backend, "gates": gates}


def integer(output, key):
    found = re.search(rf"\b{key} = (\d+) : i64", output)
    if not found:
        raise AssertionError(f"missing {key} in {output}")
    return int(found[1])


class QRiskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mlirq-qrisk-")
        self.addCleanup(self.temp.cleanup)

    def catalog(self, patterns):
        path = Path(self.temp.name) / "catalog.json"
        path.write_text(json.dumps({"schema_version": 1, "source_url": "unknown", "patterns": patterns}))
        return path

    def run_pass(self, source, catalog=TOY, mode="mitigate", mapped=False):
        passes = [] if mapped else ["--mlirq-map-identity"]
        passes += [f"--mlirq-qrisk-{mode}=patterns-file={catalog}", "--mlirq-verify-target"]
        result = oracle.compile_ir(source, *passes, generic=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def equivalent(self, events, output, n=3):
        actual_n, actual = oracle.parse_generic_unitary(output)
        self.assertEqual(actual_n, n)
        for basis in range(1 << n):
            with self.subTest(basis=basis):
                expected = oracle.statevector(n, events, basis)
                obtained = oracle.statevector(n, actual, basis)
                self.assertLess(max(abs(a - b) for a, b in zip(expected, obtained)), 1e-10)

    def test_toy_recurrences_and_spectator(self):
        repeated = [("rz", (0,), .7), ("cx", (0, 1), None), ("x", (1,), None)]
        events = repeated[:1] + [("h", (2,), None)] + repeated[1:] + repeated
        scanned = self.run_pass(circuit(events), mode="scan")
        self.assertEqual(integer(scanned, "before_total"), 2)
        self.assertEqual(oracle.parse_generic_unitary(scanned)[1], events)
        output = self.run_pass(circuit(events))
        self.assertEqual(integer(output, "before_total"), 2)
        self.assertEqual(integer(output, "after_total"), 0)
        self.assertEqual(integer(output, "rewrites"), 2)
        self.assertIn('status = "eliminated"', output)
        self.equivalent(events, output)
        again = self.run_pass(output, mapped=True)
        self.assertEqual(integer(again, "rewrites"), 0)
        self.assertEqual(oracle.parse_generic_unitary(output), oracle.parse_generic_unitary(again))

    def test_exact_backend_isolation(self):
        events = [("cx", (0, 1), None), ("x", (1,), None)]
        alpha = self.run_pass(circuit(events))
        beta = self.run_pass(circuit(events, backend="toy_backend_beta"))
        other = self.run_pass(circuit(events, backend="toy_backend_beta_extra"))
        self.assertEqual(integer(alpha, "before_total"), 0)
        self.assertEqual(integer(beta, "before_total"), 1)
        self.assertEqual(integer(beta, "after_total"), 0)
        self.assertIn('status = "no_backend_patterns"', other)
        self.assertNotIn('id = "toy-beta-cx-x"', alpha)
        self.equivalent(events, beta)

    def test_physical_indices_and_cx_roles(self):
        events = [("z", (0,), None), ("cx", (0, 1), None)]
        source = self.run_pass(circuit(events), mode="scan")
        self.assertEqual(integer(source, "before_total"), 1)
        # Permute actual physical allocations while retaining SSA names/order.
        swapped = source.replace("physical = 0 : i64", "physical = 9 : i64").replace(
            "physical = 1 : i64", "physical = 0 : i64").replace("physical = 9 : i64", "physical = 1 : i64")
        output = self.run_pass(swapped, mode="scan", mapped=True)
        self.assertEqual(integer(output, "before_total"), 0)
        reversed_cx = [("z", (0,), None), ("cx", (1, 0), None)]
        self.assertEqual(integer(self.run_pass(circuit(reversed_cx)), "before_total"), 0)

    def test_gate_on_pattern_wire_breaks_match(self):
        events = [("z", (0,), None), ("h", (1,), None), ("cx", (0, 1), None)]
        self.assertEqual(integer(self.run_pass(circuit(events)), "before_total"), 0)
        # An interaction with an outside wire also interrupts the projection.
        events[1] = ("cx", (1, 2), None)
        self.assertEqual(integer(self.run_pass(circuit(events)), "before_total"), 0)

    def test_all_supported_commutations(self):
        pairs = [
            [("z", (0,), None), ("rz", (0,), .2)],
            [("rz", (0,), .2), ("rz", (0,), -.9)],
            [("rz", (0,), .2), ("cx", (0, 1), None)],
            [("z", (0,), None), ("cx", (0, 1), None)],
            [("cx", (0, 1), None), ("x", (1,), None)],
            [("cx", (0, 1), None), ("cx", (0, 2), None)],
            [("cx", (0, 2), None), ("cx", (1, 2), None)],
            [("sx", (0,), None), ("x", (0,), None)],
            [("cz", (0, 1), None), ("rz", (1,), .4)],
            [("cz", (0, 1), None), ("cz", (1, 2), None)],
            [("cx", (0, 1), None), ("sx", (1,), None)],
        ]
        for pair in pairs:
            with self.subTest(pair=pair):
                catalog = self.catalog([pattern(pair, angles=True)])
                events = [("h", (0,), None), ("rz", (1,), .35)] + pair + [("h", (2,), None)]
                output = self.run_pass(circuit(events), catalog)
                self.assertEqual(integer(output, "before_total"), 1)
                self.assertEqual(integer(output, "after_total"), 0)
                self.equivalent(events, output)

    def test_noncommuting_pairs_remain_reported(self):
        pairs = [
            [("x", (0,), None), ("cx", (0, 1), None)],
            [("rz", (1,), .3), ("cx", (0, 1), None)],
            [("h", (0,), None), ("z", (0,), None)],
            [("cx", (0, 1), None), ("cx", (1, 2), None)],
            [("sx", (0,), None), ("cz", (0, 1), None)],
            [("sx", (0,), None), ("rz", (0,), .4)],
            [("cx", (0, 1), None), ("cz", (1, 2), None)],
        ]
        for pair in pairs:
            with self.subTest(pair=pair):
                output = self.run_pass(circuit(pair), self.catalog([pattern(pair)]))
                self.assertEqual(integer(output, "before_total"), 1)
                self.assertEqual(integer(output, "after_total"), 1)
                self.assertIn('status = "blocked"', output)
                self.assertEqual(oracle.parse_generic_unitary(output)[1], pair)

    def test_rewrite_cannot_create_another_active_pattern(self):
        pair = [("z", (0,), None), ("cx", (0, 1), None)]
        catalog = self.catalog([pattern(pair, "forward"), pattern(list(reversed(pair)), "reverse")])
        output = self.run_pass(circuit(pair), catalog)
        self.assertEqual(integer(output, "before_total"), 1)
        self.assertEqual(integer(output, "after_total"), 1)
        self.assertEqual(integer(output, "rewrites"), 0)
        self.assertIn('status = "blocked"', output)

    def test_overlapping_matches_are_counted(self):
        pair = [("z", (0,), None)] * 2
        output = self.run_pass(circuit(pair + pair[:1]), self.catalog([pattern(pair)]))
        self.assertEqual(integer(output, "before_total"), 2)
        self.assertEqual(integer(output, "after_total"), 2)
        self.assertEqual(output.count('pattern_id = "test-pattern"'), 2)

    def test_disjoint_reordering_is_not_mitigation(self):
        pair = [("h", (0,), None), ("z", (1,), None)]
        output = self.run_pass(circuit(pair), self.catalog([pattern(pair)]))
        self.assertEqual(integer(output, "before_total"), 1)
        self.assertEqual(integer(output, "after_total"), 1)
        self.assertEqual(integer(output, "rewrites"), 0)

    def test_actual_qrisk_catalog_on_physical_qubits(self):
        data = json.loads(IMPORTED.read_text())
        self.assertEqual(data["source_url"], "https://github.com/qzydustin/qrisk")
        self.assertEqual({p["backend"] for p in data["patterns"]}, {"ibm_fez", "ibm_kingston", "ibm_marrakesh"})
        for item in data["patterns"]:
            with self.subTest(backend=item["backend"]):
                physical = sorted({q for gate in item["gates"] for q in gate["qubits"]})
                events = [(g["gate"], tuple(physical.index(q) for q in g["qubits"]), g.get("angle"))
                          for g in item["gates"]] * 2
                source = circuit(events, backend=item["backend"], n=len(physical))
                mapped = oracle.compile_ir(source, "--mlirq-map-identity", generic=True)
                self.assertEqual(mapped.returncode, 0, mapped.stderr)
                source = mapped.stdout
                for logical, qubit in enumerate(physical):
                    source = source.replace(f"physical = {logical} : i64", f"physical = {qubit} : i64")
                pairs = ", ".join(str(q) for a in physical for b in physical if a < b for q in (a, b))
                source = re.sub(r"coupling = array<i64[^>]*>", f"coupling = array<i64: {pairs}>", source)
                source = re.sub(r"num_qubits = \d+ : i64", "num_qubits = 156 : i64", source)
                output = self.run_pass(source, IMPORTED, mapped=True)
                self.assertEqual(integer(output, "before_total"), 2)
                self.assertEqual(integer(output, "after_total"), 0)
                self.equivalent(events, output, n=len(physical))
                other = source.replace(f'name = "{item["backend"]}"', 'name = "unrelated_backend"')
                self.assertEqual(integer(self.run_pass(other, IMPORTED, mapped=True), "before_total"), 0)

    def test_qrisk_parameter_tolerance_does_not_modify_angles(self):
        pair = [("rz", (0,), 1.570796), ("cz", (0, 1), None)]
        spec = dict(pattern(pair, angles=True), angle_tolerance=1e-5)
        path = self.catalog([spec])
        pair[0] = ("rz", (0,), 1.5707963267948966)
        output = self.run_pass(circuit(pair), path)
        self.assertEqual(integer(output, "before_total"), 1)
        self.assertEqual(integer(output, "after_total"), 0)
        self.assertIn(pair[0], oracle.parse_generic_unitary(output)[1])
        self.equivalent(pair, output)
        pair[0] = ("rz", (0,), 1.571)
        self.assertEqual(integer(self.run_pass(circuit(pair), path), "before_total"), 0)

    def test_cz_connectivity_and_symmetry(self):
        events = [("cz", (1, 0), None)]
        source = circuit(events, n=2).replace("directed = false", "directed = true")
        self.run_pass(source)
        source = source.replace("array<i64: 0, 1>", "array<i64>")
        result = oracle.compile_ir(source, "--mlirq-map-identity")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CZ violates target connectivity", result.stderr)

    def test_sx_and_cz_phase_conventions(self):
        cases = [
            ([("sx", (0,), None)] * 2, [("x", (0,), None)]),
            ([("cz", (0, 1), None)], [("h", (1,), None), ("cx", (0, 1), None), ("h", (1,), None)]),
        ]
        for events, equivalent in cases:
            output = self.run_pass(circuit(events))
            self.equivalent(equivalent, output)

    def test_real_example_recurrence_and_terminal_measurements(self):
        source = (ROOT / "examples" / "qrisk-fez.mlir").read_text()
        before = oracle.compile_ir(source, generic=True)
        self.assertEqual(before.returncode, 0, before.stderr)
        n, events = oracle.parse_generic_unitary(before.stdout)
        output = self.run_pass(source, IMPORTED, mapped=True)
        self.assertEqual(integer(output, "before_total"), 2)
        self.assertEqual(integer(output, "after_total"), 0)
        self.assertEqual(output.count('"mlirq.measure"'), 2)
        self.equivalent(events, output, n=n)

    def test_rotation_angle_filter(self):
        pair = [("rz", (0,), .25), ("cx", (0, 1), None)]
        catalog = self.catalog([pattern(pair, angles=True)])
        self.assertEqual(integer(self.run_pass(circuit(pair), catalog), "before_total"), 1)
        pair[0] = ("rz", (0,), .5)
        self.assertEqual(integer(self.run_pass(circuit(pair), catalog), "before_total"), 0)

    def test_annotation_and_measurement_barriers(self):
        pair = [("z", (0,), None), ("cx", (0, 1), None)]
        source = circuit(pair).replace('"mlirq.z"(%q0)', '"mlirq.z"(%q0) {mlirq.annotation = "calibrated"}')
        output = self.run_pass(source)
        self.assertEqual(integer(output, "after_total"), 1)
        self.assertIn('mlirq.annotation = "calibrated"', output)
        # A terminal measurement on a spectator must not be crossed either.
        source = circuit(pair)
        measure = '    %m = "mlirq.measure"(%q2) : (!mlirq.qubit) -> i1\n'
        source = source.replace('    %s1_0,', measure + '    %s1_0,')
        source = source.replace('    "mlirq.discard"(%q2) : (!mlirq.qubit) -> ()\n', '')
        source = source.replace('"mlirq.output"() : () -> ()', '"mlirq.output"(%m) : (i1) -> ()')
        output = self.run_pass(source)
        self.assertEqual(integer(output, "before_total"), 1)
        self.assertEqual(integer(output, "rewrites"), 0)
        self.assertEqual(output.count('"mlirq.measure"'), 1)

    def test_terminal_measurements_keep_output_order(self):
        events = [("h", (0,), None), ("z", (0,), None), ("cx", (0, 1), None)]
        source = circuit(events)
        source = re.sub(r'"mlirq.discard"\((%[^)]+)\) : \(!mlirq.qubit\) -> \(\)',
                        lambda m: f'%m{m.start()} = "mlirq.measure"({m[1]}) : (!mlirq.qubit) -> i1', source)
        measured = re.findall(r'(%m\d+) = "mlirq.measure"', source)
        source = source.replace('"mlirq.output"() : () -> ()',
                                f'"mlirq.output"({", ".join(reversed(measured))}) : (i1, i1, i1) -> ()')
        before = oracle.compile_ir(source, "--mlirq-map-identity", generic=True)
        self.assertEqual(before.returncode, 0, before.stderr)
        output = self.run_pass(source)
        self.assertEqual(integer(output, "after_total"), 0)
        self.equivalent(events, output)
        terminal = lambda s: [line.strip() for line in s.splitlines() if '"mlirq.measure"' in line or '"mlirq.output"' in line]
        # Gate reorder can change SSA numbering; compare output operand roles.
        def roles(text):
            lines = terminal(text)
            names = [line.split(" = ")[0] for line in lines[:-1]]
            return [names.index(name) for name in re.findall(r'%[\w#]+', lines[-1])]
        self.assertEqual(roles(before.stdout), roles(output))

    def test_random_context_equivalence(self):
        pair = [("z", (0,), None), ("cx", (0, 1), None)]
        for seed in range(12):
            rng = random.Random(seed)
            events = []
            for index in range(24):
                gate = rng.choice(["h", "x", "z", "rz", "cx"])
                indices = tuple(rng.sample(range(3), 2 if gate == "cx" else 1))
                events.append((gate, indices, rng.uniform(-3, 3) if gate == "rz" else None))
                if index % 8 == 0:
                    events.extend(pair)
            with self.subTest(seed=seed):
                output = self.run_pass(circuit(events))
                self.assertGreaterEqual(integer(output, "before_total"), 3)
                # A local swap may just shift a hit (e.g. Z Z CX). The pass
                # reports that local minimum instead of claiming elimination.
                self.assertLess(integer(output, "after_total"), integer(output, "before_total"))
                self.equivalent(events, output)
                rescanned = self.run_pass(output, mode="scan", mapped=True)
                self.assertEqual(integer(rescanned, "before_total"), integer(output, "after_total"))

    def test_stage_and_missing_catalog_errors(self):
        source = circuit([])
        for passes, error in [
            ([f"--mlirq-qrisk-scan=patterns-file={TOY}"], "requires the architecture stage"),
            (["--mlirq-map-identity", "--mlirq-qrisk-mitigate"], "requires patterns-file"),
            (["--mlirq-map-identity", "--mlirq-qrisk-scan=patterns-file=/nonexistent/qrisk.json"], "cannot read QRisk catalog"),
        ]:
            result = oracle.compile_ir(source, *passes)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(error, result.stderr)

    def test_invalid_catalogs_fail_clearly(self):
        pair = [("z", (0,), None), ("cx", (0, 1), None)]
        base = pattern(pair)
        invalid = [
            ([base, base], "duplicate QRisk pattern id"),
            ([dict(base, backend="*")], "exact backend name"),
            ([dict(base, gates=[{"gate": "unknown", "qubits": [0]}] * 2)], "correct qubit arity"),
            ([dict(base, gates=[{"gate": "z", "qubits": [-1]}] * 2)], "nonnegative integers"),
            ([dict(base, gates=[{"gate": "cx", "qubits": [0, 0]}] * 2)], "operands must be distinct"),
            ([dict(base, gates=[{"gate": "z", "qubits": [0], "angle": .5}] * 2)], "finite number on an rz"),
            ([dict(base, gates=[{"gate": "z", "qubits": [9]}] * 2)], "outside this backend's capacity"),
            ([dict(base, typo=True)], "unsupported QRisk catalog field"),
        ]
        for patterns, diagnostic in invalid:
            with self.subTest(diagnostic=diagnostic):
                path = self.catalog(patterns)
                result = oracle.compile_ir(circuit(pair), "--mlirq-map-identity", f"--mlirq-qrisk-scan=patterns-file={path}")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(diagnostic, result.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mlirq-opt", required=True, type=Path)
    args = parser.parse_args()
    oracle.COMPILER = args.mlirq_opt.resolve()
    if not oracle.COMPILER.is_file():
        parser.error(f"native compiler not found: {oracle.COMPILER}")
    unittest.main(argv=[sys.argv[0]], verbosity=2)
