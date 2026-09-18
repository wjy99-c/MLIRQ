#ifndef MLIRQ_IR_TARGET_H
#define MLIRQ_IR_TARGET_H
#include "mlirq/IR/MLIRQOps.h"
#include "llvm/ADT/DenseMap.h"
#include <cstdint>
#include <set>
#include <string>
#include <utility>

namespace mlirq {
// Initial topology contract. No live calibration, pulse, or vendor claims.
struct TargetModel {
  std::string name;
  int64_t numQubits = 0;
  bool directed = false;
  std::set<std::pair<int64_t, int64_t>> coupling;
  bool supportsCX(int64_t control, int64_t target) const;
  bool supportsCZ(int64_t left, int64_t right) const;
  // A SWAP must admit CX(a,b), CX(b,a), CX(a,b), without direction correction.
  bool supportsSwap(int64_t left, int64_t right) const;
};
mlir::LogicalResult parseTarget(CircuitOp circuit, TargetModel &model);
mlir::LogicalResult verifyRoutingMetadata(CircuitOp circuit);
// Optional candidate allocations permit preflight before a mapping mutates IR.
mlir::LogicalResult verifyMappedCircuit(
    CircuitOp circuit,
    const llvm::DenseMap<mlir::Operation *, int64_t> *candidate = nullptr);
} // namespace mlirq
#endif
