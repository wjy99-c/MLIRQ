"""Native routing tests with an independent permutation-aware numerical oracle."""
import argparse
from pathlib import Path
import random
import re
import sys
import unittest

import test_cli as oracle

ROOT = Path(__file__).resolve().parents[1]


def circuit(events, n=3, capacity=None, edges=None, directed=False, backend="routing-test"):
    capacity = n if capacity is None else capacity
    edges = [(i, i + 1) for i in range(capacity - 1)] if edges is None else edges
    flat = ", ".join(str(q) for edge in edges for q in edge)
    coupling = f"array<i64: {flat}>" if flat else "array<i64>"
    attrs = (f'attributes {{mlirq.target = {{name = "{backend}", num_qubits = {capacity} : i64, '
             f'coupling = {coupling}, directed = {str(directed).lower()}' + '}}')
    return oracle.generated_circuit(events, n=n).replace("@generated {", f"@generated {attrs} {{")


def layout(text, key):
    match = re.search(rf"\b{key} = array<i64(?:: ([^>]*))?>", text)
    if not match:
        raise AssertionError(f"missing routing {key}")
    return [int(q) for q in match[1].split(",")] if match[1] else []


def allocation_sites(text):
    return [int(re.search(r"physical = (\d+)", line)[1])
            for line in text.splitlines() if '"mlirq.alloc"' in line]


def terminal_output_sites(text):
    """Track physical SSA wires separately from logical-state permutation."""
    sites, bits = {}, {}
    next_site = 0
    for line in text.splitlines():
        match = re.match(r'\s*(?:(.*?)\s*=\s*)?"mlirq\.(\w+)"\(([^)]*)\)(.*)', line)
        if not match:
            continue
        lhs, gate, operands, tail = match.groups()
        names = []
        for result in (lhs or "").split(","):
            result = result.strip()
            if not result:
                continue
            group = re.fullmatch(r"(%[\w.$-]+):(\d+)", result)
            names.extend([f"{group[1]}#{i}" for i in range(int(group[2]))] if group else [result])
        inputs = [s.strip() for s in operands.split(",") if s.strip()]
        if gate == "alloc":
            physical = re.search(r"physical = (\d+)", tail)
            sites[names[0]] = int(physical[1]) if physical else next_site
            next_site += 1
        elif gate == "measure":
            bits[names[0]] = sites[inputs[0]]
        elif gate == "output":
            return [bits[value] for value in inputs]
        elif gate not in ("circuit", "discard"):
            sites.update(zip(names, [sites[value] for value in inputs]))
    raise AssertionError("missing output")


