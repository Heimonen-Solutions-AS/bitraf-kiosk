"""Tests for the estimated-ppb derived metric (bitraf/vocppb.py)."""
import unittest

from bitraf.vocppb import (D100, DEFAULT_BASELINE, OFFSET, PPB_FLOOR, SLOPE, DERIVED_SENSOR,
                           PpbEstimator, deviation, lower_average, reestimate)


class DeviationTest(unittest.TestCase):
    def test_index_100_is_the_anchor(self):
        self.assertAlmostEqual(deviation(100.0), D100)
        self.assertLess(abs(D100), 1.0)  # index 100 means "at the baseline"

    def test_monotonic_and_clamped(self):
        self.assertLess(deviation(80), deviation(100))
        self.assertLess(deviation(100), deviation(250))
        self.assertEqual(deviation(500), deviation(600))  # saturates, no domain error
        self.assertEqual(deviation(0), deviation(-5))


class LowerAverageTest(unittest.TestCase):
    def test_tall_parts_removed(self):
        quiet = [80.0] * 70
        spikes = [800.0] * 30
        self.assertAlmostEqual(lower_average(quiet + spikes), 80.0)


class EstimatorTest(unittest.TestCase):
    def _primed(self, level=80.0):
        """An estimator that has seen 24 h of two real sensors at `level` ppb."""
        est = PpbEstimator()
        for minute in range(24 * 60):
            t = minute * 60_000
            est.apply(t, {"real0.voc": level, "real1.voc": level + 10.0})
        return est, 24 * 60 * 60_000

    def test_index_100_maps_to_floor_plus_offset(self):
        est, now = self._primed(80.0)
        metrics = {"shield0.voc-index": 100.0}
        added = est.apply(now, metrics)
        value = added["shield0." + DERIVED_SENSOR]
        # floor is the median of 80 and 90, the offset on top, d(100) ~ 0
        self.assertAlmostEqual(value, 85.0 + OFFSET + SLOPE * 0, delta=2)
        self.assertEqual(metrics["shield0." + DERIVED_SENSOR], value)

    def test_higher_index_means_more_ppb(self):
        est, now = self._primed()
        low = est.apply(now, {"shield0.voc-index": 100.0})["shield0." + DERIVED_SENSOR]
        high = est.apply(now + 60_000, {"shield0.voc-index": 250.0})["shield0." + DERIVED_SENSOR]
        self.assertGreater(high, low + 100)

    def test_pegs_at_the_device_floor_not_zero(self):
        est, now = self._primed(80.0)
        added = est.apply(now, {"shield0.voc-index": 1.0})
        self.assertEqual(added["shield0." + DERIVED_SENSOR], PPB_FLOOR)

    def test_clean_rooms_keep_their_order(self):
        # indexes below 100 still differentiate instead of collapsing onto one line
        est, now = self._primed(80.0)
        a = est.apply(now, {"shield0.voc-index": 85.0})["shield0." + DERIVED_SENSOR]
        b = est.apply(now, {"shield1.voc-index": 70.0})["shield1." + DERIVED_SENSOR]
        self.assertGreater(a, b)
        self.assertGreaterEqual(b, PPB_FLOOR)

    def test_floor_ignores_spikes(self):
        est = PpbEstimator()
        for minute in range(24 * 60):
            t = minute * 60_000
            ppb = 500.0 if minute % 10 == 0 else 80.0  # a spike every 10 minutes
            est.apply(t, {"real0.voc": ppb})
        self.assertAlmostEqual(est.floor(24 * 60 * 60_000), 80.0, delta=1)

    def test_real_ppb_nodes_are_not_estimated(self):
        est, now = self._primed()
        # an Airthings row: real ppb plus its derived index in the same sample
        added = est.apply(now, {"real0.voc": 120.0, "real0.voc-index": 130.0})
        self.assertEqual(added, {})

    def test_default_floor_without_history(self):
        est = PpbEstimator()
        added = est.apply(0, {"shield0.voc-index": 100.0})
        self.assertAlmostEqual(added["shield0." + DERIVED_SENSOR],
                               DEFAULT_BASELINE + OFFSET + SLOPE * (deviation(100.0) - D100), delta=1)

    def test_index_units_voc_is_not_a_floor_vote(self):
        est = PpbEstimator()
        meta = {"odd0.voc": {"unitsDisplay": "VOC index"}}
        for minute in range(24 * 60):
            est.apply(minute * 60_000, {"odd0.voc": 100.0}, meta)
        self.assertEqual(est.floor(24 * 60 * 60_000), DEFAULT_BASELINE)


class _FakeDB:
    def __init__(self, rows):
        self.rows = [(t, dict(m)) for t, m in rows]
        self.updated = []

    def iter_rows(self, from_ms, to_ms):
        for t, m in self.rows:
            if from_ms <= t <= to_ms:
                yield t, dict(m)

    def update_metrics(self, rows):
        self.updated = list(rows)
        return len(self.updated)


class ReestimateTest(unittest.TestCase):
    def test_backfilled_rows_gain_the_estimate(self):
        rows = []
        for minute in range(25 * 60):
            t = minute * 60_000
            metrics = {"real0.voc": 80.0}
            if minute >= 24 * 60:
                metrics["shield0.voc-index"] = 120.0
            rows.append((t, metrics))
        db = _FakeDB(rows)
        changed = reestimate(db, 24 * 60 * 60_000, 25 * 60 * 60_000)
        self.assertEqual(changed, 60)
        for t, metrics in db.updated:
            self.assertIn("shield0." + DERIVED_SENSOR, metrics)


if __name__ == "__main__":
    unittest.main()
