module {
  mlirq.circuit @bell {
    %q0 = mlirq.alloc : !mlirq.qubit
    %q1 = mlirq.alloc : !mlirq.qubit
    %h = mlirq.h %q0 : !mlirq.qubit
    %c, %t = mlirq.cx %h, %q1 : !mlirq.qubit, !mlirq.qubit
    %a = mlirq.measure %c : !mlirq.qubit -> i1
    %b = mlirq.measure %t : !mlirq.qubit -> i1
    mlirq.output %a, %b : i1, i1
  }
}
