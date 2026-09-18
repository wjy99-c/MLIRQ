cmake_minimum_required(VERSION 3.20)
project(MLIRQ VERSION 0.1.0 LANGUAGES C CXX)

find_package(MLIR REQUIRED CONFIG)
if(NOT LLVM_VERSION_MAJOR EQUAL 18)
  message(FATAL_ERROR "This initial MLIRQ core targets LLVM/MLIR 18.x; found ${LLVM_PACKAGE_VERSION}")
endif()
set(CMAKE_CXX_STANDARD 17 CACHE STRING "C++ standard")
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)
set(LLVM_RUNTIME_OUTPUT_INTDIR ${CMAKE_BINARY_DIR}/bin)
set(LLVM_LIBRARY_OUTPUT_INTDIR ${CMAKE_BINARY_DIR}/lib)
set(MLIR_BINARY_DIR ${CMAKE_BINARY_DIR})
list(APPEND CMAKE_MODULE_PATH "${MLIR_CMAKE_DIR}" "${LLVM_CMAKE_DIR}")
include(TableGen)
include(AddLLVM)
include(AddMLIR)
include(HandleLLVMOptions)
include_directories(SYSTEM ${LLVM_INCLUDE_DIRS} ${MLIR_INCLUDE_DIRS})
include_directories(${PROJECT_SOURCE_DIR}/include ${PROJECT_BINARY_DIR}/include)
add_definitions(${LLVM_DEFINITIONS})

add_subdirectory(include/mlirq/IR)
add_subdirectory(lib/IR)
add_subdirectory(lib/Transforms)
add_subdirectory(tools/mlirq-opt)
include(CTest)
if(BUILD_TESTING)
  find_package(Python3 REQUIRED COMPONENTS Interpreter)
  add_test(NAME mlirq-regression
    COMMAND ${Python3_EXECUTABLE} ${PROJECT_SOURCE_DIR}/test/test_cli.py
      --mlirq-opt $<TARGET_FILE:mlirq-opt>)
  add_test(NAME mlirq-qrisk
    COMMAND ${Python3_EXECUTABLE} ${PROJECT_SOURCE_DIR}/test/test_qrisk.py
      --mlirq-opt $<TARGET_FILE:mlirq-opt>)
  add_test(NAME mlirq-qrisk-import
    COMMAND ${Python3_EXECUTABLE} ${PROJECT_SOURCE_DIR}/test/test_qrisk_import.py)
  add_test(NAME mlirq-routing
    COMMAND ${Python3_EXECUTABLE} ${PROJECT_SOURCE_DIR}/test/test_routing.py
      --mlirq-opt $<TARGET_FILE:mlirq-opt>)
endif()
