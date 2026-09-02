"""Estimated VOC ppb (``<node>.voc-est``) for sensors that only report the
Sensirion index.

The index is baseline-relative: 100 is "this room's usual air" and the scale
adapts to each sensor. The archive shows the building shares one VOC floor:
on every real ppb sensor, stripping the excursions (values above the 30th
percentile of a trailing 24 h) and averaging the rest gives curves that agree
across rooms to ~23 ppb. Measured against that floor, index 100 sits a
consistent +77 ppb above it on all three real sensors, and ppb above baseline
is linear in the sigmoid-inverted index with slope ~1.31 ppb per deviation
unit (theory: (12 h std + 220) / 230). Hence, per sample:

    ppb_est = B(t) + OFFSET + SLOPE * (d(index) - d(100))
    d(I)    = X0 + ln(500 / I - 1) / K          (the index sigmoid, inverted)

where B(t) is the building floor tracked live from the real ppb sensors.
Replayed on the real sensors this estimates their own ppb with a median error
within a few ppb and a p90 absolute error of 60..100 ppb: fine for the kiosk
bands, coarse as an absolute reading. Calibration measured 2026-09-02 on
2026-07-30..2026-09-02 (scratchpad voc_ppb_calibration.py, voc_baseline_test.py).
"""
from __future__ import annotations

from collections import deque
import math
from typing import Deque, Dict, Iterable, Optional, Tuple

from .gasindex import is_ppb_voc

DERIVED_SENSOR = "voc-est"
K = -0.0065                    # index sigmoid: I = 500 / (1 + exp(K * (d - X0)))
X0 = 213.0
SLOPE = 1.31                   # ppb per deviation unit
OFFSET = 77.0                  # index 100 sits this far above the building floor
BASELINE_WINDOW_MS = 24 * 3600_000
LOW_QUANTILE = 0.30            # the floor is the mean of the lowest 30 % of the window
MIN_SAMPLES = 60               # a sensor votes on the floor only with an hour of data
DEFAULT_BASELINE = 81.0        # archive median of the floor, used until a vote exists
PPB_FLOOR = 46.0               # every real sensor's hard minimum reading in the archive
RECOMPUTE_MS = 5 * 60_000      # the floor moves slowly; recompute at most this often
PRIME_HOURS = 25               # history replayed on startup so the floor is known


def deviation(index: float) -> float:
    """Invert the index sigmoid. With offset 100 the scaled sigmoid has no
    shift, so this is exact for 0 < I < 500; outside, the clamp saturates."""
    i = min(499.5, max(0.5, float(index)))
    return X0 + math.log(500.0 / i - 1.0) / K


D100 = deviation(100.0)


def lower_average(values: Iterable[float]) -> float:
    """Mean of the lowest LOW_QUANTILE share: the tall parts removed."""
    ordered = sorted(values)
    keep = max(1, int(len(ordered) * LOW_QUANTILE))
    return sum(ordered[:keep]) / keep


def derived_meta(node: str) -> dict:
    return {"node": node, "sensor": DERIVED_SENSOR, "unitsDisplay": "ppb, estimated from index",
            "valueType": "other", "derived": True}


class PpbEstimator:
    """Tracks the building VOC floor from the real ppb sensors and adds
    ``<node>.voc-est`` for every node whose only VOC signal is the index.

    Samples must be applied oldest first (per node); older or duplicate
    timestamps are ignored, the same contract as gasindex.VocIndexer.
    """

    def __init__(self) -> None:
        self.history: Dict[str, Deque[Tuple[int, float]]] = {}
        self.last_ms: Dict[str, int] = {}
        self._floor = DEFAULT_BASELINE
        self._floor_ms: Optional[int] = None

    def floor(self, now_ms: int) -> float:
        """The building floor B(t): median of the per-sensor lower averages."""
        if self._floor_ms is not None and now_ms - self._floor_ms < RECOMPUTE_MS:
            return self._floor
        votes = []
        for dq in self.history.values():
            while dq and dq[0][0] < now_ms - BASELINE_WINDOW_MS:
                dq.popleft()
            if len(dq) >= MIN_SAMPLES:
                votes.append(lower_average(v for _, v in dq))
        if votes:
            votes.sort()
            mid = len(votes) // 2
            self._floor = votes[mid] if len(votes) % 2 else (votes[mid - 1] + votes[mid]) / 2
        self._floor_ms = now_ms
        return self._floor

    def apply(self, time_ms: int, metrics: Dict[str, float], metrics_meta: Optional[dict] = None) -> Dict[str, float]:
        """Add the derived metrics to ``metrics`` in place; returns what was added."""
        meta = metrics_meta or {}
        for key, value in metrics.items():
            dot = key.find(".")
            if dot <= 0 or not isinstance(value, (int, float)):
                continue
            node, sensor = key[:dot], key[dot + 1:]
            if not is_ppb_voc(sensor, (meta.get(key) or {}).get("unitsDisplay")):
                continue
            if time_ms <= self.last_ms.get(node, -1):
                continue
            self.last_ms[node] = time_ms
            self.history.setdefault(node, deque()).append((time_ms, float(value)))
        added: Dict[str, float] = {}
        for key, value in list(metrics.items()):
            dot = key.find(".")
            if dot <= 0 or key[dot + 1:] != "voc-index" or not isinstance(value, (int, float)):
                continue
            node = key[:dot]
            if f"{node}.voc" in metrics:
                continue  # a real ppb sensor: its index is the derived one
            est = self.floor(time_ms) + OFFSET + SLOPE * (deviation(float(value)) - D100)
            # a low index reads below the building floor but never below the
            # 46 ppb device floor every real sensor bottoms out at; clamping to
            # the moving floor instead would collapse all clean rooms onto one line
            metrics[f"{node}.{DERIVED_SENSOR}"] = added[f"{node}.{DERIVED_SENSOR}"] = round(max(PPB_FLOOR, est))
        return added

    def replay(self, rows: Iterable[Tuple[int, Dict[str, float]]], metrics_meta: Optional[dict] = None) -> None:
        for t, metrics in rows:
            self.apply(t, dict(metrics), metrics_meta)

    def prime(self, db, now_ms: int, metrics_meta: Optional[dict] = None, hours: int = PRIME_HOURS) -> None:
        self.replay(db.iter_rows(now_ms - hours * 3600_000, now_ms), metrics_meta)


def reestimate(db, from_ms: int, to_ms: int, metrics_meta: Optional[dict] = None) -> int:
    """Recompute ``voc-est`` for every stored row in [from_ms, to_ms], oldest
    first, primed on the PRIME_HOURS before the range. Returns rows changed."""
    estimator = PpbEstimator()
    estimator.prime(db, from_ms - 1, metrics_meta)
    changed = []
    for t, metrics in db.iter_rows(from_ms, to_ms):
        before = {k: v for k, v in metrics.items() if k.endswith("." + DERIVED_SENSOR)}
        for k in before:
            del metrics[k]
        estimator.apply(t, metrics, metrics_meta)
        after = {k: v for k, v in metrics.items() if k.endswith("." + DERIVED_SENSOR)}
        if after != before:
            changed.append((t, metrics))
    if changed:
        db.update_metrics(changed)
    return len(changed)
