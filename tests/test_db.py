import tempfile
import unittest
from pathlib import Path

from bitraf.db import SensorDB
from bitraf.parser import Sample


class SensorDBTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = SensorDB(Path(self.tmp.name) / "t.sqlite")
        self.db.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def test_insert_is_idempotent_per_minute(self):
        s = Sample(60_000, {"a.x": 1.0})
        self.assertEqual(len(self.db.insert_samples([s])), 1)
        self.assertEqual(len(self.db.insert_samples([s])), 0)
        self.assertEqual(self.db.count(), 1)

    def test_range_raw_and_aggregated(self):
        samples = [Sample(t * 60_000, {"a.x": float(t), "a.y": 10.0}) for t in range(1, 201)]
        self.db.insert_samples(samples)
        rows, bucket = self.db.rows_in_range(0, 300 * 60_000, max_points=500)
        self.assertEqual((len(rows), bucket), (200, 0))
        rows, bucket = self.db.rows_in_range(0, 300 * 60_000, max_points=50)
        self.assertGreater(bucket, 60_000)
        self.assertLessEqual(len(rows), 50 + 1)
        self.assertAlmostEqual(rows[0]["metrics"]["a.y"], 10.0)
        self.assertEqual(self.db.time_bounds(), (60_000, 200 * 60_000))

    def test_meta_roundtrip(self):
        self.assertIsNone(self.db.get_meta("k"))
        self.db.set_meta("k", {"nodes": {"n": {"id": "n"}}})
        self.db.set_meta("k", {"nodes": {"n": {"id": "n2"}}})
        self.assertEqual(self.db.get_meta("k")["nodes"]["n"]["id"], "n2")

    def test_csv_export(self):
        self.db.insert_samples([Sample(60_000, {"a.x": 1.5}), Sample(120_000, {"a.y": 2.0})])
        text = self.db.export_csv().decode("utf-8-sig")
        lines = text.strip().splitlines()
        self.assertEqual(lines[0], "timestamp_utc,time_ms,a.x,a.y")
        self.assertEqual(len(lines), 3)


if __name__ == "__main__":
    unittest.main()
