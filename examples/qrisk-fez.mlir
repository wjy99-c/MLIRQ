// Repeat an actual QRisk observation twice to exercise recurrence disruption.
// Pattern source: patterns/qrisk-imported.json, ibm_fez, 2026-06-17.
// The repetition is a regression harness, not the original QRisk workload.
// The target describes only the observed 3--4 interaction, not a full/current
// device configuration. Run the QRisk pass directly: this IR is already mapped.
module {
  mlirq.circuit @qrisk_fez attributes {
    mlirq.stage = "architecture",
    mlirq.target = {
      name = "ibm_fez",
      num_qubits = 156 : i64,
      coupling = array<i64: 3, 4>,
      directed = false
    }
  } {
    %q3 = mlirq.alloc {physical = 3 : i64} : !mlirq.qubit
    %q4 = mlirq.alloc {physical = 4 : i64} : !mlirq.qubit
    %s0 = mlirq.sx %q3 : !mlirq.qubit
    %c0, %t0 = mlirq.cz %s0, %q4 : !mlirq.qubit, !mlirq.qubit
    %r0 = mlirq.rz %c0, -3.141592653589793 : !mlirq.qubit
    %s1 = mlirq.sx %t0 : !mlirq.qubit
    %s2 = mlirq.sx %r0 : !mlirq.qubit
    %c1, %t1 = mlirq.cz %s2, %s1 : !mlirq.qubit, !mlirq.qubit
    %r1 = mlirq.rz %c1, -3.141592653589793 : !mlirq.qubit
    %s3 = mlirq.sx %t1 : !mlirq.qubit
    %a = mlirq.measure %r1 : !mlirq.qubit -> i1
    %b = mlirq.measure %s3 : !mlirq.qubit -> i1
    mlirq.output %a, %b : i1, i1
  }
}
