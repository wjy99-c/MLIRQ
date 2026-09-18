// CX(0,2) is illegal on this line before routing. One physical SWAP makes it
// legal; final_layout maps the original logical wires to [1,0,2].
module {
  mlirq.circuit @route_line attributes {
    mlirq.target = {
      name = "synthetic-line-3",
      num_qubits = 3 : i64,
      coupling = array<i64: 0, 1, 1, 2>,
      directed = false
    }
  } {
    %q0 = mlirq.alloc : !mlirq.qubit
    %q1 = mlirq.alloc : !mlirq.qubit
    %q2 = mlirq.alloc : !mlirq.qubit
    %h = mlirq.h %q0 : !mlirq.qubit
    %c, %t = mlirq.cx %h, %q2 : !mlirq.qubit, !mlirq.qubit
    %a = mlirq.measure %c : !mlirq.qubit -> i1
    %b = mlirq.measure %q1 : !mlirq.qubit -> i1
    %d = mlirq.measure %t : !mlirq.qubit -> i1
    mlirq.output %a, %b, %d : i1, i1, i1
  }
}
