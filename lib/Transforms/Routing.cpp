#include "mlirq/Transforms/Passes.h"
#include "mlirq/IR/MLIRQOps.h"
#include "mlirq/IR/Target.h"
#include "mlir/IR/Builders.h"
#include "mlir/IR/IRMapping.h"
#include "mlir/IR/OwningOpRef.h"
#include "mlir/IR/Verifier.h"
#include "mlir/Pass/Pass.h"
#include "llvm/ADT/DenseSet.h"
#include <algorithm>
#include <map>
#include <queue>
#include <vector>

using namespace mlir;
using namespace mlirq;

namespace {
using Edge = std::pair<int64_t, int64_t>;

class CircuitRouter {
public:
  CircuitRouter(CircuitOp circuit, StringRef layout)
      : circuit(circuit), layoutOption(layout), builder(circuit.getContext()) {}

  LogicalResult run() {
    auto stage = circuit->getAttrOfType<StringAttr>("mlirq.stage");
    if (stage && stage.getValue() != "logical")
      return circuit.emitOpError("routing requires the logical stage; do not run identity mapping first");
    if (failed(parseTarget(circuit, target)))
      return failure();
    size_t count = 0;
    for (Operation &op : circuit.getBody().front()) {
      if (isa<AllocOp>(op))
        ++count;
      else if (!isa<MeasureOp, DiscardOp, OutputOp>(op))
        for (NamedAttribute attr : op.getAttrs())
          if (!(isa<RzOp>(op) && attr.getName().getValue() == "angle"))
            return op.emitOpError("routing does not support annotated quantum gates");
    }
    if (count > static_cast<uint64_t>(target.numQubits))
      return circuit.emitOpError("routing exceeds target qubit capacity");
    if (layoutOption.empty()) {
      for (size_t i = 0; i < count; ++i)
        initial.push_back(i);
    } else {
      SmallVector<StringRef> fields;
      layoutOption.split(fields, ',', -1, true);
      for (StringRef field : fields) {
        int64_t index;
        if (field.trim().getAsInteger(10, index))
          return circuit.emitOpError("initial-layout must be comma-separated physical integers");
        initial.push_back(index);
      }
      if (initial.size() != count)
        return circuit.emitOpError("initial-layout must contain one index per logical allocation");
    }
    for (int64_t q : initial)
      if (q < 0 || q >= target.numQubits || !reserved.insert(q).second)
        return circuit.emitOpError("initial-layout indices must be distinct and within target capacity");
    position = initial;
    for (const Edge &edge : target.coupling) {
      if (!target.supportsSwap(edge.first, edge.second))
        continue;
      adjacent[edge.first].push_back(edge.second);
      adjacent[edge.second].push_back(edge.first);
    }
    for (auto &entry : adjacent) {
      auto &neighbors = entry.second;
      std::sort(neighbors.begin(), neighbors.end());
      neighbors.erase(std::unique(neighbors.begin(), neighbors.end()), neighbors.end());
    }

    Region replacement;
    Block *block = new Block;
    replacement.push_back(block);
    builder.setInsertionPointToEnd(block);
    IRMapping mapping;
    llvm::DenseMap<Value, size_t> logical;
    size_t nextLogical = 0;
    for (Operation &op : circuit.getBody().front()) {
      if (isa<AllocOp>(op)) {
        size_t index = nextLogical++;
        int64_t q = position[index];
        Operation *copy = builder.clone(op, mapping);
        copy->setAttr("physical", builder.getI64IntegerAttr(q));
        logical[op.getResult(0)] = index;
        allocated.insert(q);
        live[q] = copy->getResult(0);
        owner[q] = index;
        continue;
      }
      SmallVector<size_t> wires;
      for (Value operand : op.getOperands())
        if (isa<QubitType>(operand.getType()))
          wires.push_back(logical.lookup(operand));
      if (isa<CXOp, CZOp, SwapOp>(op)) {
        std::vector<Edge> swaps;
        if (!findRoute(op, position[wires[0]], position[wires[1]], swaps))
          return op.emitOpError("no legal route: disconnected, retired/reserved qubits, or unsupported CX/SWAP direction");
        for (const Edge &edge : swaps)
          insertSwap(op.getLoc(), edge.first, edge.second);
      }
      // Generated SWAPs exchange states, so original logical operands must
      // follow their new physical locations, not the old SSA successor names.
      unsigned wire = 0;
      for (Value operand : op.getOperands())
        if (isa<QubitType>(operand.getType()))
          mapping.map(operand, live.lookup(position[wires[wire++]]));
      if (isa<OutputOp>(op))
        for (size_t i = initial.size(); i < position.size(); ++i)
          builder.create<DiscardOp>(op.getLoc(), live.lookup(position[i]));
      Operation *copy = builder.clone(op, mapping);
      wire = 0;
      for (auto results : llvm::zip(op.getResults(), copy->getResults())) {
        Value oldValue = std::get<0>(results), newValue = std::get<1>(results);
        if (isa<QubitType>(oldValue.getType())) {
          size_t index = wires[wire++];
          logical[oldValue] = index;
          live[position[index]] = newValue;
        }
      }
      if (isa<MeasureOp, DiscardOp>(op))
        live.erase(position[wires[0]]);
    }
    NamedAttrList record;
    record.append("algorithm", builder.getStringAttr("bfs"));
    record.append("initial_layout", builder.getDenseI64ArrayAttr(initial));
    record.append("final_layout", builder.getDenseI64ArrayAttr(ArrayRef<int64_t>(position).take_front(initial.size())));
    record.append("auxiliary_initial_layout", builder.getDenseI64ArrayAttr(auxiliaryInitial));
    record.append("auxiliary_final_layout", builder.getDenseI64ArrayAttr(ArrayRef<int64_t>(position).drop_front(initial.size())));
    circuit.getBody().takeBody(replacement);
    circuit->setAttr("mlirq.stage", builder.getStringAttr("architecture"));
    circuit->setAttr("mlirq.routing", record.getDictionary(builder.getContext()));
    circuit->removeAttr("mlirq.qrisk.report");
    return success();
  }

private:
  bool legal(Operation &op, const Edge &state) const {
    if (isa<CXOp>(op))
      return target.supportsCX(state.first, state.second);
    if (isa<CZOp>(op))
      return target.supportsCZ(state.first, state.second);
    return target.supportsSwap(state.first, state.second);
  }

