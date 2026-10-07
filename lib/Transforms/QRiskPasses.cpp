#include "mlirq/Transforms/Passes.h"
#include "mlirq/IR/MLIRQOps.h"
#include "mlirq/IR/Target.h"
#include "mlir/IR/Builders.h"
#include "mlir/IR/OwningOpRef.h"
#include "mlir/IR/Verifier.h"
#include "mlir/Pass/Pass.h"
#include "llvm/Support/JSON.h"
#include "llvm/Support/MemoryBuffer.h"
#include "llvm/Support/FileSystem.h"
#include "llvm/Support/FormatVariadic.h"
#include "llvm/Support/raw_ostream.h"
#include <chrono>
#include <algorithm>
#include <cmath>
#include <initializer_list>
#include <numeric>
#include <optional>
#include <set>
#include <string>
#include <vector>

using namespace mlir;
using namespace mlirq;

namespace {
struct GateSpec {
  std::string gate;
  std::vector<int64_t> qubits;
  std::optional<double> angle;
};

struct Pattern {
  std::string id;
  std::string backend;
  std::vector<GateSpec> gates;
  std::set<int64_t> scope;
  double angleTolerance = 0;
};

struct Catalog {
  std::string source;
  std::vector<Pattern> patterns;
};

bool isGate(StringRef gate) {
  return gate == "h" || gate == "x" || gate == "z" ||
         gate == "sx" || gate == "rz" || gate == "cx" || gate == "cz";
}

LogicalResult checkFields(const llvm::json::Object &object,
                          std::initializer_list<StringRef> allowed,
                          ModuleOp module) {
  for (const auto &field : object) {
    StringRef key = field.first;
    if (std::find(allowed.begin(), allowed.end(), key) == allowed.end())
      return module.emitError("unsupported QRisk catalog field: ") << key;
  }
  return success();
}

LogicalResult loadCatalog(StringRef path, ModuleOp module, Catalog &catalog) {
  if (path.empty())
    return module.emitError("QRisk requires patterns-file=<local JSON catalog>");
  auto buffer = llvm::MemoryBuffer::getFile(path);
  if (!buffer)
    return module.emitError("cannot read QRisk catalog '") << path << "': "
                                                            << buffer.getError().message();
  auto value = llvm::json::parse((*buffer)->getBuffer());
  if (!value)
    return module.emitError("invalid QRisk JSON: ") << llvm::toString(value.takeError());
  auto *root = value->getAsObject();
  if (!root)
    return module.emitError("QRisk catalog must be a JSON object");
  if (failed(checkFields(*root, {"schema_version", "source_url", "description", "patterns"}, module)))
    return failure();
  auto version = root->getInteger("schema_version");
  auto source = root->getString("source_url");
  auto *patterns = root->getArray("patterns");
  if (!version || *version != 1 || !source || source->empty() || !patterns)
    return module.emitError("QRisk catalog requires schema_version=1, nonempty source_url, and a patterns array");
  if (root->get("description") && !root->getString("description"))
    return module.emitError("QRisk description must be a string");
  catalog.source = source->str();
  std::set<std::string> ids;
  for (const llvm::json::Value &item : *patterns) {
    auto *object = item.getAsObject();
    if (!object)
      return module.emitError("each QRisk pattern must be an object");
    if (failed(checkFields(*object, {"id", "backend", "description", "gates", "angle_tolerance", "provenance"}, module)))
      return failure();
    auto id = object->getString("id");
    auto backend = object->getString("backend");
    auto *gates = object->getArray("gates");
    if (!id || id->empty() || !backend || backend->empty() ||
        backend->contains('*') || !gates || gates->size() < 2)
      return module.emitError("QRisk pattern requires an id, exact backend name (no wildcard), and at least two gates");
    if (!ids.insert(id->str()).second)
      return module.emitError("duplicate QRisk pattern id: ") << *id;
    if (object->get("description") && !object->getString("description"))
      return module.emitError("QRisk pattern description must be a string");
    Pattern pattern;
    pattern.id = id->str();
    pattern.backend = backend->str();
    if (object->get("angle_tolerance")) {
      auto tolerance = object->getNumber("angle_tolerance");
      if (!tolerance || !std::isfinite(*tolerance) || *tolerance < 0 || *tolerance > 1e-5)
        return module.emitError("QRisk angle_tolerance must be between zero and 1e-5 radians");
      pattern.angleTolerance = *tolerance;
    }
    if (object->get("provenance") && !object->getObject("provenance"))
      return module.emitError("QRisk provenance must be an object");
    for (const llvm::json::Value &entry : *gates) {
      auto *step = entry.getAsObject();
      if (!step)
        return module.emitError("each QRisk gate must be an object");
      if (failed(checkFields(*step, {"gate", "qubits", "angle"}, module)))
        return failure();
      auto gate = step->getString("gate");
      auto *qubits = step->getArray("qubits");
      if (!gate || !isGate(*gate) || !qubits ||
          qubits->size() != ((*gate == "cx" || *gate == "cz") ? 2u : 1u))
        return module.emitError("QRisk gate must be h/x/z/sx/rz/cx/cz with the correct qubit arity");
      GateSpec spec;
      spec.gate = gate->str();
      for (const llvm::json::Value &qubit : *qubits) {
        auto index = qubit.getAsInteger();
        if (!index || *index < 0)
          return module.emitError("QRisk physical qubit indices must be nonnegative integers");
        spec.qubits.push_back(*index);
        pattern.scope.insert(*index);
      }
      if (spec.qubits.size() == 2 && spec.qubits[0] == spec.qubits[1])
        return module.emitError("QRisk two-qubit operands must be distinct");
      if (step->get("angle")) {
        auto angle = step->getNumber("angle");
        if (*gate != "rz" || !angle || !std::isfinite(*angle))
          return module.emitError("QRisk angle must be a finite number on an rz gate");
        spec.angle = *angle;
      }
      pattern.gates.push_back(std::move(spec));
    }
    catalog.patterns.push_back(std::move(pattern));
  }
  return success();
}

struct Event : GateSpec {
  Operation *op = nullptr;
  bool movable = false;
};
using Trace = std::vector<Event>;

// Every operation is represented, including allocations and terminal effects.
// A pattern ignores disjoint wires; a rewrite still cannot cross an effect.
LogicalResult buildTrace(CircuitOp circuit, Trace &trace) {
  llvm::DenseMap<Value, int64_t> placement;
  for (Operation &op : circuit.getBody().front()) {
    Event event;
    event.op = &op;
    event.gate = op.getName().stripDialect().str();
    if (isa<AllocOp>(op)) {
      auto physical = op.getAttrOfType<IntegerAttr>("physical");
      if (!physical)
        return op.emitOpError("QRisk requires physical allocations");
      event.qubits.push_back(physical.getInt());
      placement[op.getResult(0)] = physical.getInt();
    } else if (auto barrier = dyn_cast<BarrierOp>(op)) {
      event.qubits.assign(barrier.getQubits().begin(), barrier.getQubits().end());
    } else {
      for (Value operand : op.getOperands()) {
        if (!isa<QubitType>(operand.getType()))
          continue;
        auto found = placement.find(operand);
        if (found == placement.end())
          return op.emitOpError("QRisk cannot resolve a physical quantum wire");
        event.qubits.push_back(found->second);
      }
      unsigned wire = 0;
      for (Value result : op.getResults()) {
        if (isa<QubitType>(result.getType())) {
          if (wire >= event.qubits.size())
            return op.emitOpError("unsupported QRisk wire-changing operation");
          placement[result] = event.qubits[wire++];
        }
      }
    }
    if (auto rz = dyn_cast<RzOp>(op))
      event.angle = rz.getAngle().convertToDouble();
    event.movable = isGate(event.gate);
    for (NamedAttribute attr : op.getAttrs()) {
      StringRef key = attr.getName().getValue();
      auto index = dyn_cast<IntegerAttr>(attr.getValue());
      bool sourceIndex = key == "mlirq.qiskit.source_index" && index &&
                         index.getType().isInteger(64) && index.getInt() >= 0;
      if (!(isa<RzOp>(op) && key == "angle") && !sourceIndex)
        event.movable = false; // Timing, calibration, and opaque annotations are barriers.
    }
    trace.push_back(std::move(event));
  }
  return success();
}

struct Occurrence {
  size_t pattern;
  std::vector<size_t> steps;
};
using Counts = std::vector<int64_t>;

std::vector<Occurrence> findMatches(const Trace &trace,
                                    const std::vector<const Pattern *> &patterns, bool global = false) {
  std::vector<Occurrence> matches;
  for (size_t p = 0; p < patterns.size(); ++p) {
    const Pattern &pattern = *patterns[p];
    std::vector<size_t> projected;
    for (size_t i = 0; i < trace.size(); ++i)
      if ((global && (isGate(trace[i].gate) || trace[i].gate == "measure" ||
                      trace[i].gate == "barrier")) ||
          (!global && std::any_of(trace[i].qubits.begin(), trace[i].qubits.end(),
                      [&](int64_t q) { return pattern.scope.count(q); })))
        projected.push_back(i);
    for (size_t start = 0; start + pattern.gates.size() <= projected.size(); ++start) {
      bool matched = true;
      for (size_t j = 0; j < pattern.gates.size(); ++j) {
        const auto &expected = pattern.gates[j];
        const auto &actual = trace[projected[start + j]];
        if (expected.gate != actual.gate || expected.qubits != actual.qubits ||
            (expected.angle && (!actual.angle ||
              std::abs(*expected.angle - *actual.angle) > pattern.angleTolerance))) {
          matched = false;
          break;
        }
      }
      if (matched)
        matches.push_back({p, std::vector<size_t>(projected.begin() + start,
                                                projected.begin() + start + pattern.gates.size())});
    }
  }
  return matches;
}

Counts countMatches(const std::vector<Occurrence> &matches, size_t patterns) {
  Counts counts(patterns, 0);
  for (const auto &match : matches)
    ++counts[match.pattern];
  return counts;
}

int64_t total(const Counts &counts) {
  return std::accumulate(counts.begin(), counts.end(), int64_t{0});
}

// The JSON file describes observations, never executable rewrite rules.
// These identities commute exactly, including phase, for all input states.
bool commutes(const Event &a, const Event &b, bool diagonalOnly = false) {
  if (!a.movable || !b.movable)
    return false;
  bool disjoint = std::none_of(a.qubits.begin(), a.qubits.end(), [&](int64_t q) {
    return std::find(b.qubits.begin(), b.qubits.end(), q) != b.qubits.end();
  });
  if (disjoint)
    return true;
  auto diagonal = [](const Event &event) {
    return event.gate == "z" || event.gate == "rz" || event.gate == "cz";
  };
  if (diagonal(a) && diagonal(b))
    return true;
  if (diagonalOnly)
    return false;
  if (a.qubits.size() == 1 && b.qubits.size() == 1)
    return a.gate == b.gate ||
           ((a.gate == "x" || a.gate == "sx") && (b.gate == "x" || b.gate == "sx"));
  if (a.gate == "cx" && b.gate == "cx")
    return a.qubits[0] != b.qubits[1] && b.qubits[0] != a.qubits[1];
  const Event &cx = a.gate == "cx" ? a : b;
  const Event &single = a.gate == "cx" ? b : a;
  return cx.gate == "cx" && single.qubits.size() == 1 &&
         ((diagonal(single) && single.qubits[0] == cx.qubits[0]) ||
          ((single.gate == "x" || single.gate == "sx") && single.qubits[0] == cx.qubits[1]));
}

bool improves(const Counts &candidate, const Counts &current, bool totalOnly) {
  if (total(candidate) >= total(current))
    return false;
  for (size_t i = 0; i < current.size(); ++i)
    if (!totalOnly && candidate[i] > current[i])
      return false;
  return true;
}

struct SearchOptions {
  bool global = false, totalOnly = false, diagonalOnly = false, localOnly = false;
  int64_t maxCandidates = 0, maxRewrites = 0;
};

struct Plan {
  CircuitOp circuit;
  std::string backend;
  Trace order;
  std::vector<const Pattern *> patterns;
  std::vector<Occurrence> matches;
  Counts before;
  Counts after;
  int64_t rewrites = 0, candidates = 0, legalCandidates = 0, scans = 1;
  bool budgetExhausted = false;
  double searchSeconds = 0, scanSeconds = 0;
  SearchOptions options;
};

void mitigate(Plan &plan) {
  while (!plan.matches.empty()) {
    if (plan.options.maxRewrites && plan.rewrites >= plan.options.maxRewrites) {
      plan.budgetExhausted = true;
      return;
    }
    bool accepted = false;
    std::vector<Occurrence> acceptedMatches;
    // Restart matching after every accepted change. Overlapping occurrences
    // and all patterns for this backend participate in the acceptance check.
    for (const Occurrence &match : plan.matches) {
      for (size_t first = 0; !accepted && first < match.steps.size(); ++first) {
        for (size_t last = first + 1; !accepted && last < match.steps.size(); ++last) {
          size_t left = match.steps[first], right = match.steps[last];
          // Swapping disjoint gates alone changes the textual serialization,
          // not a shared quantum timeline. Never count that as mitigation.
          if (std::none_of(plan.order[left].qubits.begin(), plan.order[left].qubits.end(),
                           [&](int64_t q) {
                             const auto &qubits = plan.order[right].qubits;
                             return std::find(qubits.begin(), qubits.end(), q) != qubits.end();
                           }))
            continue;
          for (unsigned direction = 0; direction < (right == left + 1 ? 1u : 2u); ++direction) {
            if (plan.options.maxCandidates && plan.candidates >= plan.options.maxCandidates) {
              plan.budgetExhausted = true;
              return;
            }
            ++plan.candidates;
            bool legal = true;
            for (size_t i = left; i < right; ++i)
              if (!commutes(plan.order[direction == 0 ? right : left],
                            plan.order[direction == 0 ? i : i + 1], plan.options.diagonalOnly)) {
                legal = false;
                break;
              }
            if (!legal)
              continue;
            ++plan.legalCandidates;
            Trace candidate = plan.order;
            if (direction == 0)
              std::rotate(candidate.begin() + left, candidate.begin() + right,
                          candidate.begin() + right + 1);
            else
              std::rotate(candidate.begin() + left, candidate.begin() + left + 1,
                          candidate.begin() + right + 1);
            ++plan.scans;
            auto scanStart = std::chrono::steady_clock::now();
            auto matches = findMatches(candidate, plan.patterns, plan.options.global);
            plan.scanSeconds += std::chrono::duration<double>(
                std::chrono::steady_clock::now() - scanStart).count();
            auto counts = countMatches(matches, plan.patterns.size());
            if (plan.options.localOnly ? counts[match.pattern] >= plan.after[match.pattern]
                                       : !improves(counts, plan.after, plan.options.totalOnly))
              continue;
            plan.order = std::move(candidate);
            plan.after = std::move(counts);
            acceptedMatches = std::move(matches);
            ++plan.rewrites;
            accepted = true;
            break;
          }
        }
      }
      if (accepted)
        break;
    }
    if (!accepted)
      break;
    plan.matches = std::move(acceptedMatches);
    // Defaults strictly decrease total occurrences. Local-only evaluation
    // ablations instead require an explicit finite candidate budget.
  }
}

LogicalResult applyOrder(Plan &plan) {
  Block &block = plan.circuit.getBody().front();
  llvm::DenseMap<int64_t, Value> live;
  for (Event &event : plan.order) {
    Operation *op = event.op;
    op->moveBefore(&block, block.end());
    if (isa<AllocOp>(op)) {
      live[event.qubits[0]] = op->getResult(0);
      continue;
    }
    size_t wire = 0;
    for (OpOperand &operand : op->getOpOperands()) {
      if (!isa<QubitType>(operand.get().getType()))
        continue;
      auto found = live.find(event.qubits[wire++]);
      if (found == live.end())
        return op->emitOpError("QRisk reorder crossed a quantum lifetime boundary");
      operand.set(found->second);
    }
    wire = 0;
    for (Value result : op->getResults())
      if (isa<QubitType>(result.getType()))
        live[event.qubits[wire++]] = result;
    if (isa<MeasureOp, DiscardOp>(op))
      live.erase(event.qubits[0]);
  }
  return success();
}

void attachReport(Plan &plan, const Catalog &catalog, bool transform) {
  Builder builder(plan.circuit.getContext());
  SmallVector<Attribute> counts, matches;
  for (size_t i = 0; i < plan.patterns.size(); ++i) {
    NamedAttrList item;
    item.append("id", builder.getStringAttr(plan.patterns[i]->id));
    item.append("before", builder.getI64IntegerAttr(plan.before[i]));
    item.append("after", builder.getI64IntegerAttr(plan.after[i]));
    counts.push_back(item.getDictionary(builder.getContext()));
  }
  for (const auto &occurrence : plan.matches) {
    NamedAttrList item;
    SmallVector<int64_t> steps(occurrence.steps.begin(), occurrence.steps.end());
    item.append("pattern_id", builder.getStringAttr(plan.patterns[occurrence.pattern]->id));
    item.append("operation_indices", builder.getDenseI64ArrayAttr(steps));
    matches.push_back(item.getDictionary(builder.getContext()));
  }
  StringRef status = "no_matches";
  if (plan.patterns.empty())
    status = "no_backend_patterns";
  else if (total(plan.after))
    status = !transform ? "matched" :
        (total(plan.after) > total(plan.before) ? "increased" :
         (plan.rewrites && total(plan.after) == total(plan.before) ? "unchanged" :
          (plan.rewrites ? "partial" : "blocked")));
  else if (total(plan.before))
    status = "eliminated";
  NamedAttrList report;
  report.append("backend", builder.getStringAttr(plan.backend));
  report.append("source_url", builder.getStringAttr(catalog.source));
  report.append("mode", builder.getStringAttr(transform ? "mitigate" : "scan"));
  report.append("before_total", builder.getI64IntegerAttr(total(plan.before)));
  report.append("after_total", builder.getI64IntegerAttr(total(plan.after)));
  report.append("rewrites", builder.getI64IntegerAttr(plan.rewrites));
  report.append("status", builder.getStringAttr(status));
  report.append("pattern_counts", builder.getArrayAttr(counts));
  report.append("matches", builder.getArrayAttr(matches));
  plan.circuit->setAttr("mlirq.qrisk.report", report.getDictionary(builder.getContext()));
}

llvm::json::Object jsonReport(const Plan &plan, bool transform) {
  llvm::json::Array counts;
  for (size_t i = 0; i < plan.patterns.size(); ++i)
    counts.push_back(llvm::json::Object{{"id", plan.patterns[i]->id},
                                     {"before", plan.before[i]}, {"after", plan.after[i]}});
  auto status = plan.circuit->getAttrOfType<DictionaryAttr>("mlirq.qrisk.report")
                    .getAs<StringAttr>("status").getValue();
  StringRef reason = plan.budgetExhausted ? "budget" :
      (plan.matches.empty() ? "no_matches" : (transform ? "no_improving_move" : "scan"));
  return llvm::json::Object{
      {"backend", plan.backend}, {"status", status}, {"termination_reason", reason},
      {"before_total", total(plan.before)}, {"after_total", total(plan.after)},
      {"rewrites", plan.rewrites}, {"candidates", plan.candidates},
      {"legal_candidates", plan.legalCandidates}, {"scans", plan.scans},
      {"search_seconds", plan.searchSeconds}, {"scan_seconds", plan.scanSeconds},
      {"pattern_counts", std::move(counts)},
      {"options", llvm::json::Object{{"matching", plan.options.global ? "global" : "scoped"},
                  {"acceptance", plan.options.localOnly ? "local" :
                                 (plan.options.totalOnly ? "total" : "componentwise")},
                  {"rules", plan.options.diagonalOnly ? "diagonal" : "all"},
                  {"max_candidates", plan.options.maxCandidates},
                  {"max_rewrites", plan.options.maxRewrites}}}};
}

LogicalResult runQRisk(ModuleOp module, StringRef path, bool transform,
                       StringRef reportFile, SearchOptions options = {}) {
  if (options.maxCandidates < 0 || options.maxRewrites < 0)
    return module.emitError("QRisk budgets must be nonnegative (zero means unlimited)");
  if (options.localOnly && (!options.maxCandidates || options.totalOnly))
    return module.emitError("local-only requires a finite candidate budget and excludes total-only");
  Catalog catalog;
  if (failed(loadCatalog(path, module, catalog)))
    return failure();
  // Work on a clone so a failed preflight or final verifier leaves the entire
  // input module intact. No live hardware is contacted by this pass.
  OwningOpRef<ModuleOp> working(cast<ModuleOp>(module->clone()));
  std::vector<Plan> plans;
  for (CircuitOp circuit : working->getOps<CircuitOp>()) {
    auto stage = circuit->getAttrOfType<StringAttr>("mlirq.stage");
    if (!stage || stage.getValue() != "architecture")
      return circuit.emitOpError("QRisk requires the architecture stage after physical mapping");
    TargetModel target;
    if (failed(parseTarget(circuit, target)) || failed(verifyMappedCircuit(circuit)))
      return failure();
    Plan plan;
    plan.options = options;
    plan.circuit = circuit;
    plan.backend = target.name;
    for (const Pattern &pattern : catalog.patterns) {
      if (pattern.backend != target.name)
        continue;
      if (*pattern.scope.rbegin() >= target.numQubits)
        return circuit.emitOpError("QRisk pattern '") << pattern.id << "' has physical qubits outside this backend's capacity";
      plan.patterns.push_back(&pattern);
    }
    if (failed(buildTrace(circuit, plan.order)))
      return failure();
    auto scanStart = std::chrono::steady_clock::now();
    plan.matches = findMatches(plan.order, plan.patterns, options.global);
    plan.scanSeconds = std::chrono::duration<double>(
        std::chrono::steady_clock::now() - scanStart).count();
    plan.before = plan.after = countMatches(plan.matches, plan.patterns.size());
    auto start = std::chrono::steady_clock::now();
    if (transform)
      mitigate(plan);
    plan.searchSeconds = std::chrono::duration<double>(
        std::chrono::steady_clock::now() - start).count();
    plans.push_back(std::move(plan));
  }
  for (Plan &plan : plans) {
    if (plan.rewrites && failed(applyOrder(plan)))
      return failure();
    attachReport(plan, catalog, transform);
  }
  if (failed(verify(*working)))
    return module.emitError("QRisk result failed IR/target verification; original module retained");
  if (!reportFile.empty()) {
    llvm::json::Array circuits;
    for (const Plan &plan : plans)
      circuits.push_back(jsonReport(plan, transform));
    llvm::json::Object report{{"schema_version", 1}, {"source_url", catalog.source},
                             {"mode", transform ? "mitigate" : "scan"},
                             {"circuits", std::move(circuits)}};
    std::error_code error;
    llvm::raw_fd_ostream stream(reportFile, error, llvm::sys::fs::OF_Text);
    if (error)
      return module.emitError("cannot create QRisk report: ") << error.message();
    stream << llvm::formatv("{0:2}", llvm::json::Value(std::move(report))) << "\n";
    stream.flush();
    if (stream.has_error())
      return module.emitError("cannot write QRisk report");
  }
  module.getBodyRegion().takeBody(working->getBodyRegion());
  return success();
}

struct QRiskScanPass : PassWrapper<QRiskScanPass, OperationPass<ModuleOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(QRiskScanPass)
  QRiskScanPass() = default;
  QRiskScanPass(const QRiskScanPass &other) : PassWrapper(other) {}
  Option<std::string> patternsFile{*this, "patterns-file", llvm::cl::desc("Local QRisk JSON catalog"), llvm::cl::init("")};
  Option<std::string> reportFile{*this, "report-file", llvm::cl::desc("Optional structured JSON report"), llvm::cl::init("")};
  StringRef getArgument() const final { return "mlirq-qrisk-scan"; }
  StringRef getDescription() const final { return "Report backend-specific QRisk occurrences on mapped physical wires"; }
  void runOnOperation() override {
    if (failed(runQRisk(getOperation(), patternsFile, false, reportFile)))
      signalPassFailure();
  }
};

