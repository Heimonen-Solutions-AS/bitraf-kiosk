#!/usr/bin/env python3
"""Quickly seed the database with recent archive data (a subsample), so the
kiosk has something to show before a full --backfill has run.

    python tools/seed_recent.py --hours 26 --step 5 --db bitraf_data.sqlite
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bitraf.db import SensorDB  # noqa: E402
from bitraf.poller import SOURCE_URL, Poller  # noqa: E402

log = logging.getLogger("seed")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=Path("bitraf_data.sqlite"))
    ap.add_argument("--hours", type=float, default=26)
    ap.add_argument("--step", type=int, default=5, help="minutes between fetched snapshots")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--source", default=SOURCE_URL)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    db = SensorDB(args.db)
    db.initialize()
    poller = Poller(db, args.source)
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(hours=args.hours)
    existing = db.existing_times()
    urls = []
    t = start
    while t <= end:
        if int(t.timestamp() * 1000) not in existing:
            urls.append(f"{poller.source_url}{t:%Y/%m/%d/%H/%M}/data.xml")
        t += timedelta(minutes=args.step)
    log.info("fetching %d snapshots", len(urls))
    samples, newest, failed = [], None, 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(poller.parse_url, u): u for u in urls}
        for f in as_completed(futures):
            try:
                r = f.result()
            except Exception:  # noqa: BLE001 - missing minutes are expected
                failed += 1
                continue
            samples.append(r.sample)
            if newest is None or r.sample.time_ms > newest.sample.time_ms:
                newest = r
    inserted = db.insert_samples(samples)
    if newest:
        poller._store_metadata(newest)  # noqa: SLF001
    log.info("inserted %d rows (%d failed); database now has %d rows", len(inserted), failed, db.count())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