  bool usable(int64_t q) const {
    // Never reset/reuse a terminated wire or preempt a future logical allocation.
    return allocated.count(q) ? live.count(q) != 0 : !reserved.count(q);
  }

  // BFS over the two operands' physical positions. Other live states may be
  // moved by the selected SWAPs; insertSwap updates their placements as well.
  // Searching both operands avoids falsely rejecting routes that move a target.
  bool findRoute(Operation &op, int64_t left, int64_t right,
                 std::vector<Edge> &swaps) const {
    struct Predecessor { Edge state; Edge swap; };
    Edge start{left, right};
    std::map<Edge, Predecessor> visited;
    std::queue<Edge> pending;
    visited[start] = {start, {-1, -1}};
    pending.push(start);
    while (!pending.empty()) {
      Edge state = pending.front();
      pending.pop();
      if (legal(op, state)) {
        while (state != start) {
          const auto &previous = visited.at(state);
          swaps.push_back(previous.swap);
          state = previous.state;
        }
        std::reverse(swaps.begin(), swaps.end());
        return true;
      }
      for (unsigned side = 0; side < 2; ++side) {
        int64_t from = side == 0 ? state.first : state.second;
        auto neighbors = adjacent.find(from);
        if (neighbors == adjacent.end())
          continue;
        for (int64_t to : neighbors->second) {
          if (!usable(to))
            continue;
          auto moved = [from, to](int64_t q) { return q == from ? to : (q == to ? from : q); };
          Edge next{moved(state.first), moved(state.second)};
          if (visited.emplace(next, Predecessor{state, {from, to}}).second)
            pending.push(next);
        }
      }
    }
    return false;
  }

  void insertSwap(Location loc, int64_t left, int64_t right) {
    for (int64_t q : {left, right}) {
      if (allocated.count(q))
        continue;
      auto alloc = builder.create<AllocOp>(loc, QubitType::get(builder.getContext()), builder.getI64IntegerAttr(q));
      alloc->setAttr("mlirq.routing_ancilla", builder.getUnitAttr());
      allocated.insert(q);
      live[q] = alloc.getQubit();
      owner[q] = position.size();
      position.push_back(q);
      auxiliaryInitial.push_back(q);
    }
    auto swap = builder.create<SwapOp>(loc, live[left].getType(), live[right].getType(), live[left], live[right]);
    swap->setAttr("mlirq.routing_swap", builder.getUnitAttr());
    live[left] = swap.getLeftOut();
    live[right] = swap.getRightOut();
    std::swap(owner[left], owner[right]);
    position[owner[left]] = left;
    position[owner[right]] = right;
  }

  CircuitOp circuit;
  StringRef layoutOption;
  OpBuilder builder;
  TargetModel target;
  std::vector<int64_t> initial, position, auxiliaryInitial;
  std::map<int64_t, std::vector<int64_t>> adjacent;
  llvm::DenseSet<int64_t> reserved, allocated;
  llvm::DenseMap<int64_t, Value> live;
  llvm::DenseMap<int64_t, size_t> owner;
};

struct RoutePass : PassWrapper<RoutePass, OperationPass<ModuleOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(RoutePass)
  RoutePass() = default;
  RoutePass(const RoutePass &other) : PassWrapper(other) {}
  Option<std::string> initialLayout{*this, "initial-layout",
      llvm::cl::desc("Physical indices in logical allocation order (default: 0,1,...)"), llvm::cl::init("")};
  StringRef getArgument() const final { return "mlirq-route"; }
  StringRef getDescription() const final { return "Route logical circuits using shortest legal SWAP paths and record final placement"; }
  void runOnOperation() override {
    OwningOpRef<ModuleOp> working(cast<ModuleOp>(getOperation()->clone()));
    for (CircuitOp circuit : working->getOps<CircuitOp>())
      if (failed(CircuitRouter(circuit, initialLayout).run())) {
        signalPassFailure();
        return;
      }
    if (failed(verify(*working))) {
      getOperation().emitError("routed module failed independent verification; original module retained");
      signalPassFailure();
      return;
    }
    getOperation().getBodyRegion().takeBody(working->getBodyRegion());
  }
};
} // namespace

std::unique_ptr<Pass> mlirq::createRoutePass() { return std::make_unique<RoutePass>(); }
void mlirq::registerRoutingPasses() { PassRegistration<RoutePass>(); }
