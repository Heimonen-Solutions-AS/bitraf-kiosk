"""Weekly per-node statistics for the roll-call panel: availability gaps,
per-sensor aggregates and time-in-band shares over the last N days.

The band table mirrors SENSOR_TYPES in static/js/sensors.js — keep them in sync.
"""
from __future__ import annotations

import re
import statistics
import time
from typing import Dict, List, Optional, Tuple

from .db import SensorDB

# type: ((good_lo, good_hi), (fair_lo, fair_hi)) — outside fair is "poor"
BANDS: Dict[str, Tuple[Tuple[float, float], Tuple[float, float]]] = {
    "temperature": ((18, 25), (15, 28)),
    "co2":         ((0, 800), (0, 1000)),
    "humidity":    ((30, 60), (25, 70)),
    "voc":         ((0, 250), (0, 2000)),
    "pm25":        ((0, 10),  (0, 25)),
    "pm1":         ((0, 10),  (0, 25)),
    "pm10":        ((0, 20),  (0, 50)),
    "radon":       ((0, 100), (0, 150)),
}
GAP_MIN_MS = 10 * 60_000      # a silence never counts as a gap below this
GAP_CADENCE_FACTOR = 3        # ... or below this multiple of the node's own cadence
DAY_MS = 24 * 3600_000


def sensor_type(name: str) -> str:
    """Mirror of sensorType() in static/js/sensors.js."""
    n = name.lower()
    if n.startswith("radon"):
        return "radon"
    if n.startswith("temp") or re.fullmatch(r"th\d*", n):
        return "temperature"
    if n in ("pm25", "pm2.5", "pm2_5"):
        return "pm25"
    if n.startswith("humid") or n == "rh":
        return "humidity"
    if n.startswith("press"):
        return "pressure"
    if re.sub(r"[-_ ]", "", n).endswith("airquality"):
        return "airquality"
    return n


def band_status(type_: str, value: float) -> Optional[str]:
    """ok / fair / poor for rated types, None for unrated ones."""
    if type_ == "airquality":  # device-reported enum: 1 good, 2-3 fair, >=4 poor
        v = round(value)
        if v == 1:
            return "ok"
        if v in (2, 3):
            return "fair"
        return "poor" if v >= 4 else None
    bands = BANDS.get(type_)
    if bands is None:
        return None
    good, fair = bands
    if good[0] <= value <= good[1]:
        return "ok"
    if fair[0] <= value <= fair[1]:
        return "fair"
    return "poor"


class _TypeAcc:
    __slots__ = ("n", "total", "vmin", "vmax", "min_at", "max_at", "last", "last_at",
                 "bands", "n24", "total24", "n_prior", "total_prior")

    def __init__(self) -> None:
        self.n = 0; self.total = 0.0
        self.vmin = None; self.vmax = None; self.min_at = None; self.max_at = None
        self.last = None; self.last_at = None
        self.bands = {"ok": 0, "fair": 0, "poor": 0}
        self.n24 = 0; self.total24 = 0.0; self.n_prior = 0; self.total_prior = 0.0


def weekly_stats(db: SensorDB, days: int = 7, now_ms: Optional[int] = None) -> dict:
    """One pass over the samples of the last `days` days.

    Per node: reporting cadence (median interval), availability gaps relative to
    that cadence, total downtime, and per sensor type avg/min/max (with times),
    time-in-band shares, plus a last-24 h vs the-days-before average for trends.
    """
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    from_ms = now_ms - days * DAY_MS
    day_ago = now_ms - DAY_MS

    times: Dict[str, List[int]] = {}
    types: Dict[str, Dict[str, _TypeAcc]] = {}
    for t, metrics in db.iter_rows(from_ms, now_ms):
        seen_nodes = set()
        for key, value in metrics.items():
            dot = key.find(".")
            if dot <= 0 or not isinstance(value, (int, float)):
                continue
            node, sensor = key[:dot], key[dot + 1:]
            if node not in seen_nodes:
                seen_nodes.add(node)
                times.setdefault(node, []).append(t)
            type_ = sensor_type(sensor)
            acc = types.setdefault(node, {}).setdefault(type_, _TypeAcc())
            acc.n += 1; acc.total += value
            if acc.vmin is None or value < acc.vmin:
                acc.vmin, acc.min_at = value, t
            if acc.vmax is None or value > acc.vmax:
                acc.vmax, acc.max_at = value, t
            acc.last, acc.last_at = value, t
            status = band_status(type_, value)
            if status:
                acc.bands[status] += 1
            if t >= day_ago:
                acc.n24 += 1; acc.total24 += value
            else:
                acc.n_prior += 1; acc.total_prior += value

    nodes = {}
    for node, ts in times.items():
        diffs = [b - a for a, b in zip(ts, ts[1:]) if b > a]
        cadence = int(statistics.median(diffs)) if diffs else None
        gap_over = max(GAP_MIN_MS, GAP_CADENCE_FACTOR * cadence) if cadence else GAP_MIN_MS
        gaps = [(a, b) for a, b in zip(ts, ts[1:]) if b - a > gap_over]
        downtime = sum(b - a for a, b in gaps)
        silent_ms = now_ms - ts[-1]
        silent_now = silent_ms > gap_over
        if silent_now:
            downtime += silent_ms
        span = max(1, now_ms - max(from_ms, ts[0]))
        node_types = {}
        for type_, a in types.get(node, {}).items():
            entry = {
                "avg": round(a.total / a.n, 2), "min": a.vmin, "max": a.vmax,
                "minAt": a.min_at, "maxAt": a.max_at, "last": a.last, "lastAt": a.last_at, "n": a.n,
            }
            rated = sum(a.bands.values())
            if rated:
                entry["pct"] = {k: round(v / rated, 3) for k, v in a.bands.items()}
            if a.n24:
                entry["avg24h"] = round(a.total24 / a.n24, 2)
            if a.n_prior:
                entry["avgPrior"] = round(a.total_prior / a.n_prior, 2)
            node_types[type_] = entry
        nodes[node] = {
            "firstMs": ts[0], "lastMs": ts[-1], "rows": len(ts),
            "cadenceMs": cadence,
            "gapCount": len(gaps),
            "gaps": [{"fromMs": a, "toMs": b} for a, b in gaps[-20:]],
            "silentNow": silent_now,
            "downtimeMs": downtime,
            "downtimePct": round(downtime / span, 4),
            "types": node_types,
        }
    return {"generatedAt": now_ms, "days": days, "fromMs": from_ms, "toMs": now_ms, "nodes": nodes}
