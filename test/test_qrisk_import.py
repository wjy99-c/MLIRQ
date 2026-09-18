"""Validate the QRisk boundary without Qiskit, network access, or hardware."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from import_qrisk import import_catalog

REVISION = "c51b860505a06618371fd400f7da097c16689f01"
TOKENS = [["rz", [3], [1.570796]], ["cz", [3, 4], []]]


class QRiskImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mlirq-import-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, path, data):
        (self.root / path).write_text(json.dumps(data))
        return path

    def report(self, filename="report.json", date="2026-06-17T15:06:00", **changes):
        data = {"meta": {"timestamp": date, "evaluation": {"test_mode": False}}, "pattern": TOKENS}
        data.update(changes)
        self.write(filename, data)
        return "ibm_fez:" + filename

    def test_report_binding_parameters_and_provenance(self):
        spec = self.report()
        result = import_catalog(self.root, REVISION, [spec], min_observations=1)
        item = result["patterns"][0]
        self.assertEqual(item["backend"], "ibm_fez")
        self.assertEqual(item["gates"], [{"gate": "rz", "qubits": [3], "angle": 1.570796}, {"gate": "cz", "qubits": [3, 4]}])
        self.assertEqual(item["angle_tolerance"], 1e-5)
        self.assertEqual(item["provenance"]["source_revision"], REVISION)
        observation = item["provenance"]["observations"][0]
        self.assertEqual(observation["timestamp"], "2026-06-17T15:06:00")
        self.assertEqual(len(observation["sources"][0]["sha256"]), 64)

    def test_unique_runs_required_for_promotion(self):
        first = self.report()
        self.assertEqual(import_catalog(self.root, REVISION, [first, first])["patterns"], [])
        second = self.report("next.json", "2026-06-24T15:06:00")
        item = import_catalog(self.root, REVISION, [first, first, second])["patterns"][0]
        self.assertEqual(item["provenance"]["observation_count"], 2)

    def test_backends_are_not_merged(self):
        first = self.report()
        other = first.replace("ibm_fez:", "ibm_marrakesh:")
        patterns = import_catalog(self.root, REVISION, [first, other], min_observations=1)["patterns"]
        self.assertEqual(len(patterns), 2)
        self.assertNotEqual(patterns[0]["id"], patterns[1]["id"])

    def test_cleared_pattern_is_never_revived(self):
        spec = self.report(pattern=[], _clean_override={"original": {"pattern": TOKENS}})
        self.assertEqual(import_catalog(self.root, REVISION, [spec], min_observations=1)["patterns"], [])

    def test_pattern_memory_and_mixed_backend_rejection(self):
        observations = [{"backend": "ibm_fez", "timestamp": f"2026-06-{day}T12:00:00"} for day in (17, 24)]
        memory = {"schema_version": 1, "backend": "ibm_fez", "patterns": [
            {"id": "upstream-id", "tokens": TOKENS, "evidence": {"run_count": 99, "observations": observations}}]}
        self.write("memory.json", memory)
        item = import_catalog(self.root, REVISION, databases=["ibm_fez:memory.json"])["patterns"][0]
        self.assertEqual(item["provenance"]["observation_count"], 2)  # Does not trust run_count.
        observations[1]["backend"] = "ibm_marrakesh"
        self.write("memory.json", memory)
        with self.assertRaisesRegex(ValueError, "mixed-backend"):
            import_catalog(self.root, REVISION, databases=["ibm_fez:memory.json"])

    def test_malformed_and_unsupported_tokens_fail(self):
        cases = [
            [["ecr", [3, 4], []], TOKENS[1]],
            [["rz", [3], ["pi/2"]], TOKENS[1]],
            [["rz", [3], [float("inf")]], TOKENS[1]],
            [["cz", [3, 3], []], TOKENS[0]],
            [["x", [True], []], TOKENS[0]],
            [["rz", [3], []], TOKENS[1]],
        ]
        for tokens in cases:
            with self.subTest(tokens=tokens):
                spec = self.report(pattern=tokens)
                with self.assertRaises(ValueError):
                    import_catalog(self.root, REVISION, [spec], min_observations=1)

    def test_simulated_or_inconsistent_evidence_rejected(self):
        for meta in [
            {"timestamp": "2026-06-17", "evaluation": {"test_mode": True}},
            {"timestamp": "2026-06-17", "evaluation": {}},
            {"timestamp": "bad date", "evaluation": {"test_mode": False}},
            {"timestamp": "2026-06-17", "evaluation": {"test_mode": False}, "backend": "ibm_kingston"},
        ]:
            spec = self.report(meta=meta)
            with self.subTest(meta=meta), self.assertRaises(ValueError):
                import_catalog(self.root, REVISION, [spec], min_observations=1)

    def test_import_is_deterministic_and_keeps_input_unchanged(self):
        first = self.report()
        second = self.report("next.json", "2026-06-24T15:06:00")
        original = (self.root / "report.json").read_bytes()
        a = import_catalog(self.root, REVISION, [first, second])
        b = import_catalog(self.root, REVISION, [second, first])
        self.assertEqual(a, b)
        self.assertEqual((self.root / "report.json").read_bytes(), original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
