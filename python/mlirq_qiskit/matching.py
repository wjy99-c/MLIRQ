"""Independent final-circuit oracle; does not inspect native IR or reports."""


def count_patterns(circuit, catalog, backend_name, *, matching="scoped"):
    """Count overlapping windows in a native-validated schema-1 catalog.

    Scope projection retains gates and effects that touch a pattern wire.
    Global matching is an evaluation ablation retaining every instruction.
    This function expects the supported, already validated circuit subset.
    """
    if matching not in {"scoped", "global"}:
        raise ValueError("matching must be scoped or global")
    trace = [(item.operation.name,
              tuple(circuit.find_bit(q).index for q in item.qubits),
              tuple(float(p) for p in item.operation.params)) for item in circuit.data]
    counts = {}
    for pattern in catalog["patterns"]:
        if pattern["backend"] != backend_name:
            continue
        gates = pattern["gates"]
        scope = {q for gate in gates for q in gate["qubits"]}
        projected = [event for event in trace if matching == "global" or scope.intersection(event[1])]
        tolerance = pattern.get("angle_tolerance", 0.0)
        def equal(event, gate):
            return (event[0] == gate["gate"] and event[1] == tuple(gate["qubits"])
                    and ("angle" not in gate or
                         len(event[2]) == 1 and abs(event[2][0] - gate["angle"]) <= tolerance))
        counts[pattern["id"]] = sum(
            all(equal(event, gate) for event, gate in zip(projected[start:], gates))
            for start in range(len(projected) - len(gates) + 1))
    return counts
