import tempfile
import unittest
from pathlib import Path

from bitraf.db import META_KEY, SensorDB
from bitraf.parser import ParseResult, Sample
from bitraf.poller import Poller


class StoreMetadataTests(unittest.TestCase):
    def test_absent_nodes_keep_last_known_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = SensorDB(Path(tmp) / "t.sqlite")
            db.initialize()
            poller = Poller(db)
            parsed = ParseResult(Sample(1, {}), metadata={
                "nodes": {"a": {"location": "Lab"}, "b": {"location": "Attic"}},
                "metrics": {"a.temp": {"unitsDisplay": "celsius"}},
                "sampleTime": 1,
            })
            poller._store_metadata(parsed)
            # next snapshot is missing node "b" and updates "a"
            parsed2 = ParseResult(Sample(2, {}), metadata={
                "nodes": {"a": {"location": "New lab"}},
                "metrics": {},
                "sampleTime": 2,
            })
            poller._store_metadata(parsed2)
            meta = db.get_meta(META_KEY)
            self.assertEqual(meta["nodes"]["a"]["location"], "New lab")
            self.assertEqual(meta["nodes"]["b"]["location"], "Attic")
            self.assertIn("a.temp", meta["metrics"])
            self.assertEqual(meta["sampleTime"], 2)


class TimeFromUrlTests(unittest.TestCase):
    def test_archive_path(self):
        url = "https://lightside-instruments.com/bitraf/data/2026/08/18/13/14/data.xml"
        self.assertEqual(Poller.time_from_url(url), 1_787_058_840_000)

    def test_garbage(self):
        self.assertIsNone(Poller.time_from_url("https://x/data.xml"))


if __name__ == "__main__":
    unittest.main()
