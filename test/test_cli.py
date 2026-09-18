"""Native compiler regression tests, including independent statevector checks.

Python standard library only. An absent/broken compiler is a failure, not a skip.
The numerical oracle checks optimized native output, including complex phase.
"""
import argparse
import cmath
import math
from pathlib import Path
import random
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
Q = "!mlirq.qubit"
COMPILER = None


def compile_ir(source, *passes, generic=False):
    args = [str(COMPILER), "--verify-each", *passes]
    if generic:
        args.append("--mlir-print-op-generic")
    return subprocess.run(args, input=source, text=True, capture_output=True, timeout=30)


def generated_circuit(events, n=3, basis=0):
    lines = ["module {", "  mlirq.circuit @generated {"]
    wires = [f"%q{i}" for i in range(n)]
    for value in wires:
        lines.append(f'    {value} = "mlirq.alloc"() : () -> {Q}')
    sequence = [("x", (i,), None) for i in range(n) if basis & (1 << i)] + events
    for index, (gate, indices, angle) in enumerate(sequence):
        inputs = ", ".join(wires[i] for i in indices)
        outputs = [f"%s{index}_{i}" for i in range(len(indices))]
        types = ", ".join([Q] * len(indices))
        result_types = Q if len(indices) == 1 else f"({types})"
        attrs = f" {{angle = {angle:.17e} : f64}}" if gate == "rz" else ""
        lines.append(f'    {", ".join(outputs)} = "mlirq.{gate}"({inputs}){attrs} : ({types}) -> {result_types}')
        for i, output in zip(indices, outputs):
            wires[i] = output
    for value in wires:
        lines.append(f'    "mlirq.discard"({value}) : ({Q}) -> ()')
    lines += ['    "mlirq.output"() : () -> ()', "  }", "}"]
    return "\n".join(lines)


def parse_generic_unitary(text):
    """Read straight-line native generic output, retaining SSA wire identity.

    Measurement/discard must be terminal with respect to gates; this oracle
    deliberately does not claim to validate dynamic or open-system semantics.
    """
    wires, events = {}, []
    next_wire = 0
    terminal = False
    for line in text.splitlines():
        match = re.match(r'\s*(?:(.*?)\s*=\s*)?"mlirq\.(\w+)"\(([^)]*)\)(.*)', line)
        if not match:
            continue
        lhs, gate, operands, tail = match.groups()
        if gate == "circuit":
            continue
        names = []
        for result in (lhs or "").split(","):
            result = result.strip()
            if not result:
                continue
            group = re.fullmatch(r"(%[\w.$-]+):(\d+)", result)
            names.extend([f"{group[1]}#{i}" for i in range(int(group[2]))] if group else [result])
        if gate == "alloc":
            if terminal or len(names) != 1:
                raise ValueError("unexpected allocation in numerical oracle")
            wires[names[0]] = next_wire
            next_wire += 1
            continue
        if gate in ("measure", "discard", "output"):
            terminal = True
            continue
        if terminal or gate not in ("h", "x", "z", "sx", "rz", "cx", "cz", "swap"):
            raise ValueError(f"unsupported numerical-oracle operation: {gate}")
        inputs = [wires[value.strip()] for value in operands.split(",")]
        if len(names) != len(inputs):
            raise ValueError("unexpected gate result shape")
        angle = None
        if gate == "rz":
            attr = re.search(r"angle\s*=\s*([^\s,}>]+)", tail)
            if not attr:
                raise ValueError("missing Rz angle")
            angle = float(attr[1])
        events.append((gate, tuple(inputs), angle))
        wires.update(zip(names, inputs))
    return next_wire, events


