#ifndef MLIRQ_OPS_TD
#define MLIRQ_OPS_TD
include "mlirq/IR/MLIRQTypes.td"
include "mlir/IR/SymbolInterfaces.td"

def MLIRQ_CircuitOp : MLIRQ_Op<"circuit", [
    IsolatedFromAbove, SingleBlock, Symbol, HasParent<"::mlir::ModuleOp">]> {
  let summary = "Closed, straight-line quantum circuit with classical outputs";
  let arguments = (ins SymbolNameAttr:$sym_name);
  let regions = (region AnyRegion:$body);
  let assemblyFormat = "$sym_name attr-dict-with-keyword $body";
  let hasRegionVerifier = 1;
}

// Deliberately no Pure trait: quantum-state operations must not be CSE'd,
// speculated, or deleted by classical optimizers without ownership reasoning.
def MLIRQ_AllocOp : MLIRQ_Op<"alloc", [HasParent<"CircuitOp">]> {
  let summary = "Allocate a fresh qubit initialized to |0>";
  let arguments = (ins OptionalAttr<I64Attr>:$physical);
  let results = (outs MLIRQ_Qubit:$qubit);
  let assemblyFormat = "attr-dict `:` type($qubit)";
  let hasVerifier = 1;
}

class MLIRQ_UnaryGate<string mnemonic> : MLIRQ_Op<mnemonic, [HasParent<"CircuitOp">]> {
  let arguments = (ins MLIRQ_Qubit:$input);
  let results = (outs MLIRQ_Qubit:$result);
  let assemblyFormat = "$input attr-dict `:` type($result)";
}
def MLIRQ_HOp : MLIRQ_UnaryGate<"h">;
def MLIRQ_XOp : MLIRQ_UnaryGate<"x">;
def MLIRQ_ZOp : MLIRQ_UnaryGate<"z">;
def MLIRQ_SXOp : MLIRQ_UnaryGate<"sx"> {
  let summary = "Positive square root of X: ((1+i) I + (1-i) X) / 2";
}

def MLIRQ_RzOp : MLIRQ_Op<"rz", [HasParent<"CircuitOp">]> {
  let arguments = (ins MLIRQ_Qubit:$input, F64Attr:$angle);
  let results = (outs MLIRQ_Qubit:$result);
  let assemblyFormat = "$input `,` $angle attr-dict `:` type($result)";
  let hasVerifier = 1;
}

def MLIRQ_CXOp : MLIRQ_Op<"cx", [HasParent<"CircuitOp">]> {
  let summary = "Controlled X; results retain control/target wire order";
  let arguments = (ins MLIRQ_Qubit:$control, MLIRQ_Qubit:$target);
  let results = (outs MLIRQ_Qubit:$control_out, MLIRQ_Qubit:$target_out);
  let assemblyFormat = "$control `,` $target attr-dict `:` type($control_out) `,` type($target_out)";
  let hasVerifier = 1;
}

def MLIRQ_CZOp : MLIRQ_Op<"cz", [HasParent<"CircuitOp">]> {
  let summary = "Controlled Z, diag(1,1,1,-1); results retain operand order";
  let arguments = (ins MLIRQ_Qubit:$control, MLIRQ_Qubit:$target);
  let results = (outs MLIRQ_Qubit:$control_out, MLIRQ_Qubit:$target_out);
  let assemblyFormat = "$control `,` $target attr-dict `:` type($control_out) `,` type($target_out)";
  let hasVerifier = 1;
}

def MLIRQ_MeasureOp : MLIRQ_Op<"measure", [HasParent<"CircuitOp">]> {
  let summary = "Terminal Z-basis measurement, consuming the quantum wire";
  let arguments = (ins MLIRQ_Qubit:$qubit);
  let results = (outs I1:$bit);
  let assemblyFormat = "$qubit attr-dict `:` type($qubit) `->` type($bit)";
}
def MLIRQ_DiscardOp : MLIRQ_Op<"discard", [HasParent<"CircuitOp">]> {
  let summary = "Explicitly trace out a quantum wire; does not assert |0>";
  let arguments = (ins MLIRQ_Qubit:$qubit);
  let assemblyFormat = "$qubit attr-dict `:` type($qubit)";
}
def MLIRQ_OutputOp : MLIRQ_Op<"output", [Terminator, HasParent<"CircuitOp">]> {
  let summary = "Return ordered classical measurement results";
  let arguments = (ins Variadic<I1>:$bits);
  let assemblyFormat = "$bits attr-dict (`:` type($bits)^)?";
}
#endif
