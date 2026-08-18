"""Command line entry point: `python -m bitraf` or `python bitraf_kiosk.py`."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .db import SensorDB
from .poller import SOURCE_URL, Poller
from .server import make_server

log = logging.getLogger("bitraf")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bitraf indoor-climate kiosk: poller + SQLite + web UI")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8006)
    parser.add_argument("--db", type=Path, default=Path("bitraf_data.sqlite"))
    parser.add_argument("--source", default=SOURCE_URL)
    parser.add_argument("--interval", type=int, default=60, help="seconds between fetches")
    parser.add_argument("--backfill", action="store_true", help="fetch the whole archive on startup")
    parser.add_argument("--once", action="store_true", help="fetch once (or backfill) and exit")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db = SensorDB(args.db)
    db.initialize()
    poller = Poller(db=db, source_url=args.source, interval_seconds=args.interval)
    log.info("database %s (%d rows)", args.db.resolve(), db.count())

    if args.backfill or args.once:
        try:
            if args.backfill:
                log.info("starting backfill (this can take a few minutes)")
                parsed, inserted, failed = poller.backfill()
                log.info("backfill: %d files parsed, %d new rows, %d failed", parsed, inserted, failed)
            else:
                poller.poll()
                log.info("poll done")
        except Exception as exc:  # noqa: BLE001
            log.error("backfill/poll failed: %s", exc)
            if args.once:
                return 1
    if args.once:
        return 0

    poller.start()
    server = make_server(args.host, args.port, db, poller)
    log.info("kiosk ready at http://%s:%d/", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        poller.stop()
        server.server_close()
    return 0
