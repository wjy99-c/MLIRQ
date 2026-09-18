#include "mlirq/IR/MLIRQTypes.h"
#include "mlirq/IR/MLIRQDialect.h"
#include "mlir/IR/Builders.h"
#include "mlir/IR/DialectImplementation.h"
#include "llvm/ADT/TypeSwitch.h"

#define GET_TYPEDEF_CLASSES
#include "mlirq/IR/MLIRQTypes.cpp.inc"

void mlirq::MLIRQDialect::registerTypes() {
  addTypes<
#define GET_TYPEDEF_LIST
#include "mlirq/IR/MLIRQTypes.cpp.inc"
      >();
}
