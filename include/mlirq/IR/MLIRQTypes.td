#ifndef MLIRQ_TYPES_TD
#define MLIRQ_TYPES_TD
include "mlirq/IR/MLIRQDialect.td"
include "mlir/IR/AttrTypeBase.td"

def MLIRQ_Qubit : TypeDef<MLIRQ_Dialect, "Qubit"> {
  let mnemonic = "qubit";
  let summary = "One live quantum-state wire, consumed exactly once";
  let assemblyFormat = "";
}
#endif
