module {
  mlirq.circuit @inverse_pairs {
    %q0 = mlirq.alloc : !mlirq.qubit
    %q1 = mlirq.alloc : !mlirq.qubit
    %h0 = mlirq.h %q0 : !mlirq.qubit
    %h1 = mlirq.h %h0 : !mlirq.qubit
    %x0 = mlirq.x %h1 : !mlirq.qubit
    %x1 = mlirq.x %x0 : !mlirq.qubit
    %c0, %t0 = mlirq.cx %x1, %q1 : !mlirq.qubit, !mlirq.qubit
    %c1, %t1 = mlirq.cx %c0, %t0 : !mlirq.qubit, !mlirq.qubit
    %a = mlirq.measure %c1 : !mlirq.qubit -> i1
    %b = mlirq.measure %t1 : !mlirq.qubit -> i1
    mlirq.output %a, %b : i1, i1
  }
}
