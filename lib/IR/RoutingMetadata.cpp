#include "mlirq/IR/Target.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/SmallVector.h"

using namespace mlir;
using namespace mlirq;

// Independently replay placement changes from the IR. This verifier does not
// call the router or trust the final permutation written by it.
LogicalResult mlirq::verifyRoutingMetadata(CircuitOp circuit) {
  Attribute raw = circuit->getAttr("mlirq.routing");
  for (Operation &op : circuit.getBody().front()) {
    for (StringRef key : {"mlirq.routing_swap", "mlirq.routing_ancilla"}) {
      Attribute marker = op.getAttr(key);
      if (!marker)
        continue;
      bool correctOp = key == "mlirq.routing_swap" ? isa<SwapOp>(op) : isa<AllocOp>(op);
      if (!raw || !isa<UnitAttr>(marker) || !correctOp)
        return op.emitOpError("routing markers require a routing record, a unit attribute, and the corresponding SWAP/allocation");
    }
  }
  if (!raw)
    return success();
  auto record = dyn_cast<DictionaryAttr>(raw);
  auto stage = circuit->getAttrOfType<StringAttr>("mlirq.stage");
  if (!record || !stage || stage.getValue() != "architecture")
    return circuit.emitOpError("mlirq.routing requires an architecture-stage dictionary");
  for (NamedAttribute attr : record) {
    StringRef key = attr.getName().getValue();
    if (key != "algorithm" && key != "initial_layout" && key != "final_layout" &&
        key != "auxiliary_initial_layout" && key != "auxiliary_final_layout")
      return circuit.emitOpError("unsupported routing field: ") << key;
  }
  auto algorithm = record.getAs<StringAttr>("algorithm");
  auto initial = record.getAs<DenseI64ArrayAttr>("initial_layout");
  auto final = record.getAs<DenseI64ArrayAttr>("final_layout");
  auto auxInitial = record.getAs<DenseI64ArrayAttr>("auxiliary_initial_layout");
  auto auxFinal = record.getAs<DenseI64ArrayAttr>("auxiliary_final_layout");
  if (!algorithm || algorithm.getValue() != "bfs" || !initial || !final ||
      !auxInitial || !auxFinal || initial.size() != final.size() ||
      auxInitial.size() != auxFinal.size())
    return circuit.emitOpError("routing requires algorithm='bfs' and equally sized initial/final layout pairs");
  TargetModel target;
  if (failed(parseTarget(circuit, target)))
    return failure();
  llvm::DenseSet<int64_t> initialSet, finalSet, auxOrigins;
  for (auto array : {initial, auxInitial})
    for (int64_t q : array.asArrayRef())
      if (q < 0 || q >= target.numQubits || !initialSet.insert(q).second)
        return circuit.emitOpError("routing initial layouts must contain distinct in-range physical indices");
  for (auto array : {final, auxFinal})
    for (int64_t q : array.asArrayRef())
      if (!initialSet.count(q) || !finalSet.insert(q).second)
        return circuit.emitOpError("routing final layouts must permute the initial physical indices");
  for (int64_t q : auxInitial.asArrayRef())
    auxOrigins.insert(q);

  llvm::DenseMap<Value, int64_t> physical;
  llvm::DenseMap<int64_t, int64_t> owner, location;
  llvm::DenseSet<int64_t> live;
  size_t logicalIndex = 0, auxiliaryIndex = 0;
  for (Operation &op : circuit.getBody().front()) {
    if (isa<AllocOp>(op)) {
      bool auxiliary = op.hasAttr("mlirq.routing_ancilla");
      auto sequence = auxiliary ? auxInitial.asArrayRef() : initial.asArrayRef();
      size_t index = auxiliary ? auxiliaryIndex++ : logicalIndex++;
      auto place = op.getAttrOfType<IntegerAttr>("physical");
      if (!place || index >= sequence.size() || place.getInt() != sequence[index])
        return op.emitOpError("physical allocation disagrees with routing initial layout");
      int64_t q = place.getInt();
      if (!live.insert(q).second || owner.count(q))
        return op.emitOpError("routing does not permit physical qubit reuse");
      physical[op.getResult(0)] = q;
      owner[q] = location[q] = q;
      continue;
    }
    SmallVector<int64_t> inputs;
    bool routingSwap = op.hasAttr("mlirq.routing_swap");
    for (Value operand : op.getOperands()) {
      if (!isa<QubitType>(operand.getType()))
        continue;
      auto found = physical.find(operand);
      if (found == physical.end() || !live.count(found->second))
        return op.emitOpError("routing operation uses a non-live physical wire");
      int64_t q = found->second;
      if (auxOrigins.count(owner[q]) && !routingSwap && !isa<DiscardOp>(op))
        return op.emitOpError("routing auxiliary states may only pass through routing SWAPs and discard");
      inputs.push_back(q);
    }
    if (routingSwap) {
      if (inputs.size() != 2 || inputs[0] == inputs[1])
        return op.emitOpError("routing SWAP requires two distinct live wires");
      std::swap(owner[inputs[0]], owner[inputs[1]]);
      location[owner[inputs[0]]] = inputs[0];
      location[owner[inputs[1]]] = inputs[1];
    }
    unsigned wire = 0;
    for (Value result : op.getResults())
      if (isa<QubitType>(result.getType()))
        physical[result] = inputs[wire++]; // SWAP outputs retain physical order.
    if (isa<MeasureOp, DiscardOp>(op))
      live.erase(inputs[0]);
  }
  if (logicalIndex != initial.asArrayRef().size() || auxiliaryIndex != auxInitial.asArrayRef().size())
    return circuit.emitOpError("routing layouts do not cover exactly the allocated wires");
  for (size_t i = 0; i < initial.asArrayRef().size(); ++i)
    if (location[initial[i]] != final[i])
      return circuit.emitOpError("routing final layout disagrees with the SWAP permutation");
  for (size_t i = 0; i < auxInitial.asArrayRef().size(); ++i)
    if (location[auxInitial[i]] != auxFinal[i])
      return circuit.emitOpError("routing auxiliary final layout disagrees with the SWAP permutation");
  return success();
}
