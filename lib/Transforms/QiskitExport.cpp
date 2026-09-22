#include "mlirq/Transforms/Passes.h"
#include "mlirq/IR/MLIRQOps.h"
#include "mlirq/IR/Target.h"
#include "mlir/IR/Verifier.h"
#include "mlir/Pass/Pass.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/StringExtras.h"
#include "llvm/Support/FileSystem.h"
#include "llvm/Support/FormatVariadic.h"
#include "llvm/Support/JSON.h"
#include "llvm/Support/raw_ostream.h"
#include <algorithm>
#include <cmath>
#include <initializer_list>

using namespace mlir;
using namespace mlirq;

namespace {
// Export parsed operations, never reparsed assembly. Hex strings preserve every
// binary64 bit, including -0 and subnormals, independently of JSON formatting.
std::string bits(FloatAttr attr) {
  std::string value = llvm::utohexstr(
      attr.getValue().bitcastToAPInt().getZExtValue(), true);
  return std::string(16 - value.size(), '0') + value;
}

LogicalResult attributes(Operation *op,
                         std::initializer_list<StringRef> allowed) {
  for (NamedAttribute attr : op->getAttrs())
    if (std::find(allowed.begin(), allowed.end(), attr.getName().getValue()) ==
        allowed.end())
      return op->emitOpError("Qiskit export cannot preserve attribute: ")
             << attr.getName();
  return success();
}

bool isI64(IntegerAttr attr) {
  return attr && attr.getType().isInteger(64);
}

LogicalResult serialize(ModuleOp module, llvm::json::Object &payload) {
  if (failed(verify(module)) || failed(attributes(module, {})))
    return failure();
  Block &body = *module.getBody();
  if (body.getOperations().size() != 1 || !isa<CircuitOp>(body.front()))
    return module.emitError("Qiskit export requires exactly one imported circuit");
  auto circuit = cast<CircuitOp>(body.front());
  if (failed(attributes(circuit, {
          "sym_name", "mlirq.stage", "mlirq.target", "mlirq.qrisk.report",
          "mlirq.qiskit.import_version", "mlirq.qiskit.num_qubits",
          "mlirq.qiskit.num_clbits", "mlirq.qiskit.global_phase"})))
    return failure();
  auto version = circuit->getAttrOfType<IntegerAttr>("mlirq.qiskit.import_version");
  auto width = circuit->getAttrOfType<IntegerAttr>("mlirq.qiskit.num_qubits");
  auto classical = circuit->getAttrOfType<IntegerAttr>("mlirq.qiskit.num_clbits");
  auto phase = circuit->getAttrOfType<FloatAttr>("mlirq.qiskit.global_phase");
  auto stage = circuit->getAttrOfType<StringAttr>("mlirq.stage");
  TargetModel target;
  if (!isI64(version) || version.getInt() != 2 || !isI64(width) ||
      width.getInt() < 0 || !isI64(classical) || classical.getInt() < 0 ||
      !phase || !phase.getType().isF64() ||
      !std::isfinite(phase.getValueAsDouble()) ||
      !stage || stage.getValue() != "architecture")
    return circuit.emitOpError("Qiskit export requires version 2 import metadata at the architecture stage");
  if (failed(parseTarget(circuit, target)) || failed(verifyMappedCircuit(circuit)))
    return failure();
  if (width.getInt() > target.numQubits)
    return circuit.emitOpError("imported width exceeds target capacity");

  llvm::DenseMap<Value, int64_t> placement;
  llvm::DenseSet<int64_t> allocated, ids;
  SmallVector<Value> measured;
  llvm::json::Array instructions;
  bool started = false, discarded = false;
  for (Operation &op : circuit.getBody().front()) {
    if (auto alloc = dyn_cast<AllocOp>(op)) {
      if (failed(attributes(&op, {"physical"})))
        return failure();
      int64_t physical = alloc->getAttrOfType<IntegerAttr>("physical").getInt();
      if (started || physical >= width.getInt() || !allocated.insert(physical).second)
        return alloc.emitOpError("Qiskit allocations must declare input wires once, before instructions");
      placement[alloc.getQubit()] = physical;
      continue;
    }
    started = true;
    if (allocated.size() != static_cast<uint64_t>(width.getInt()))
      return circuit.emitOpError("Qiskit export requires every input wire, including idle wires");
    if (auto output = dyn_cast<OutputOp>(op)) {
      if (failed(attributes(&op, {})))
        return failure();
      if (output.getNumOperands() != measured.size() ||
          !std::equal(measured.begin(), measured.end(), output.getOperands().begin()))
        return output.emitOpError("Qiskit output must retain all measurement results in instruction order");
      continue;
    }
    if (isa<DiscardOp>(op)) {
      if (failed(attributes(&op, {})))
        return failure();
      discarded = true;
      continue;
    }
    if (discarded)
      return op.emitOpError("Qiskit discards must be trailing bookkeeping, after all instructions");
    if (!isa<HOp, XOp, ZOp, SXOp, RzOp, CXOp, CZOp, MeasureOp, BarrierOp>(op))
      return op.emitOpError("unsupported Qiskit export operation");
    auto id = op.getAttrOfType<IntegerAttr>("mlirq.qiskit.source_index");
    if (!isI64(id) || id.getInt() < 0 || !ids.insert(id.getInt()).second)
      return op.emitOpError("Qiskit instruction requires a unique nonnegative i64 source_index");

    llvm::json::Array qubits, clbits, params;
    if (auto barrier = dyn_cast<BarrierOp>(op)) {
      if (failed(attributes(&op, {"qubits", "mlirq.qiskit.source_index"})))
        return failure();
      for (int64_t qubit : barrier.getQubits())
        qubits.push_back(qubit);
    } else {
      SmallVector<int64_t> inputs;
      for (Value operand : op.getOperands()) {
        auto found = placement.find(operand);
        if (found == placement.end())
          return op.emitOpError("Qiskit export cannot resolve a physical operand");
        inputs.push_back(found->second);
        qubits.push_back(found->second);
      }
      unsigned wire = 0;
      for (Value result : op.getResults())
        if (isa<QubitType>(result.getType()))
          placement[result] = inputs[wire++];
      if (auto measure = dyn_cast<MeasureOp>(op)) {
        if (failed(attributes(&op, {"mlirq.qiskit.clbit", "mlirq.qiskit.source_index"})))
          return failure();
        auto bit = op.getAttrOfType<IntegerAttr>("mlirq.qiskit.clbit");
        if (!isI64(bit) || bit.getInt() < 0 || bit.getInt() >= classical.getInt())
          return op.emitOpError("Qiskit measurement requires an in-range i64 classical destination");
        clbits.push_back(bit.getInt());
        measured.push_back(measure.getBit());
      } else if (auto rz = dyn_cast<RzOp>(op)) {
        if (failed(attributes(&op, {"angle", "mlirq.qiskit.source_index"})))
          return failure();
        params.push_back(bits(rz.getAngleAttr()));
      } else if (failed(attributes(&op, {"mlirq.qiskit.source_index"}))) {
        return failure();
      }
    }
    instructions.push_back(llvm::json::Object{
        {"source_index", id.getInt()},
        {"name", op.getName().stripDialect()},
        {"qubits", std::move(qubits)}, {"clbits", std::move(clbits)},
        {"parameter_bits", std::move(params)}});
  }
  payload = llvm::json::Object{
      {"schema_version", 2}, {"backend", target.name},
      {"num_qubits", width.getInt()}, {"num_clbits", classical.getInt()},
      {"target_num_qubits", target.numQubits},
      {"global_phase_bits", bits(phase)}, {"instructions", std::move(instructions)}};
  return success();
}

struct QiskitExportPass : PassWrapper<QiskitExportPass, OperationPass<ModuleOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(QiskitExportPass)
  QiskitExportPass() = default;
  QiskitExportPass(const QiskitExportPass &other) : PassWrapper(other) {}
  Option<std::string> outputFile{
      *this, "output-file", llvm::cl::desc("Destination for Qiskit interchange JSON"),
      llvm::cl::init("")};
  StringRef getArgument() const final { return "mlirq-export-qiskit"; }
  StringRef getDescription() const final {
    return "Serialize verified imported operations and physical operands for Qiskit export";
  }
  void runOnOperation() override {
    llvm::json::Object payload;
    if (outputFile.empty()) {
      getOperation().emitError("Qiskit export requires output-file=<JSON path>");
      signalPassFailure();
      return;
    }
    if (failed(serialize(getOperation(), payload))) {
      signalPassFailure();
      return;
    }
    std::error_code error;
    llvm::raw_fd_ostream stream(outputFile, error, llvm::sys::fs::OF_Text);
    if (error) {
      getOperation().emitError("cannot open Qiskit export file: ") << error.message();
      signalPassFailure();
      return;
    }
    stream << llvm::formatv("{0:2}\n", llvm::json::Value(std::move(payload)));
    stream.close();
    if (stream.has_error()) {
      getOperation().emitError("cannot write Qiskit export file");
      stream.clear_error();
      signalPassFailure();
      return;
    }
    markAllAnalysesPreserved();
  }
};
} // namespace

void mlirq::registerQiskitExportPass() {
  PassRegistration<QiskitExportPass>();
}
