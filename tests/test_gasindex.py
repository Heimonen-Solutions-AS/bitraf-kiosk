"""The gas index port is diffed against Sensirion's reference C implementation
(compiled on the fly when a C compiler is around), and the derived voc-index
metric is checked end to end through the indexer, the DB and the reindex pass."""
from __future__ import annotations

import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import unittest

from bitraf.db import SensorDB
from bitraf.gasindex import (ALGORITHM_TYPE_NOX, ALGORITHM_TYPE_VOC, DERIVED_SENSOR, GasIndexAlgorithm, VocIndexer,
                             is_ppb_voc, reindex, sraw_from_ppb)
from bitraf.parser import Sample

FIXTURES = Path(__file__).parent / "fixtures" / "sensirion"
HARNESS = r"""
#include <stdio.h>
#include <stdlib.h>
#include "sensirion_gas_index_algorithm.h"
int main(int argc, char** argv) {
    GasIndexAlgorithmParams p;
    int32_t type = atoi(argv[1]);
    float interval = (float)atof(argv[2]);
    GasIndexAlgorithm_init_with_sampling_interval(&p, type, interval);
    int32_t sraw, idx;
    while (scanf("%d", &sraw) == 1) { GasIndexAlgorithm_process(&p, sraw, &idx); printf("%d\n", idx); }
    return 0;
}
"""


def _compiler():
    for cc in ("cc", "gcc", "clang"):
        if shutil.which(cc):
            return cc
    return None