class RoutingTests(unittest.TestCase):
    def ok(self, source, *passes):
        result = oracle.compile_ir(source, *passes, generic=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def route(self, source, initial=None, *after):
        option = "--mlirq-route"
        if initial is not None:
            option += "=initial-layout=" + ",".join(str(q) for q in initial)
        return self.ok(source, option, *after, "--mlirq-verify-target")

    def bad(self, source, error, *passes):
        result = oracle.compile_ir(source, *passes)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(error, result.stderr)

    def equivalent(self, events, output, n):
        actual_n, actual = oracle.parse_generic_unitary(output)
        physical = allocation_sites(output)
        initial, final = layout(output, "initial_layout"), layout(output, "final_layout")
        self.assertEqual(len(initial), n)
        self.assertEqual(len(final), n)
        self.assertEqual(actual_n, len(physical))
        slots = {site: bit for bit, site in enumerate(physical)}
        for basis in range(1 << n):
            with self.subTest(basis=basis):
                input_index = sum(((basis >> i) & 1) << slots[q] for i, q in enumerate(initial))
                obtained = oracle.statevector(actual_n, actual, input_index)
                logical = oracle.statevector(n, events, basis)
                expected = [0j] * (1 << actual_n)
                for logical_index, amplitude in enumerate(logical):
                    output_index = sum(((logical_index >> i) & 1) << slots[q] for i, q in enumerate(final))
                    expected[output_index] = amplitude
                # Compare the entire state, including zero-valued auxiliary
                # states. This is not postselection or a probability-only test.
                self.assertLess(max(abs(a - b) for a, b in zip(expected, obtained)), 1e-10)

    def test_non_neighbor_cx_and_final_permutation(self):
        events = [("h", (0,), None), ("rz", (1,), .3), ("cx", (0, 2), None)]
        source = circuit(events)
        self.bad(source, "routing is required", "--mlirq-map-identity")
        output = self.route(source)
        self.assertEqual(output.count('"mlirq.swap"'), 1)
        self.assertEqual(layout(output, "final_layout"), [1, 0, 2])
        self.equivalent(events, output, 3)
        self.ok(output)  # Verify the serialized artifact independently of routing.

    def test_initial_layout_and_unused_routing_qubits(self):
        events = [("sx", (0,), None), ("cx", (0, 1), None), ("rz", (1,), -.23)]
        output = self.route(circuit(events, n=2, capacity=4), [3, 0])
        self.assertEqual(layout(output, "initial_layout"), [3, 0])
        self.assertEqual(output.count('"mlirq.swap"'), 2)
        self.assertEqual(layout(output, "auxiliary_initial_layout"), [2, 1])
        self.assertEqual(output.count("mlirq.routing_ancilla"), 2)
        self.equivalent(events, output, 2)

    def test_reverse_cx_and_logical_swap_semantics(self):
        events = [("sx", (0,), None), ("cx", (2, 0), None),
                  ("swap", (0, 2), None), ("cz", (2, 1), None), ("rz", (0,), .9)]
        output = self.route(circuit(events))
        self.assertGreater(output.count('"mlirq.swap"'), 1)
        self.assertEqual(output.count('"mlirq.swap"'), output.count("mlirq.routing_swap") + 1)
        self.equivalent(events, output, 3)

    def test_swap_definition_against_three_cx(self):
        events = [("swap", (0, 1), None)]
        output = self.route(circuit(events, n=2))
        equivalent = [("cx", (0, 1), None), ("cx", (1, 0), None), ("cx", (0, 1), None)]
        self.assertEqual(layout(output, "final_layout"), [0, 1])
        self.assertNotIn("mlirq.routing_swap", output)
        self.equivalent(equivalent, output, 2)

    def test_noop_routing_and_empty_circuit(self):
        events = [("cx", (0, 1), None), ("cz", (1, 2), None)]
        output = self.route(circuit(events))
        self.assertNotIn('"mlirq.swap"', output)
        self.assertEqual(layout(output, "initial_layout"), layout(output, "final_layout"))
        self.equivalent(events, output, 3)
        empty = self.route(circuit([], n=0, capacity=1, edges=[]))
        self.assertEqual(layout(empty, "final_layout"), [])

    def test_ring_tie_break_is_deterministic(self):
        events = [("cx", (0, 2), None), ("cx", (3, 1), None)]
        edges = [(0, 1), (1, 2), (2, 3), (3, 0)]
        a = self.route(circuit(events, n=4, edges=edges))
        b = self.route(circuit(events, n=4, edges=list(reversed(edges))))
        self.assertEqual(oracle.parse_generic_unitary(a), oracle.parse_generic_unitary(b))
        self.assertEqual(layout(a, "final_layout"), layout(b, "final_layout"))
        self.equivalent(events, a, 4)

    def test_directed_target_and_target_operand_movement(self):
        cases = [
            ([(0, 1), (1, 0), (1, 2)], [1, 0, 2]),
            ([(0, 1), (1, 2), (2, 1)], [0, 2, 1]),
        ]
        events = [("cx", (0, 2), None)]
        for edges, expected in cases:
            with self.subTest(edges=edges):
                output = self.route(circuit(events, edges=edges, directed=True))
                self.assertEqual(layout(output, "final_layout"), expected)
                self.equivalent(events, output, 3)

    def test_disconnected_and_asymmetric_routes_fail(self):
        self.bad(circuit([("cx", (0, 2), None)], edges=[(0, 1)]), "no legal route", "--mlirq-route")
        self.bad(circuit([("cx", (1, 0), None)], n=2, directed=True), "no legal route", "--mlirq-route")
        self.bad(circuit([("cx", (0, 2), None)], directed=True), "no legal route", "--mlirq-route")
        self.bad(circuit([("swap", (0, 1), None)], n=2, directed=True), "no legal route", "--mlirq-route")

    def test_invalid_initial_layout_and_stage(self):
        source = circuit([])
        for option, diagnostic in [("0,1", "one index per"), ("0,0,1", "distinct"),
                                   ("0,1,3", "within target"), ("0,-1,2", "within target"),
                                   ("0,,2", "comma-separated"), ("a,b,c", "comma-separated")]:
            self.bad(source, diagnostic, f"--mlirq-route=initial-layout={option}")
        self.bad(self.route(source), "requires the logical stage", "--mlirq-route")
        self.bad(circuit([], n=3, capacity=2), "exceeds target", "--mlirq-route")

    def test_final_permutation_cannot_be_forged(self):
        output = self.route(circuit([("cx", (0, 2), None)]))
        corrupt = output.replace("final_layout = array<i64: 1, 0, 2>", "final_layout = array<i64: 0, 1, 2>")
        self.bad(corrupt, "disagrees with the SWAP permutation")
        self.bad(output.replace("mlirq.routing_swap", "some_annotation"), "disagrees with the SWAP permutation")
        duplicate = output.replace("final_layout = array<i64: 1, 0, 2>", "final_layout = array<i64: 0, 0, 2>")
        self.bad(duplicate, "must permute")
        self.bad(output.replace("initial_layout = array<i64: 0, 1, 2>", "initial_layout = array<i64: 1, 0, 2>"), "allocation disagrees")

    def test_independent_swap_topology_and_marker_checks(self):
        source = circuit([("swap", (0, 1), None)], n=2)
        output = self.ok(source, "--mlirq-map-identity")
        self.bad(output.replace("directed = false", "directed = true"), "SWAP requires connectivity")
        marked = source.replace('"mlirq.swap"(%q0, %q1)', '"mlirq.swap"(%q0, %q1) {mlirq.routing_swap}')
        self.bad(marked, "routing markers require")
        aliased = source.replace('"mlirq.swap"(%q0, %q1)', '"mlirq.swap"(%q0, %q0)')
        self.bad(aliased, "distinct quantum wires")

    def test_auxiliary_permutation_and_zero_state_contract(self):
        events = [("cx", (0, 1), None)]
        output = self.route(circuit(events, n=2, capacity=3), [0, 2])
        self.equivalent(events, output, 2)
        bad = output.replace("auxiliary_final_layout = array<i64: 0>", "auxiliary_final_layout = array<i64: 1>")
        self.bad(bad, "must permute")
        # Applying an X to the auxiliary state must invalidate the record,
        # even though the ordinary quantum SSA ownership remains well formed.
        discard = re.findall(r'"mlirq.discard"\((%[^)]+)\)', output)[-1]
        injected = output.replace(f'"mlirq.discard"({discard})',
            f'%injected = "mlirq.x"({discard}) : (!mlirq.qubit) -> !mlirq.qubit\n    "mlirq.discard"(%injected)')
        self.bad(injected, "auxiliary states may only")

    def test_ordered_measurements_follow_original_logical_wires(self):
        source = (ROOT / "examples" / "routing-line.mlir").read_text()
        source = source.replace("mlirq.output %a, %b, %d", "mlirq.output %d, %a, %b")
        original = self.ok(source)
        output = self.route(source)
        final = layout(output, "final_layout")
        self.assertEqual(terminal_output_sites(output), [final[q] for q in terminal_output_sites(original)])
        n, events = oracle.parse_generic_unitary(original)
        self.equivalent(events, output, n)

    def test_retired_wire_is_not_reused_for_routing(self):
        events = [("cx", (0, 2), None)]
        source = circuit(events)
        discard = '    "mlirq.discard"(%q1) : (!mlirq.qubit) -> ()\n'
        source = source.replace(discard, "").replace('    %s0_0,', discard + '    %s0_0,')
        self.bad(source, "no legal route", "--mlirq-route")

    def test_future_allocation_is_not_preempted(self):
        events = [("cx", (0, 1), None)]
        source = circuit(events)
        alloc = '    %q2 = "mlirq.alloc"() : () -> !mlirq.qubit\n'
        source = source.replace(alloc, "").replace('    "mlirq.discard"(%s0_0)', alloc + '    "mlirq.discard"(%s0_0)')
        # The third logical wire will own site 1, so it cannot be an auxiliary
        # for the earlier operation between sites 0 and 2.
        self.bad(source, "no legal route", "--mlirq-route=initial-layout=0,2,1")

    def test_opaque_quantum_annotations_fail_explicitly(self):
        source = circuit([("h", (0,), None)]).replace('"mlirq.h"(%q0)', '"mlirq.h"(%q0) {calibration = "pinned"}')
        self.bad(source, "does not support annotated", "--mlirq-route")

    def test_qrisk_after_routing_uses_new_physical_sites(self):
        events = [("cx", (0, 2), None), ("z", (1,), None), ("cx", (1, 0), None)]
        source = circuit(events, backend="toy_backend_alpha")
        option = f'--mlirq-qrisk-mitigate=patterns-file={ROOT / "test/fixtures/qrisk-toy.json"}'
        output = self.route(source, None, option)
        self.assertIn("before_total = 1 : i64", output)
        self.assertIn("after_total = 0 : i64", output)
        self.assertEqual(layout(output, "final_layout"), [1, 0, 2])
        self.equivalent(events, output, 3)

    def test_randomized_line_and_ring_equivalence(self):
        for seed in range(12):
            rng = random.Random(seed)
            events = []
            for _ in range(24):
                gate = rng.choice(["h", "x", "z", "sx", "rz", "cx", "cz", "swap"])
                wires = tuple(rng.sample(range(4), 2 if gate in ("cx", "cz", "swap") else 1))
                events.append((gate, wires, rng.uniform(-3, 3) if gate == "rz" else None))
            initial = rng.sample(range(4), 4)
            edges = [(0, 1), (1, 2), (2, 3)] + ([(3, 0)] if seed % 2 else [])
            with self.subTest(seed=seed):
                output = self.route(circuit(events, n=4, edges=edges), initial)
                self.equivalent(events, output, 4)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mlirq-opt", required=True, type=Path)
    args = parser.parse_args()
    oracle.COMPILER = args.mlirq_opt.resolve()
    if not oracle.COMPILER.is_file():
        parser.error(f"native compiler not found: {oracle.COMPILER}")
    unittest.main(argv=[sys.argv[0]], verbosity=2)
