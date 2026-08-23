"""HTTP service: static kiosk files, JSON API and a server-sent-events stream."""
from __future__ import annotations

from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
from pathlib import Path
import queue
import threading
import time
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

from .db import META_KEY, SensorDB
from .poller import Poller
from .stats import weekly_stats

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
SSE_HEARTBEAT_SEC = 15
STATS_TTL_SEC = 600


def chart_payload(rows: List[dict], **extra) -> dict:
    names: List[str] = []
    seen = set()
    for row in rows:
        for name in row["metrics"]:
            if name not in seen:
                seen.add(name)
                names.append(name)
    return {"records": rows, "metricNames": names, "count": len(rows), **extra}


class KioskHandler(BaseHTTPRequestHandler):
    poller: Poller
    db: SensorDB
    static_dir: Path = STATIC_DIR
    protocol_version = "HTTP/1.1"

    # -- response helpers --------------------------------------------------
    def _send(self, body: bytes, content_type: str, status: int = HTTPStatus.OK, headers: Optional[dict] = None) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(body, "application/json; charset=utf-8", status,
                   {"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"})

    def _static(self, relative: str) -> None:
        root = self.static_dir.resolve()
        target = (root / relative.lstrip("/")).resolve()
        if root not in target.parents or not target.is_file():
            return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "text/javascript"):
            content_type += "; charset=utf-8"
        self._send(target.read_bytes(), content_type, headers={"Cache-Control": "no-cache"})

    @staticmethod
    def _int(value: Optional[str], default: int, min_value: int = 0) -> int:
        try:
            return max(min_value, int(value))
        except (TypeError, ValueError):
            return default

    # -- API builders ------------------------------------------------------
    def _status(self) -> dict:
        return {"databaseRows": self.db.count(), "lastFetch": self.db.last_fetch(),
                "pollIntervalSec": self.poller.interval_seconds, "sourceUrl": self.poller.source_url,
                "lastError": self.poller.last_error, "sseClients": self.poller.events.size,
                "serverTime": int(time.time() * 1000)}

    def _meta(self) -> dict:
        meta = self.db.get_meta(META_KEY) or {"nodes": {}, "metrics": {}}
        lo, hi = self.db.time_bounds()
        meta["bounds"] = {"min": lo, "max": hi}
        meta["status"] = self._status()
        return meta

    def _range(self, from_ms: int, to_ms: int, max_points: int) -> dict:
        rows, bucket_ms = self.db.rows_in_range(from_ms, to_ms, max_points=max_points)
        lo, hi = self.db.time_bounds()
        return chart_payload(rows, bounds={"min": lo, "max": hi}, requestedRange={"min": from_ms, "max": to_ms},
                             aggregated=bucket_ms > 0, bucketMs=bucket_ms)

    # -- SSE ---------------------------------------------------------------
    def _events(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        q = self.poller.events.subscribe()
        try:
            self._sse("hello", {"serverTime": int(time.time() * 1000)})
            while True:
                try:
                    event = q.get(timeout=SSE_HEARTBEAT_SEC)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                self._sse(event.get("type", "message"), event)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.poller.events.unsubscribe(q)

    def _sse(self, event: str, payload: dict) -> None:
        self.wfile.write(f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.flush()

    # -- routes ------------------------------------------------------------
    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path.rstrip("/") or "/"
        q = {k: v[0] for k, v in parse_qs(url.query).items()}

        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path == "/api/events":
            return self._events()
        if path == "/api/meta":
            return self._json(self._meta())
        if path == "/api/status":
            return self._json(self._status())
        if path == "/api/data":
            if "fromMs" in q:
                from_ms = self._int(q.get("fromMs"), 0)
                to_ms = self._int(q.get("toMs"), int(time.time() * 1000) + 60_000)
                max_points = min(5000, self._int(q.get("maxPoints"), 1800, min_value=50))
                return self._json(self._range(from_ms, to_ms, max_points))
            lo, hi = self.db.time_bounds()
            if lo is None:
                return self._json(chart_payload([], bounds={"min": None, "max": None}))
            return self._json(self._range(lo, hi, min(5000, self._int(q.get("maxPoints"), 1800, min_value=50))))
        if path == "/api/stats":
            days = self._int(q.get("days"), 7, min_value=1)
            return self._json(self._stats(min(31, days)))
        if path == "/api/export.csv":
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            return self._send(self.db.export_csv(), "text/csv; charset=utf-8", headers={
                "Content-Disposition": f'attachment; filename="bitraf-sensor-data-{stamp}.csv"',
                "Cache-Control": "no-store"})
        self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    _stats_lock = threading.Lock()
    _stats_cache: dict = {}  # days -> (monotonic_time, payload)

    def _stats(self, days: int) -> dict:
        with KioskHandler._stats_lock:
            hit = KioskHandler._stats_cache.get(days)
            if hit and time.monotonic() - hit[0] < STATS_TTL_SEC:
                return hit[1]
        payload = weekly_stats(self.db, days=days)
        with KioskHandler._stats_lock:
            KioskHandler._stats_cache[days] = (time.monotonic(), payload)
        return payload

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        if path == "/api/poll":
            try:
                result = self.poller.poll()
                return self._json({"ok": True, "sampleTime": result.sample.time_ms, "metrics": len(result.sample.metrics)})
            except Exception as exc:  # noqa: BLE001
                return self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_GATEWAY)
        if path == "/api/backfill":
            try:
                parsed, inserted, failed = self.poller.backfill()
                return self._json({"ok": True, "filesParsed": parsed, "rowsNew": inserted, "filesFailed": failed,
                                   "errors": [{"url": u, "error": e} for u, e in
                                              list(self.poller.last_backfill_errors.items())[:20]]})
            except Exception as exc:  # noqa: BLE001
                return self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_GATEWAY)
        self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, fmt, *args):
        log.debug("%s " + fmt, self.address_string(), *args)


def make_server(host: str, port: int, db: SensorDB, poller: Poller, static_dir: Optional[Path] = None) -> ThreadingHTTPServer:
    handler = type("BoundKioskHandler", (KioskHandler,), {"db": db, "poller": poller, "static_dir": static_dir or STATIC_DIR})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server
