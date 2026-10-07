#!/usr/bin/env python3
"""Download the pinned upstream baseline unchanged; never execute at download."""
import hashlib
from pathlib import Path
import urllib.request

REVISION = "c51b860505a06618371fd400f7da097c16689f01"
SHA256 = "1c21a1ceb379663fab72c2f55856c7059d64cc696712ef9fe31f063c40d9bc5f"
URL = f"https://raw.githubusercontent.com/qzydustin/qrisk/{REVISION}/quantum/pattern_transform.py"
destination = Path(__file__).resolve().parents[1] / ".cache/qrisk/pattern_transform.py"
data = urllib.request.urlopen(URL, timeout=60).read()
if hashlib.sha256(data).hexdigest() != SHA256:
    raise SystemExit("Upstream source hash mismatch; baseline not installed")
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_bytes(data)
print(f"Downloaded QRisk {REVISION} to {destination}")
