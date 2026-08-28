"""SQLite storage: one row per archived minute, a fetch log and a metadata KV."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import json
import math
from pathlib import Path
import sqlite3
import time
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .parser import Sample

META_KEY = "node_metadata"
SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    time_ms      INTEGER PRIMARY KEY,
    metrics_json TEXT    NOT NULL,
    created_at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS fetch_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at  INTEGER NOT NULL,
    status      TEXT    NOT NULL,
    rows_found  INTEGER NOT NULL,
    rows_new    INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    message     TEXT
);
CREATE INDEX IF NOT EXISTS ix_fetch_log_time ON fetch_log(fetched_at);
CREATE TABLE IF NOT EXISTS metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


class SensorDB:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA busy_timeout = 30000")
        return db

    def initialize(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(SCHEMA)

    # -- metadata ----------------------------------------------------------
    def set_meta(self, key: str, value: dict) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO metadata(key, value) VALUES (?, ?) "
                       "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                       (key, json.dumps(value, ensure_ascii=False)))

    def get_meta(self, key: str) -> Optional[dict]:
        with self._connect() as db:
            row = db.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except ValueError:
            return None

    # -- writes ------------------------------------------------------------
    def insert_samples(self, samples: Iterable[Sample]) -> List[Sample]:
        """Insert samples; returns the ones that were actually new."""
        inserted: List[Sample] = []
        now = int(time.time() * 1000)
        with self._connect() as db:
            for sample in samples:
                cur = db.execute("INSERT OR IGNORE INTO samples(time_ms, metrics_json, created_at) VALUES (?, ?, ?)",
                                 (int(sample.time_ms), _dumps(sample.metrics), now))
                if cur.rowcount == 1:
                    inserted.append(sample)
        return inserted

    def update_metrics(self, rows: Iterable[Tuple[int, Dict[str, float]]]) -> int:
        """Rewrite the metrics of existing rows (derived-metric recomputes). Returns rows touched."""
        n = 0
        with self._connect() as db:
            for time_ms, metrics in rows:
                n += db.execute("UPDATE samples SET metrics_json = ? WHERE time_ms = ?",
                                (_dumps(metrics), int(time_ms))).rowcount
        return n

    def log_fetch(self, status: str, rows_found: int, rows_new: int, duration_ms: int,
                  message: Optional[str] = None) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO fetch_log(fetched_at, status, rows_found, rows_new, duration_ms, message) "
                       "VALUES (?, ?, ?, ?, ?, ?)",
                       (int(time.time() * 1000), status, rows_found, rows_new, duration_ms, message))

    # -- reads -------------------------------------------------------------
    def last_fetch(self) -> Optional[dict]:
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT fetched_at, status, rows_found, rows_new, duration_ms, message "
                             "FROM fetch_log ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def count(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM samples").fetchone()[0])

    def time_bounds(self) -> Tuple[Optional[int], Optional[int]]:
        with self._connect() as db:
            row = db.execute("SELECT MIN(time_ms), MAX(time_ms) FROM samples").fetchone()
        return (int(row[0]), int(row[1])) if row and row[0] is not None else (None, None)

    def existing_times(self) -> Set[int]:
        with self._connect() as db:
            return {int(r[0]) for r in db.execute("SELECT time_ms FROM samples")}

    def iter_rows(self, from_ms: int, to_ms: int):
        """Yield (time_ms, metrics dict) for every raw row in [from_ms, to_ms], oldest first."""
        with self._connect() as db:
            for t, mj in db.execute("SELECT time_ms, metrics_json FROM samples WHERE time_ms BETWEEN ? AND ? "
                                    "ORDER BY time_ms", (from_ms, to_ms)):
                yield int(t), json.loads(mj)

    def rows_in_range(self, from_ms: int, to_ms: int, max_points: int = 1800) -> Tuple[List[dict], int]:
        """Rows in [from_ms, to_ms]. Above max_points they are averaged into
        whole-minute buckets. Returns (rows, bucket_ms) with bucket_ms == 0 for raw rows."""
        if to_ms < from_ms:
            from_ms, to_ms = to_ms, from_ms
        max_points = max(50, min(5000, max_points))
        with self._connect() as db:
            count = int(db.execute("SELECT COUNT(*) FROM samples WHERE time_ms BETWEEN ? AND ?",
                                   (from_ms, to_ms)).fetchone()[0])
            if count <= max_points:
                rows = db.execute("SELECT time_ms, metrics_json FROM samples WHERE time_ms BETWEEN ? AND ? "
                                  "ORDER BY time_ms", (from_ms, to_ms)).fetchall()
                return [{"time": int(t), "metrics": json.loads(m)} for t, m in rows], 0

            span_ms = max(60_000, to_ms - from_ms)
            bucket_ms = max(60_000, math.ceil(span_ms / max_points / 60_000) * 60_000)
            aggregate = db.execute(
                """
                SELECT CAST(s.time_ms / ? AS INTEGER) * ? AS bucket, metric.key, AVG(CAST(metric.value AS REAL))
                FROM samples AS s, json_each(s.metrics_json) AS metric
                WHERE s.time_ms BETWEEN ? AND ?
                GROUP BY bucket, metric.key ORDER BY bucket, metric.key
                """,
                (bucket_ms, bucket_ms, from_ms, to_ms),
            ).fetchall()
        grouped: Dict[int, Dict[str, float]] = {}
        for bucket, name, avg in aggregate:
            grouped.setdefault(int(bucket), {})[name] = float(avg)
        return [{"time": t, "metrics": m} for t, m in sorted(grouped.items())], bucket_ms

    # -- export ------------------------------------------------------------
    def export_csv(self) -> bytes:
        with self._connect() as db:
            rows = db.execute("SELECT time_ms, metrics_json FROM samples ORDER BY time_ms").fetchall()
        parsed = [(int(t), json.loads(m)) for t, m in rows]
        names = sorted({n for _t, metrics in parsed for n in metrics})
        out = io.StringIO(newline="")
        writer = csv.writer(out)
        writer.writerow(["timestamp_utc", "time_ms", *names])
        for time_ms, metrics in parsed:
            stamp = datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc).isoformat()
            writer.writerow([stamp, time_ms, *(metrics.get(n, "") for n in names)])
        return out.getvalue().encode("utf-8-sig")
