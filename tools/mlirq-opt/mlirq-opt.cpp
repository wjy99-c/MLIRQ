#include "mlirq/IR/MLIRQDialect.h"
#include "mlirq/Transforms/Passes.h"
#include "mlir/IR/DialectRegistry.h"
#include "mlir/Tools/mlir-opt/MlirOptMain.h"
#include "mlir/Transforms/Passes.h"

int main(int argc, char **argv) {
  mlirq::registerMLIRQPasses();
  mlir::registerTransformsPasses();
  mlir::DialectRegistry registry;
  registry.insert<mlirq::MLIRQDialect>();
  return mlir::asMainReturnCode(mlir::MlirOptMain(argc, argv, "MLIRQ quantum compiler core\n", registry));
}