def statevector(n, events, basis=0):
    state = [0j] * (1 << n)
    state[basis] = 1 + 0j
    for gate, wires, angle in events:
        a = wires[0]
        mask = 1 << a
        if gate == "cx":
            target = 1 << wires[1]
            for i in range(len(state)):
                if i & mask and not i & target:
                    state[i], state[i | target] = state[i | target], state[i]
        elif gate == "swap":
            other = 1 << wires[1]
            for i in range(len(state)):
                if i & mask and not i & other:
                    j = i ^ mask ^ other
                    state[i], state[j] = state[j], state[i]
        elif gate == "cz":
            target = 1 << wires[1]
            for i in range(len(state)):
                if i & mask and i & target:
                    state[i] = -state[i]
        elif gate == "rz":
            for i in range(len(state)):
                state[i] *= cmath.exp((0.5j if i & mask else -0.5j) * angle)
        else:
            for i in range(len(state)):
                if i & mask:
                    continue
                j = i | mask
                u, v = state[i], state[j]
                if gate == "h":
                    state[i], state[j] = (u + v) / math.sqrt(2), (u - v) / math.sqrt(2)
                elif gate == "x":
                    state[i], state[j] = v, u
                elif gate == "z":
                    state[j] = -v
                elif gate == "sx":
                    state[i], state[j] = ((1 + 1j) * u + (1 - 1j) * v) / 2, ((1 - 1j) * u + (1 + 1j) * v) / 2
                else:
                    raise ValueError(gate)
    return state