def reference_indices(sraw: list, algorithm_type: int, interval: float) -> list:
    """Run the vendored C reference over the raw samples."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "harness.c"
        src.write_text(HARNESS)
        exe = Path(tmp) / "harness"
        subprocess.run([_compiler(), "-O2", "-I", str(FIXTURES), str(src),
                        str(FIXTURES / "sensirion_gas_index_algorithm.c"), "-lm", "-o", str(exe)],
                       check=True, capture_output=True)
        out = subprocess.run([str(exe), str(algorithm_type), repr(interval)], input="\n".join(map(str, sraw)),
                             capture_output=True, text=True, check=True).stdout
    return [int(v) for v in out.split()]


def synthetic_ticks(n: int, seed: int, base: int = 30000, spikes: int = 6, amplitude: int = -2500) -> list:
    """A drifting baseline with a few gas events (VOC ticks fall with VOC, NOx ticks rise with NOx)."""
    rng = random.Random(seed)
    level = float(base)
    out = []
    events = sorted(rng.sample(range(n // 10, n), spikes))
    for i in range(n):
        level += rng.gauss(0, 4) - (level - base) * 0.001
        v = level
        for e in events:
            if i >= e:
                v += amplitude * math.exp(-(i - e) / (n / 40))
        out.append(int(v + rng.gauss(0, 15)))
    return out


@unittest.skipUnless(_compiler(), "no C compiler for the reference implementation")
class ReferenceDiffTests(unittest.TestCase):
    def _diff(self, algorithm_type: int, interval: float, sraw: list) -> None:
        ref = reference_indices(sraw, algorithm_type, interval)
        engine = GasIndexAlgorithm(algorithm_type, interval)
        ours = [engine.process(v) for v in sraw]
        self.assertEqual(len(ours), len(ref))
        diffs = [abs(a - b) for a, b in zip(ours, ref)]
        # the reference runs in float32, the port in doubles: an off-by-one on the
        # rounded index is the only allowed disagreement, and it must stay rare
        self.assertLessEqual(max(diffs), 1, f"max diff {max(diffs)} at {diffs.index(max(diffs))}")
        self.assertLess(sum(diffs) / len(diffs), 0.02)
        self.assertGreater(max(ref), 250, "the synthetic events should drive the index up")

    def test_voc_one_second_interval(self):
        self._diff(ALGORITHM_TYPE_VOC, 1.0, synthetic_ticks(4 * 3600, seed=1))

    def test_voc_one_minute_interval(self):
        self._diff(ALGORITHM_TYPE_VOC, 60.0, synthetic_ticks(3 * 24 * 60, seed=2))

    def test_nox_one_minute_interval(self):
        self._diff(ALGORITHM_TYPE_NOX, 60.0, synthetic_ticks(3 * 24 * 60, seed=3, base=16000, spikes=3, amplitude=8000))

    def test_ppb_series_through_reference(self):
        # the ppb → ticks mapping keeps the reference usable on Airthings-style data
        rng = random.Random(4)
        ppb = [max(46.0, 150 + 70 * math.sin(i / 300) + rng.gauss(0, 25) + (600 if 1500 < i < 1700 else 0))
               for i in range(3 * 24 * 60)]
        self._diff(ALGORITHM_TYPE_VOC, 60.0, [sraw_from_ppb(p) for p in ppb])


class BehaviourTests(unittest.TestCase):
    def test_blackout_then_baseline(self):
        engine = GasIndexAlgorithm(ALGORITHM_TYPE_VOC, 60.0)
        first = engine.process(sraw_from_ppb(150), 60.0)
        self.assertEqual(first, 0)
        for _ in range(3 * 60):
            idx = engine.process(sraw_from_ppb(150), 60.0)
        self.assertEqual(idx, 100, "a flat signal settles at the configured offset")

    def test_event_rises_and_recovers(self):
        engine = GasIndexAlgorithm(ALGORITHM_TYPE_VOC, 60.0)
        for _ in range(3 * 60):
            engine.process(sraw_from_ppb(150), 60.0)
        peak = 0
        for _ in range(20):
            peak = max(peak, engine.process(sraw_from_ppb(700), 60.0))
        self.assertGreater(peak, 250)
        for _ in range(12 * 60):
            idx = engine.process(sraw_from_ppb(150), 60.0)
        self.assertLess(abs(idx - 100), 10)

    def test_ppb_detection(self):
        self.assertTrue(is_ppb_voc("voc", "VOC particles"))
        self.assertTrue(is_ppb_voc("VOC", None))
        self.assertFalse(is_ppb_voc("voc", "VOC index"))
        self.assertFalse(is_ppb_voc("voc-index", None))
        self.assertFalse(is_ppb_voc("co2", "CO2 level"))


class IndexerTests(unittest.TestCase):
    def test_adds_derived_metric_in_time_order_only(self):
        ix = VocIndexer()
        t0 = 1_700_000_000_000
        added = ix.apply(t0, {"a.voc": 120, "a.co2": 500, "b.voc": 80})
        self.assertEqual(added, {}, "the first sample is inside the blackout")
        m = {"a.voc": 120, "b.voc": 80}
        added = ix.apply(t0 + 60_000, m)
        self.assertEqual(set(added), {f"a.{DERIVED_SENSOR}", f"b.{DERIVED_SENSOR}"})
        self.assertIn(f"a.{DERIVED_SENSOR}", m)
        # an older or duplicate row does not disturb the running state
        self.assertEqual(ix.apply(t0 + 30_000, {"a.voc": 900}), {})
        self.assertEqual(ix.apply(t0 + 60_000, {"a.voc": 900}), {})
        # a device reporting an index already is left alone
        self.assertEqual(ix.apply(t0 + 120_000, {"c.voc": 130}, {"c.voc": {"unitsDisplay": "VOC index"}}), {})

    def test_reindex_fills_history_and_is_idempotent(self):
        tmp = tempfile.mkdtemp()
        try:
            db = SensorDB(Path(tmp) / "t.sqlite")
            db.initialize()
            t0 = 1_700_000_000_000
            rng = random.Random(5)
            samples = [Sample(t0 + i * 60_000, {"a.voc": 150 + rng.gauss(0, 30) + (500 if 200 < i < 230 else 0),
                                                 "a.co2": 600})
                       for i in range(6 * 60)]
            db.insert_samples(samples)
            changed = reindex(db, t0 + 60 * 60_000, t0 + 6 * 60 * 60_000)
            self.assertGreater(changed, 250)
            rows = list(db.iter_rows(t0, t0 + 6 * 60 * 60_000))
            self.assertNotIn(f"a.{DERIVED_SENSOR}", rows[10][1], "rows before the range are untouched")
            self.assertIn(f"a.{DERIVED_SENSOR}", rows[100][1])
            self.assertIn("a.co2", rows[100][1], "other metrics survive the rewrite")
            peak = max(m.get(f"a.{DERIVED_SENSOR}", 0) for _, m in rows)
            self.assertGreater(peak, 200)
            self.assertEqual(reindex(db, t0 + 60 * 60_000, t0 + 6 * 60 * 60_000), 0, "second pass changes nothing")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
