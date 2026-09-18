add_mlir_library(MLIRQTransforms Passes.cpp QRiskPasses.cpp
  DEPENDS MLIRQIRIncGen
  LINK_LIBS PUBLIC MLIRQIR MLIRPass MLIRTransforms)
