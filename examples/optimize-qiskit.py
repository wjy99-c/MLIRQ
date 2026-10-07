"""Offline T1–T8 demonstration, with a labeled synthetic post-transpile case."""
import argparse
import json
from qiskit import QuantumCircuit, transpile
from qiskit.circuit import Measure, Parameter
from qiskit.circuit.library import RZGate, ZGate
from qiskit.transpiler import Target
from mlirq_qiskit import NativeCompiler, optimize_compiled_circuit

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--mlirq-opt", default="build/bin/mlirq-opt")
args = parser.parse_args()
target = Target(num_qubits=5)
for operation in [ZGate(), RZGate(Parameter("angle")), Measure()]:
    target.add_instruction(operation)
circuit = QuantumCircuit(1, 1, global_phase=.137)
circuit.z(0); circuit.rz(.3, 0); circuit.measure(0, 0)
compiled = transpile(circuit, target=target, initial_layout=[4], optimization_level=0, seed_transpiler=17)
catalog = {"schema_version": 1, "source_url": "synthetic:offline-demo", "patterns": [
    {"id": "demo-z-rz", "backend": "demo", "gates": [
        {"gate": "z", "qubits": [4]}, {"gate": "rz", "qubits": [4], "angle": .3}]}]}
result = optimize_compiled_circuit(compiled, backend_name="demo", target=target, patterns=catalog,
                                  compiler=NativeCompiler(args.mlirq_opt))
print(json.dumps({key: result.report[key] for key in ["before", "after", "status", "output_validation"]}, indent=2))