class NativeCompilerTests(unittest.TestCase):
    def ok(self, source, *passes, generic=False):
        result = compile_ir(source, *passes, generic=generic)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def bad(self, source, diagnostic, *passes):
        result = compile_ir(source, *passes)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(diagnostic, result.stderr)

    def example(self, name):
        return (ROOT / "examples" / name).read_text()

    def target(self, n=3, edges="0, 1, 1, 2", directed="false"):
        return self.example("bell-target.mlir").replace("num_qubits = 3", f"num_qubits = {n}").replace("0, 1, 1, 2", edges).replace("array<i64: >", "array<i64>").replace("directed = false", f"directed = {directed}")

    def test_roundtrip_and_bell_semantics(self):
        generic = self.ok(self.example("bell.mlir"), generic=True)
        self.ok(generic)
        n, events = parse_generic_unitary(generic)
        vector = statevector(n, events)
        expected = [1 / math.sqrt(2), 0, 0, 1 / math.sqrt(2)]
        self.assertLess(max(abs(a-b) for a,b in zip(vector,expected)), 1e-12)

    def test_inverse_pairs_are_removed(self):
        output = self.ok(self.example("optimize.mlir"), "--mlirq-logical-opt", generic=True)
        for gate in ("h", "x", "cx"):
            self.assertNotIn(f'"mlirq.{gate}"', output)
        self.assertEqual(output.count('"mlirq.alloc"'), 2)
        self.assertEqual(output.count('"mlirq.measure"'), 2)

    def test_classical_cse_does_not_merge_allocations(self):
        output = self.ok(self.example("bell.mlir"), "--canonicalize", "--cse", generic=True)
        self.assertEqual(output.count('"mlirq.alloc"'), 2)

    def test_no_cloning(self):
        self.bad(self.example("bell.mlir").replace("mlirq.cx %h, %q1", "mlirq.cx %q0, %q1"), "exactly one use")

    def test_no_abandoned_quantum_wire(self):
        self.bad("module { mlirq.circuit @bad { %q = mlirq.alloc : !mlirq.qubit\n mlirq.output } }", "exactly one use")

    def test_no_use_after_measurement(self):
        source = self.example("bell.mlir").replace("mlirq.output %a, %b", "mlirq.discard %c : !mlirq.qubit\n mlirq.output %a, %b")
        self.bad(source, "exactly one use")

    def test_cx_cannot_alias(self):
        self.bad(self.example("bell.mlir").replace("mlirq.cx %h, %q1", "mlirq.cx %h, %h"), "distinct quantum wires")

    def test_valid_mapping(self):
        output = self.ok(self.target(), "--mlirq-map-identity", "--mlirq-verify-target")
        self.assertIn('mlirq.stage = "architecture"', output)
        self.assertIn("physical = 0 : i64", output)
        self.assertIn("physical = 1 : i64", output)
        self.ok(output)

    def test_mapping_preserves_state(self):
        original = self.ok(self.target(), generic=True)
        mapped = self.ok(self.target(), "--mlirq-map-identity", "--mlirq-verify-target", generic=True)
        self.assertEqual(parse_generic_unitary(original), parse_generic_unitary(mapped))

    def test_insufficient_capacity(self):
        self.bad(self.target(n=1, edges=""), "out-of-range physical allocation", "--mlirq-map-identity")

    def test_disconnected_target(self):
        self.bad(self.target(edges="1, 2"), "connectivity or direction", "--mlirq-map-identity")

    def test_direction_is_enforced(self):
        self.bad(self.target(edges="1, 0", directed="true"), "connectivity or direction", "--mlirq-map-identity")
        self.ok(self.target(edges="1, 0"), "--mlirq-map-identity")

    def test_malformed_topology(self):
        self.bad(self.target(edges="0, 1, 2"), "must contain pairs")
        self.bad(self.target(edges="0, 3"), "out of range")
        self.bad(self.target(edges="0, 0"), "self-edge")

    def test_unknown_target_policy_rejected(self):
        self.bad(self.target().replace("directed = false", "directed = false, ancilla_policy = \"shared\""), "unsupported target field")

    def test_stage_preconditions(self):
        self.bad(self.target(), "requires the architecture stage", "--mlirq-verify-target")
        self.bad(self.target(), "requires the logical stage", "--mlirq-map-identity", "--mlirq-logical-opt")
        self.bad(self.example("bell.mlir"), "requires a mlirq.target", "--mlirq-map-identity")

    def test_mapped_ir_cannot_bypass_verifier(self):
        mapped = self.ok(self.target(), "--mlirq-map-identity")
        self.bad(mapped.replace("physical = 1 : i64", "physical = 0 : i64"), "duplicate physical allocation")
        self.bad(mapped.replace("physical = 1 : i64", "physical = 9 : i64"), "out-of-range physical allocation")

    def test_annotation_is_preserved(self):
        source = self.example("optimize.mlir").replace("mlirq.h %q0 :", 'mlirq.h %q0 {mlirq.annotation = "keep"} :')
        output = self.ok(source, "--mlirq-logical-opt", generic=True)
        self.assertEqual(output.count('"mlirq.h"'), 2)
        self.assertIn('mlirq.annotation = "keep"', output)

    def test_non_inverse_sequences_are_preserved(self):
        events = [("h", (0,), None), ("z", (0,), None), ("h", (0,), None)]
        output = self.ok(generated_circuit(events, n=1), "--mlirq-logical-opt", generic=True)
        n, actual = parse_generic_unitary(output)
        self.assertLess(abs(statevector(n, actual)[1] - 1), 1e-12)

    def test_randomized_phase_sensitive_equivalence(self):
        # Each random circuit is checked on all 8 basis inputs. This compares
        # every column of its 3-qubit unitary, not only Z-basis probabilities.
        for seed in range(8):
            rng = random.Random(seed)
            events = []
            for _ in range(18):
                gate = rng.choice(["h", "x", "z", "rz", "cx"])
                indices = tuple(rng.sample(range(3), 2 if gate == "cx" else 1))
                angle = rng.uniform(-math.pi, math.pi) if gate == "rz" else None
                events.append((gate, indices, angle))
                if gate != "rz" and rng.random() < 0.5:
                    events.append(events[-1])
            for basis in range(8):
                with self.subTest(seed=seed, basis=basis):
                    source = generated_circuit(events, basis=basis)
                    output = self.ok(source, "--mlirq-logical-opt", generic=True)
                    n, actual = parse_generic_unitary(output)
                    expected = statevector(3, events, basis)
                    obtained = statevector(n, actual)
                    self.assertEqual(n, 3)
                    self.assertLess(max(abs(a-b) for a,b in zip(expected, obtained)), 1e-10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mlirq-opt", required=True, type=Path)
    args = parser.parse_args()
    COMPILER = args.mlirq_opt.resolve()
    if not COMPILER.is_file():
        parser.error(f"native compiler not found: {COMPILER}")
    unittest.main(argv=[sys.argv[0]], verbosity=2)
