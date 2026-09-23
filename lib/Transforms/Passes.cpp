#include "mlirq/Transforms/Passes.h"
#include "mlirq/IR/MLIRQOps.h"
#include "mlirq/IR/Target.h"
#include "mlir/IR/Builders.h"
#include "mlir/IR/PatternMatch.h"
#include "mlir/Pass/Pass.h"
#include "mlir/Transforms/GreedyPatternRewriteDriver.h"

using namespace mlir;
using namespace mlirq;

namespace {
template <typename GateOp>
struct CancelInversePair : OpRewritePattern<GateOp> {
  using OpRewritePattern<GateOp>::OpRewritePattern;
  LogicalResult matchAndRewrite(GateOp second, PatternRewriter &rewriter) const override {
    auto first = second->getOperand(0).template getDefiningOp<GateOp>();
    if (!first || first->getNextNode() != second.getOperation())
      return failure();
    // Treat annotations as opaque: a future QRisk-aware pass owns their fate.
    if (!first->getAttrs().empty() || !second->getAttrs().empty())
      return failure();
    for (unsigned i = 0; i < second->getNumOperands(); ++i)
      if (second->getOperand(i) != first->getResult(i) || !first->getResult(i).hasOneUse())
        return failure();
    rewriter.replaceOp(second, first->getOperands());
    rewriter.eraseOp(first);
    return success();
  }
};

struct LogicalOptPass : PassWrapper<LogicalOptPass, OperationPass<ModuleOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(LogicalOptPass)
  StringRef getArgument() const final { return "mlirq-logical-opt"; }
  StringRef getDescription() const final { return "Cancel adjacent H/H, X/X, Z/Z, and CX/CX pairs in logical circuits"; }
  void runOnOperation() override {
    for (CircuitOp circuit : getOperation().getOps<CircuitOp>()) {
      auto stage = circuit->getAttrOfType<StringAttr>("mlirq.stage");
      if (stage && stage.getValue() != "logical") {
        circuit.emitOpError("logical optimization requires the logical stage");
        signalPassFailure();
        return;
      }
    }
    RewritePatternSet patterns(&getContext());
    patterns.add<CancelInversePair<HOp>, CancelInversePair<XOp>,
                 CancelInversePair<ZOp>, CancelInversePair<CXOp>>(&getContext());
    if (failed(applyPatternsAndFoldGreedily(getOperation(), std::move(patterns))))
      signalPassFailure();
  }
};

struct MapIdentityPass : PassWrapper<MapIdentityPass, OperationPass<ModuleOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(MapIdentityPass)
  StringRef getArgument() const final { return "mlirq-map-identity"; }
  StringRef getDescription() const final { return "Map allocations in order and check target connectivity before committing"; }
  void runOnOperation() override {
    llvm::DenseMap<Operation *, int64_t> candidate;
    // Validate the complete module before changing any circuit.
    for (CircuitOp circuit : getOperation().getOps<CircuitOp>()) {
      auto stage = circuit->getAttrOfType<StringAttr>("mlirq.stage");
      if (stage && stage.getValue() != "logical") {
        circuit.emitOpError("identity mapping requires the logical stage");
        signalPassFailure();
        return;
      }
      int64_t next = 0;
      for (AllocOp alloc : circuit.getBody().front().getOps<AllocOp>())
        candidate[alloc.getOperation()] = next++;
      if (failed(verifyMappedCircuit(circuit, &candidate))) {
        signalPassFailure();
        return;
      }
    }
    Builder builder(&getContext());
    for (const auto &entry : candidate)
      entry.first->setAttr("physical", builder.getI64IntegerAttr(entry.second));
    for (CircuitOp circuit : getOperation().getOps<CircuitOp>())
      circuit->setAttr("mlirq.stage", builder.getStringAttr("architecture"));
  }
};

struct VerifyTargetPass : PassWrapper<VerifyTargetPass, OperationPass<ModuleOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(VerifyTargetPass)
  StringRef getArgument() const final { return "mlirq-verify-target"; }
  StringRef getDescription() const final { return "Validate mapped qubit capacity, uniqueness, connectivity, and direction"; }
  void runOnOperation() override {
    for (CircuitOp circuit : getOperation().getOps<CircuitOp>()) {
      auto stage = circuit->getAttrOfType<StringAttr>("mlirq.stage");
      if (!stage || stage.getValue() != "architecture") {
        circuit.emitOpError("target verification requires the architecture stage");
        signalPassFailure();
        return;
      }
      if (failed(verifyMappedCircuit(circuit))) {
        signalPassFailure();
        return;
      }
    }
  }
};
} // namespace

std::unique_ptr<Pass> mlirq::createLogicalOptPass() { return std::make_unique<LogicalOptPass>(); }
std::unique_ptr<Pass> mlirq::createMapIdentityPass() { return std::make_unique<MapIdentityPass>(); }
std::unique_ptr<Pass> mlirq::createVerifyTargetPass() { return std::make_unique<VerifyTargetPass>(); }
void mlirq::registerMLIRQPasses() {
  registerQRiskPasses();
  registerQiskitExportPass();
  PassRegistration<LogicalOptPass>();
  PassRegistration<MapIdentityPass>();
  PassRegistration<VerifyTargetPass>();
}
