#!/usr/bin/env python3
"""Convert local QRisk DDMin reports or pattern memories to an MLIRQ catalog.

Standard library only. No hardware access and no network fetching. Raw reports
omit the backend, so every input is explicitly bound as BACKEND:RELATIVE_PATH.
"""
import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re

SOURCE_URL = "https://github.com/qzydustin/qrisk"
ARITY = {"h": 1, "x": 1, "z": 1, "sx": 1, "rz": 1, "cx": 2, "cz": 2}


def convert_tokens(tokens):
    if not isinstance(tokens, list) or len(tokens) < 2:
        raise ValueError("a nonempty pattern must contain at least two gate tokens")
    gates = []
    for token in tokens:
        if not isinstance(token, list) or len(token) != 3:
            raise ValueError("expected QRisk [gate, [physical qubits], [parameters]] tokens")
        name, qubits, params = token
        if not isinstance(name, str) or name not in ARITY:
            raise ValueError(f"unsupported QRisk gate: {name!r}")
        if (not isinstance(qubits, list) or len(qubits) != ARITY[name]
                or any(type(q) is not int or q < 0 for q in qubits)
                or len(set(qubits)) != len(qubits)):
            raise ValueError("invalid physical qubit operands")
        if not isinstance(params, list) or len(params) != (1 if name == "rz" else 0):
            raise ValueError(f"invalid parameter arity for {name}")
        gate = {"gate": name, "qubits": qubits}
        if name == "rz":
            if type(params[0]) not in (int, float) or not math.isfinite(params[0]):
                raise ValueError("rz angle must be a finite numeric value, in radians")
            gate["angle"] = float(params[0])
        gates.append(gate)
    return gates


def timestamp(value):
    if not isinstance(value, str) or not value:
        raise ValueError("each observation requires its original timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("invalid observation timestamp") from error
    return value


def import_catalog(root, revision, reports=(), databases=(), min_observations=2):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("source revision must be a full lowercase Git commit SHA")
    if min_observations < 1:
        raise ValueError("min-observations must be positive")
    root = Path(root).resolve()
    groups = {}

    def add(backend, tokens, observed_at, source):
        gates = convert_tokens(tokens)
        key = json.dumps([backend, gates], sort_keys=True, separators=(",", ":"))
        group = groups.setdefault(key, {"backend": backend, "gates": gates, "observations": {}})
        # Re-reading a file or importing the same run through a DB is not new evidence.
        sources = group["observations"].setdefault(timestamp(observed_at), [])
        if source not in sources:
            sources.append(source)

    for kind, specs in (("report", reports), ("database", databases)):
        for spec in specs:
            backend, separator, relative = spec.partition(":")
            if not separator or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", backend):
                raise ValueError("input must be BACKEND:RELATIVE_PATH with an exact backend name")
            path = (root / relative).resolve()
            if not path.is_relative_to(root) or Path(relative).is_absolute():
                raise ValueError("input path must remain under qrisk-root")
            raw = path.read_bytes()
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("QRisk input must be a JSON object")
            source = {"path": path.relative_to(root).as_posix(),
                      "sha256": hashlib.sha256(raw).hexdigest(), "kind": kind}
            if kind == "report":
                tokens = data.get("pattern")
                if not isinstance(tokens, list):
                    raise ValueError("DDMin report requires a pattern array")
                if not tokens:
                    continue  # Honor cleared/empty reports; never revive _clean_override.original.
                meta = data.get("meta", {})
                if meta.get("evaluation", {}).get("test_mode") is not False:
                    raise ValueError("a DDMin observation requires explicit test_mode=false")
                stated_backend = meta.get("backend", data.get("backend"))
                if stated_backend is not None and stated_backend != backend:
                    raise ValueError("report backend conflicts with the explicit input binding")
                add(backend, tokens, meta.get("timestamp"), source)
            else:
                if data.get("schema_version") != 1 or data.get("backend") != backend:
                    raise ValueError("pattern memory requires schema_version=1 and matching backend")
                entries = data.get("patterns")
                if not isinstance(entries, list):
                    raise ValueError("pattern memory requires a patterns array")
                for entry in entries:
                    observations = entry.get("evidence", {}).get("observations", [])
                    if not isinstance(observations, list) or not observations:
                        raise ValueError("pattern memory entry requires observation evidence")
                    for observation in observations:
                        if observation.get("backend") != backend:
                            raise ValueError("mixed-backend pattern memory evidence is not transferable")
                        add(backend, entry.get("tokens"), observation.get("timestamp"), source)

    patterns = []
    for key, group in sorted(groups.items()):
        observations = group["observations"]
        if len(observations) < min_observations:
            continue
        patterns.append({
            "id": f'qrisk-{group["backend"]}-{hashlib.sha256(key.encode()).hexdigest()[:16]}',
            "backend": group["backend"],
            "description": "Historical QRisk observation; hardware benefit and current applicability are unverified.",
            "angle_tolerance": 1e-5,
            "gates": group["gates"],
            "provenance": {
                "source_revision": revision,
                "backend_binding": "explicit input; checked against metadata where available",
                "observation_count": len(observations),
                "observations": [{"timestamp": date, "sources": sorted(sources, key=lambda s: s["path"])}
                                 for date, sources in sorted(observations.items())],
            },
        })
    return {"schema_version": 1, "source_url": SOURCE_URL,
            "description": "Imported historical QRisk observations, not a current backend calibration or fidelity claim.",
            "patterns": patterns}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qrisk-root", required=True, type=Path)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--report", action="append", default=[], metavar="BACKEND:RELATIVE_PATH")
    parser.add_argument("--database", action="append", default=[], metavar="BACKEND:RELATIVE_PATH")
    parser.add_argument("--min-observations", type=int, default=2)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.report and not args.database:
        parser.error("supply at least one --report or --database")
    try:
        catalog = import_catalog(args.qrisk_root, args.source_revision, args.report,
                                 args.database, args.min_observations)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(catalog, indent=2) + "\n")
    except (ValueError, OSError, TypeError, AttributeError) as error:
        parser.error(str(error))
    print(f"Imported {len(catalog['patterns'])} patterns to {args.output}")


if __name__ == "__main__":
    main()
