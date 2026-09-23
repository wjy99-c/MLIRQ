"""Check that the portable adapter wheel contains no native build artifacts."""

import argparse
from pathlib import Path
import zipfile


def check_wheel(path):
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
    required = {
        "mlirq_qiskit/__init__.py",
        "mlirq_qiskit/importer.py",
        "mlirq_qiskit/exporter.py",
        "mlirq_qiskit/native.py",
        "mlirq_qiskit/py.typed",
    }
    assert required <= names, f"Missing adapter files: {sorted(required - names)}"
    unexpected = {
        name for name in names
        if not (
            name.startswith("mlirq_qiskit/")
            and (name.endswith(".py") or name == "mlirq_qiskit/py.typed")
            or name.startswith("mlirq_qiskit-") and ".dist-info/" in name
        )
    }
    assert not unexpected, f"Unexpected wheel contents: {sorted(unexpected)}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    check_wheel(args.wheel)
    print(f"Verified portable adapter wheel: {args.wheel.name}")
