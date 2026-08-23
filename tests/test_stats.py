import tempfile
import unittest
from pathlib import Path

from bitraf.db import SensorDB
from bitraf.parser import Sample
from bitraf.stats import band_status, sensor_type, weekly_stats

MIN = 60_000
H = 60 * MIN


class WeeklyStatsTests(unittest.TestCase):
    def _db(self, tmp, samples):
        db = SensorDB(Path(tmp) / "t.sqlite")
        db.initialize()
        db.insert_samples(samples)
        return db

    def test_gaps_respect_each_nodes_own_cadence(self):
        t0 = 1_787_000_000_000
        samples = []
        # node a: per-minute, silent minutes 30..59 (one true gap)
        # node b: hourly — a 60 min silence is normal, not a gap
        for m in list(range(0, 30)) + list(range(60, 121)):
            metrics = {"a.temperature": 20.0}
            if m % 60 == 0:
                metrics["b.temperature"] = 21.0
            samples.append(Sample(t0 + m * MIN, metrics))
        with tempfile.TemporaryDirectory() as tmp:
            st = weekly_stats(self._db(tmp, samples), days=1, now_ms=t0 + 121 * MIN)
        a, b = st["nodes"]["a"], st["nodes"]["b"]
        self.assertEqual(a["gapCount"], 1)
        self.assertEqual(a["gaps"][0], {"fromMs": t0 + 29 * MIN, "toMs": t0 + 60 * MIN})
        self.assertEqual(a["cadenceMs"], MIN)
        self.assertFalse(a["silentNow"])
        self.assertEqual(b["gapCount"], 0)
        self.assertEqual(b["cadenceMs"], H)

    def test_aggregates_bands_and_trend(self):
        t0 = 1_787_000_000_000
        day = 24 * H
        # 2 days: first day at 30 °C (poor), last day at 20 °C (ok)
        samples = [Sample(t0 + m * H, {"a.temperature": 30.0 if m < 24 else 20.0}) for m in range(48)]
        with tempfile.TemporaryDirectory() as tmp:
            st = weekly_stats(self._db(tmp, samples), days=7, now_ms=t0 + 2 * day)
        t = st["nodes"]["a"]["types"]["temperature"]
        self.assertEqual(t["avg"], 25.0)
        self.assertEqual((t["min"], t["max"]), (20.0, 30.0))
        self.assertEqual(t["maxAt"], t0)
        self.assertEqual(t["pct"], {"ok": 0.5, "fair": 0.0, "poor": 0.5})
        self.assertEqual(t["avgPrior"], 30.0)   # first day
        self.assertLess(t["avg24h"], 21.0)      # last day (boundary sample included)

    def test_silent_now_counts_as_downtime(self):
        t0 = 1_787_000_000_000
        samples = [Sample(t0 + m * MIN, {"a.co2": 500.0}) for m in range(60)]
        with tempfile.TemporaryDirectory() as tmp:
            st = weekly_stats(self._db(tmp, samples), days=1, now_ms=t0 + 5 * H)
        a = st["nodes"]["a"]
        self.assertTrue(a["silentNow"])
        self.assertEqual(a["gapCount"], 0)
        self.assertGreater(a["downtimeMs"], 4 * H)

    def test_type_mapping_and_bands(self):
        self.assertEqual(sensor_type("Temperature"), "temperature")
        self.assertEqual(sensor_type("rh"), "humidity")
        self.assertEqual(sensor_type("pm2_5"), "pm25")
        self.assertEqual(band_status("co2", 900), "fair")
        self.assertEqual(band_status("airquality", 4), "poor")
        self.assertIsNone(band_status("pressure", 1000))


if __name__ == "__main__":
    unittest.main()
