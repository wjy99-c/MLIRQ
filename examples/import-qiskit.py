"""Offline T1/T2 example: the caller transpiles; MLIRQ imports and verifies."""

import argparse
from pathlib import Path

from qiskit import QuantumCircuit, transpile
from qiskit.circuit import Parameter
from qiskit.circuit.library import CXGate, RZGate, SXGate, XGate
from qiskit.transpiler import Target

from mlirq_qiskit import NativeCompiler, import_compiled_circuit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mlirq-opt", help="Native compiler path; otherwise use MLIRQ_OPT or PATH")
    args = parser.parse_args()
    target = Target(num_qubits=6)
    for gate in [RZGate(Parameter("theta")), SXGate(), XGate()]:
        target.add_instruction(gate)
    target.add_instruction(
        CXGate(),
        {(i, j): None for i in range(6) for j in range(6) if abs(i - j) == 1},
    )
    logical = QuantumCircuit(2)
    logical.h(0)
    logical.cx(0, 1)
    logical.rz(0.319, 1)

    # This is the caller's compilation step. The adapter does not repeat it.
    compiled = transpile(
        logical, target=target, initial_layout=[4, 1],
        optimization_level=1, seed_transpiler=21,
    )
    module = import_compiled_circuit(
        compiled, backend_name="synthetic-line-6", target=target,
        patterns=Path(__file__).resolve().parents[1] / "patterns/qrisk-imported.json",
    )
    # This example verifies import. The historical catalog has no patterns for
    # this synthetic backend; the separate native QRisk examples show mitigation.
    result = NativeCompiler(args.mlirq_opt).run(module)
    print(result.mlir, end="")


if __name__ == "__main__":
    main()
