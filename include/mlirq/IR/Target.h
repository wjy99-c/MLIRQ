#ifndef MLIRQ_IR_TARGET_H
#define MLIRQ_IR_TARGET_H
#include "mlirq/IR/MLIRQOps.h"
#include "llvm/ADT/DenseMap.h"
#include <cstdint>
#include <set>
#include <utility>

namespace mlirq {
// Initial topology contract. No live calibration, pulse, or vendor claims.
struct TargetModel {
  int64_t numQubits = 0;
  bool directed = false;
  std::set<std::pair<int64_t, int64_t>> coupling;
  bool supportsCX(int64_t control, int64_t target) const;
};
mlir::LogicalResult parseTarget(CircuitOp circuit, TargetModel &model);
// Optional candidate allocations permit preflight before a mapping mutates IR.
mlir::LogicalResult verifyMappedCircuit(
    CircuitOp circuit,
    const llvm::DenseMap<mlir::Operation *, int64_t> *candidate = nullptr);
} // namespace mlirq
#endif
