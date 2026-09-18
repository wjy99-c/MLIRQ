#ifndef MLIRQ_TRANSFORMS_PASSES_H
#define MLIRQ_TRANSFORMS_PASSES_H
#include <memory>
namespace mlir { class Pass; }
namespace mlirq {
std::unique_ptr<mlir::Pass> createLogicalOptPass();
std::unique_ptr<mlir::Pass> createMapIdentityPass();
std::unique_ptr<mlir::Pass> createVerifyTargetPass();
void registerMLIRQPasses();
}
#endif
