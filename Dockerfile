FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    cmake ninja-build g++ python3 libmlir-18-dev mlir-18-tools llvm-18-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src/mlirq
COPY . .
RUN cmake -S . -B build -G Ninja \
      -DMLIR_DIR=/usr/lib/llvm-18/lib/cmake/mlir \
      -DLLVM_DIR=/usr/lib/llvm-18/lib/cmake/llvm -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build --parallel 2 \
    && ctest --test-dir build --output-on-failure
ENTRYPOINT ["/src/mlirq/build/bin/mlirq-opt"]
