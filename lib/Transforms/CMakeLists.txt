add_mlir_library(MLIRQTransforms Passes.cpp
  DEPENDS MLIRQIRIncGen
  LINK_LIBS PUBLIC MLIRQIR MLIRPass MLIRTransforms)