struct QRiskMitigatePass : PassWrapper<QRiskMitigatePass, OperationPass<ModuleOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(QRiskMitigatePass)
  QRiskMitigatePass() = default;
  QRiskMitigatePass(const QRiskMitigatePass &other) : PassWrapper(other) {}
  Option<std::string> patternsFile{*this, "patterns-file", llvm::cl::desc("Local QRisk JSON catalog"), llvm::cl::init("")};
  Option<std::string> reportFile{*this, "report-file", llvm::cl::desc("Optional structured JSON report"), llvm::cl::init("")};
  Option<bool> global{*this, "global-matching", llvm::cl::desc("Experimental global trace ablation"), llvm::cl::init(false)};
  Option<bool> totalOnly{*this, "total-only", llvm::cl::desc("Experimental total-only acceptance ablation"), llvm::cl::init(false)};
  Option<bool> localOnly{*this, "local-only", llvm::cl::desc("Experimental matched-pattern-only acceptance; requires candidate budget"), llvm::cl::init(false)};
  Option<bool> diagonalOnly{*this, "diagonal-only", llvm::cl::desc("Experimental diagonal/disjoint rules ablation"), llvm::cl::init(false)};
  Option<int64_t> maxCandidates{*this, "max-candidates", llvm::cl::desc("Candidate budget; zero is unlimited"), llvm::cl::init(0)};
  Option<int64_t> maxRewrites{*this, "max-rewrites", llvm::cl::desc("Rewrite budget; zero is unlimited"), llvm::cl::init(0)};
  StringRef getArgument() const final { return "mlirq-qrisk-mitigate"; }
  StringRef getDescription() const final { return "Break QRisk recurrences using exact commuting rewrites and global occurrence checks"; }
  void runOnOperation() override {
    if (failed(runQRisk(getOperation(), patternsFile, true, reportFile,
        {global, totalOnly, diagonalOnly, localOnly, maxCandidates, maxRewrites})))
      signalPassFailure();
  }
};
} // namespace

std::unique_ptr<Pass> mlirq::createQRiskScanPass() { return std::make_unique<QRiskScanPass>(); }
std::unique_ptr<Pass> mlirq::createQRiskMitigatePass() { return std::make_unique<QRiskMitigatePass>(); }
void mlirq::registerQRiskPasses() {
  PassRegistration<QRiskScanPass>();
  PassRegistration<QRiskMitigatePass>();
}
