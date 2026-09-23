add_mlir_library(MLIRQTransforms Passes.cpp QRiskPasses.cpp QiskitExport.cpp
  DEPENDS MLIRQIRIncGen
  LINK_LIBS PUBLIC MLIRQIR MLIRPass MLIRTransforms)
