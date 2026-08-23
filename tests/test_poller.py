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


FIXTURE = (Path(__file__).parent / "fixtures" / "data.xml").read_text()


class _ArchivePoller(Poller):
    """Poller over an in-memory archive: url -> body ('' = created but not written yet)."""

    def __init__(self, db, files):
        super().__init__(db, "http://archive/data/")
        self.files = files
        self.fetched = []

    def discover_latest_url(self):
        return max(self.files)

    def fetch_raw(self, url):
        self.fetched.append(url)
        if url not in self.files:
            raise RuntimeError("network error: HTTP Error 404: Not Found")
        return self.files[url]


class MetadataMonotonicTests(unittest.TestCase):
    def test_backfill_of_old_files_cannot_regress_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = SensorDB(Path(tmp) / "t.sqlite")
            db.initialize()
            poller = Poller(db)
            poller._store_metadata(ParseResult(Sample(2, {}), metadata={
                "nodes": {"a": {"location": "New name"}}, "metrics": {}, "sampleTime": 2}))
            # an older snapshot arrives later (backfill): must be ignored entirely
            poller._store_metadata(ParseResult(Sample(1, {}), metadata={
                "nodes": {"a": {"location": "Old name"}, "ghost": {}}, "metrics": {}, "sampleTime": 1}))
            meta = db.get_meta(META_KEY)
            self.assertEqual(meta["nodes"]["a"]["location"], "New name")
            self.assertNotIn("ghost", meta["nodes"])
            self.assertEqual(meta["sampleTime"], 2)


class PollTests(unittest.TestCase):
    def _db(self, tmp):
        db = SensorDB(Path(tmp) / "t.sqlite")
        db.initialize()
        return db

    def test_empty_newest_minute_falls_back_to_the_one_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            poller = _ArchivePoller(db, {
                "http://archive/data/2026/08/21/12/59/data.xml": FIXTURE,
                "http://archive/data/2026/08/21/13/00/data.xml": "",  # being written
            })
            result = poller.poll()
            self.assertEqual(result.sample.time_ms, Poller.time_from_url("http://archive/data/2026/08/21/12/59/data.xml"))
            self.assertEqual(db.count(), 1)
            self.assertIsNone(poller.last_error)
            self.assertEqual(db.last_fetch()["status"], "ok")

    def test_both_minutes_readable_stores_both_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            poller = _ArchivePoller(db, {
                "http://archive/data/2026/08/21/13/00/data.xml": FIXTURE,
                "http://archive/data/2026/08/21/13/01/data.xml": FIXTURE,
            })
            result = poller.poll()
            self.assertEqual(result.sample.time_ms, Poller.time_from_url("http://archive/data/2026/08/21/13/01/data.xml"))
            self.assertEqual(db.count(), 2)
            # the previous minute is already stored: the next poll adds nothing and stays ok
            poller.poll()
            self.assertEqual(db.count(), 2)
            self.assertEqual(db.last_fetch()["rows_new"], 0)

    def test_nothing_readable_raises_and_logs_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            poller = _ArchivePoller(db, {"http://archive/data/2026/08/21/13/00/data.xml": ""})
            with self.assertRaises(RuntimeError):
                poller.poll()
            self.assertEqual(db.last_fetch()["status"], "error")
            self.assertIn("13/00", poller.last_error)


class TimeFromUrlTests(unittest.TestCase):
    def test_archive_path(self):
        url = "https://lightside-instruments.com/bitraf/data/2026/08/18/13/14/data.xml"
        self.assertEqual(Poller.time_from_url(url), 1_787_058_840_000)

    def test_garbage(self):
        self.assertIsNone(Poller.time_from_url("https://x/data.xml"))


if __name__ == "__main__":
    unittest.main()
