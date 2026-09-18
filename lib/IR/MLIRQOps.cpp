#include "mlirq/IR/MLIRQOps.h"
#include "mlirq/IR/MLIRQDialect.h"
#include "mlirq/IR/Target.h"
#include "mlir/IR/Builders.h"
#include "mlir/IR/OpImplementation.h"
#include "llvm/ADT/DenseSet.h"
#include <cmath>

using namespace mlir;
using namespace mlirq;

#define GET_OP_CLASSES
#include "mlirq/IR/MLIRQOps.cpp.inc"

LogicalResult CircuitOp::verifyRegions() {
  if (!getBody().hasOneBlock())
    return emitOpError("requires exactly one block");
  Block &block = getBody().front();
  if (block.getNumArguments())
    return emitOpError("does not accept block arguments in the initial dialect");
  if (block.empty() || !isa<OutputOp>(block.back()))
    return emitOpError("must end with mlirq.output");

  llvm::DenseSet<int64_t> physicalIds;
  for (Operation &op : block) {
    if (!isa<AllocOp, HOp, XOp, ZOp, RzOp, CXOp, MeasureOp, DiscardOp, OutputOp>(op))
      return op.emitOpError("is not supported in a straight-line MLIRQ circuit");
    for (Value result : op.getResults()) {
      if (!isa<QubitType>(result.getType()))
        continue;
      if (!result.hasOneUse())
        return op.emitOpError("each qubit result must have exactly one use (use mlirq.discard for an unwanted wire)");
      if (result.use_begin()->getOwner()->getBlock() != &block)
        return op.emitOpError("quantum state must remain in its circuit block");
    }
    if (auto alloc = dyn_cast<AllocOp>(op)) {
      if (auto physical = alloc->getAttrOfType<IntegerAttr>("physical")) {
        if (!physicalIds.insert(physical.getInt()).second)
          return alloc.emitOpError("duplicate physical allocation; qubit reuse is not implemented");
      }
    }
  }
  auto stageAttr = (*this)->getAttr("mlirq.stage");
  auto stage = dyn_cast_or_null<StringAttr>(stageAttr);
  if (stageAttr && (!stage || (stage.getValue() != "logical" && stage.getValue() != "architecture")))
    return emitOpError("mlirq.stage must be 'logical' or 'architecture'");
  if ((*this)->hasAttr("mlirq.target")) {
    TargetModel target;
    if (failed(parseTarget(*this, target)))
      return failure();
  }
  if (stage && stage.getValue() == "architecture")
    return verifyMappedCircuit(*this);
  if (!physicalIds.empty())
    return emitOpError("physical allocations require mlirq.stage = 'architecture'");
  return success();
}

LogicalResult AllocOp::verify() {
  auto physical = (*this)->getAttrOfType<IntegerAttr>("physical");
  if (physical && physical.getInt() < 0)
    return emitOpError("physical qubit index must be nonnegative");
  return success();
}

LogicalResult RzOp::verify() {
  if (!std::isfinite(getAngle().convertToDouble()))
    return emitOpError("angle must be finite, in radians");
  return success();
}

LogicalResult CXOp::verify() {
  if (getControl() == getTarget())
    return emitOpError("control and target must be distinct quantum wires");
  return success();
}
