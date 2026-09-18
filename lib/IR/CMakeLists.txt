add_mlir_dialect_library(MLIRQIR
  MLIRQDialect.cpp MLIRQTypes.cpp MLIRQOps.cpp Target.cpp
  DEPENDS MLIRQIRIncGen
  LINK_LIBS PUBLIC MLIRIR MLIRSupport)
