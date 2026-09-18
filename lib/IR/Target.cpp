#include "mlirq/IR/Target.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/SmallVector.h"

using namespace mlir;
using namespace mlirq;

bool TargetModel::supportsCX(int64_t control, int64_t target) const {
  return coupling.count({control, target}) ||
         (!directed && coupling.count({target, control}));
}

bool TargetModel::supportsCZ(int64_t left, int64_t right) const {
  return coupling.count({left, right}) || coupling.count({right, left});
}

bool TargetModel::supportsSwap(int64_t left, int64_t right) const {
  return supportsCX(left, right) && supportsCX(right, left);
}

LogicalResult mlirq::parseTarget(CircuitOp circuit, TargetModel &model) {
  model = TargetModel{};
  auto target = circuit->getAttrOfType<DictionaryAttr>("mlirq.target");
  if (!target)
    return circuit.emitOpError("requires a mlirq.target dictionary");
  auto count = target.getAs<IntegerAttr>("num_qubits");
  auto edges = target.getAs<DenseI64ArrayAttr>("coupling");
  auto directed = target.getAs<BoolAttr>("directed");
  auto name = target.getAs<StringAttr>("name");
  if (!count || !count.getType().isInteger(64) || count.getInt() <= 0 ||
      !edges || !directed || !name || name.getValue().empty())
    return circuit.emitOpError("target requires a nonempty name, positive i64 num_qubits, coupling array<i64>, and directed boolean");
  // Reject misspelled/unsupported fields rather than silently ignoring policies.
  for (NamedAttribute attr : target) {
    StringRef key = attr.getName().getValue();
    if (key != "name" && key != "num_qubits" && key != "coupling" && key != "directed")
      return circuit.emitOpError("unsupported target field: ") << key;
  }
  model.name = name.getValue().str();
  model.numQubits = count.getInt();
  model.directed = directed.getValue();
  auto values = edges.asArrayRef();
  if (values.size() % 2)
    return circuit.emitOpError("coupling must contain pairs of physical qubit indices");
  for (size_t i = 0; i < values.size(); i += 2) {
    int64_t a = values[i], b = values[i + 1];
    if (a < 0 || b < 0 || a >= model.numQubits || b >= model.numQubits || a == b)
      return circuit.emitOpError("coupling endpoint is out of range or a self-edge");
    model.coupling.insert({a, b});
  }
  return success();
}

LogicalResult mlirq::verifyMappedCircuit(
    CircuitOp circuit, const llvm::DenseMap<Operation *, int64_t> *candidate) {
  TargetModel target;
  if (failed(parseTarget(circuit, target)))
    return failure();
  llvm::DenseMap<Value, int64_t> placement;
  llvm::DenseSet<int64_t> allocated;
  for (Operation &op : circuit.getBody().front()) {
    if (auto alloc = dyn_cast<AllocOp>(op)) {
      int64_t physical = -1;
      if (candidate) {
        auto it = candidate->find(&op);
        if (it != candidate->end())
          physical = it->second;
      } else if (auto attr = op.getAttrOfType<IntegerAttr>("physical")) {
        physical = attr.getInt();
      }
      if (physical < 0 || physical >= target.numQubits)
        return alloc.emitOpError("missing or out-of-range physical allocation");
      if (!allocated.insert(physical).second)
        return alloc.emitOpError("duplicate physical allocation");
      placement[alloc->getResult(0)] = physical;
      continue;
    }
    llvm::SmallVector<int64_t> inputs;
    for (Value operand : op.getOperands()) {
      if (!isa<QubitType>(operand.getType()))
        continue;
      auto found = placement.find(operand);
      if (found == placement.end())
        return op.emitOpError("quantum operand has no physical allocation");
      inputs.push_back(found->second);
    }
    if (isa<CXOp>(op) && !target.supportsCX(inputs[0], inputs[1]))
      return op.emitOpError("CX violates target connectivity or direction; routing is required");
    // CZ is symmetric even when the coupling data describes directed CX edges.
    if (isa<CZOp>(op) && !target.supportsCZ(inputs[0], inputs[1]))
      return op.emitOpError("CZ violates target connectivity; routing is required");
    if (isa<SwapOp>(op) && !target.supportsSwap(inputs[0], inputs[1]))
      return op.emitOpError("SWAP requires connectivity supporting CX in both directions");
    unsigned wire = 0;
    for (Value result : op.getResults()) {
      if (isa<QubitType>(result.getType()))
        placement[result] = inputs[wire++];
    }
  }
  return success();
}
