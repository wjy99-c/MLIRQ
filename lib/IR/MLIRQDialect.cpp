#include "mlirq/IR/MLIRQDialect.h"
#include "mlirq/IR/MLIRQOps.h"
#include "mlirq/IR/MLIRQTypes.h"
#include "mlirq/IR/MLIRQDialect.cpp.inc"

void mlirq::MLIRQDialect::initialize() {
  addOperations<
#define GET_OP_LIST
#include "mlirq/IR/MLIRQOps.cpp.inc"
      >();
  registerTypes();
}
