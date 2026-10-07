FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git cmake ninja-build g++ python3 python3-venv \
    libmlir-18-dev mlir-18-tools llvm-18-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/mlirq
COPY . .
RUN bash scripts/setup-experiments.sh
ENV PATH="/opt/mlirq/.venv/bin:${PATH}" OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
ENTRYPOINT ["python", "-m", "mlirq_qiskit.experiments"]
CMD ["rq1"]
