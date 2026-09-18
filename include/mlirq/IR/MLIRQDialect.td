#ifndef MLIRQ_DIALECT_TD
#define MLIRQ_DIALECT_TD
include "mlir/IR/OpBase.td"

def MLIRQ_Dialect : Dialect {
  let name = "mlirq";
  let cppNamespace = "::mlirq";
  let summary = "Quantum circuits with explicit SSA state ownership";
  let useDefaultTypePrinterParser = 1;
  let extraClassDeclaration = [{ void registerTypes(); }];
}
class MLIRQ_Op<string mnemonic, list<Trait> traits = []>
    : Op<MLIRQ_Dialect, mnemonic, traits>;
#endif
