"""Archive discovery, fetching and storage. New samples are published to
subscribers (the SSE stream in server.py)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
import logging
import queue
import threading
import time
from typing import Dict, List, Optional, Set, Tuple
from urllib import error, request
from urllib.parse import urljoin, urlparse

from .db import META_KEY, SensorDB
from .parser import ParseResult, Sample, parse_xml

log = logging.getLogger(__name__)
SOURCE_URL = "https://lightside-instruments.com/bitraf/data/"
LATEST_MINUTES = 2  # minutes fetched per poll: the newest and the one before it
ARCHIVE_DEPTH = 5  # year/month/day/hour/minute


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


class EventBus:
    """Simple fan-out: every subscriber gets its own bounded queue."""

    def __init__(self):
        self._subscribers: Set[queue.Queue] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=100)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, event: dict) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass  # slow client; the SSE reconnect + catch-up poll recovers

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._subscribers)


class Poller:
    def __init__(self, db: SensorDB, source_url: str = SOURCE_URL, interval_seconds: int = 60, timeout: int = 20):
        self.db = db
        self.source_url = source_url if source_url.endswith("/") else source_url + "/"
        self.interval_seconds = max(1, interval_seconds)
        self.timeout = timeout
        self.events = EventBus()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_error: Optional[str] = None
        self.last_backfill_errors: Dict[str, str] = {}

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="poller")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception as exc:  # noqa: BLE001 - log and keep going
                log.warning("poll failed: %s", exc)
            # Align to the next whole interval (+5 s) so we hit just after each new minute.
            wait = self.interval_seconds - (time.time() % self.interval_seconds) + 5
            self._stop.wait(wait)

    # -- HTTP --------------------------------------------------------------
    def fetch_raw(self, url: str) -> str:
        try:
            with request.urlopen(url, timeout=self.timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except error.URLError as exc:
            raise RuntimeError(f"network error: {exc}") from exc

    def _links(self, index_url: str) -> List[str]:
        parser = _LinkParser()
        parser.feed(self.fetch_raw(index_url))
        return [urljoin(index_url, href) for href in parser.hrefs]

    def _numeric_dirs(self, index_url: str) -> List[str]:
        base = urlparse(index_url).path.rstrip("/") + "/"

        def is_child(url: str) -> bool:
            path = urlparse(url).path
            if not path.startswith(base) or not path.endswith("/"):
                return False
            rel = path[len(base):].rstrip("/")
            return bool(rel) and "/" not in rel and rel.isdigit()

        return sorted(u for u in self._links(index_url) if is_child(u))

    # -- discovery ---------------------------------------------------------
    def discover_latest_url(self) -> str:
        """Newest minute directory containing data.xml. Steps back one minute
        if the newest directory exists but data.xml is not written yet."""
        current = self.source_url
        for _ in range(ARCHIVE_DEPTH - 1):
            dirs = self._numeric_dirs(current)
            if not dirs:
                raise RuntimeError(f"no data directory under {current}")
            current = dirs[-1]
        minutes = self._numeric_dirs(current)
        if not minutes:
            raise RuntimeError(f"no minute directory under {current}")
        for minute_dir in reversed(minutes[-2:]):
            data_url = urljoin(minute_dir, "data.xml")
            if data_url in self._links(minute_dir):
                return data_url
        raise RuntimeError(f"data.xml not found under {current}")

    def url_for_time(self, time_ms: int) -> str:
        t = datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc)
        return f"{self.source_url}{t:%Y/%m/%d/%H/%M}/data.xml"

    def discover_latest_urls(self, count: int = LATEST_MINUTES) -> List[str]:
        """The newest data.xml plus the `count - 1` minutes before it, newest first.

        Derived by time arithmetic rather than directory listing so the list
        crosses hour and day boundaries.
        """
        newest = self.discover_latest_url()
        t = self.time_from_url(newest)
        if t is None:
            return [newest]
        return [newest] + [self.url_for_time(t - 60_000 * i) for i in range(1, count)]

    def discover_all_urls(self) -> List[str]:
        dirs = [self.source_url]
        for _ in range(ARCHIVE_DEPTH):
            dirs = [child for d in dirs for child in self._numeric_dirs(d)]
        return sorted(urljoin(d, "data.xml") for d in dirs)

    @staticmethod
    def time_from_url(data_url: str) -> Optional[int]:
        parts = urlparse(data_url).path.strip("/").split("/")
        try:
            year, month, day, hour, minute = map(int, parts[-6:-1])
            return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp() * 1000)
        except (ValueError, IndexError):
            return None

    # -- fetching ----------------------------------------------------------
    def parse_url(self, data_url: str) -> ParseResult:
        return parse_xml(self.fetch_raw(data_url), fallback_time_ms=self.time_from_url(data_url))

    def parse_latest(self) -> ParseResult:
        return self.parse_url(self.discover_latest_url())

    def _store_metadata(self, parsed: ParseResult) -> None:
        """Merge the newest snapshot's metadata over what we already know.

        Nodes and metrics absent from the newest data.xml (offline, mid-reconfig)
        keep their last-known entries, so names stay stable while a device is quiet.
        """
        try:
            merged = dict(self.db.get_meta(META_KEY) or {})
            for section in ("nodes", "metrics"):
                known = dict(merged.get(section) or {})
                known.update(parsed.metadata.get(section) or {})
                merged[section] = known
            for key, value in parsed.metadata.items():
                if key not in ("nodes", "metrics"):
                    merged[key] = value
            merged["updatedAt"] = int(time.time() * 1000)
            self.db.set_meta(META_KEY, merged)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not store metadata: %s", exc)

    def poll(self) -> ParseResult:
        """Fetch the newest snapshots, store them, publish them, and log the attempt.

        The origin creates the minute directory and data.xml a second or two into
        the minute but only fills the file some 10-40 s later, so the newest minute
        is often empty when we look. The minute before it is fetched as well and
        whatever parses is stored (the DB drops duplicates), so a minute that is not
        readable yet is picked up by the next poll. Returns the newest parsed result.
        """
        start = time.time()
        rows_new = 0
        results: List[ParseResult] = []
        status, message = "ok", None
        try:
            errors: List[str] = []
            for url in self.discover_latest_urls():
                try:
                    results.append(self.parse_url(url))
                except Exception as exc:  # noqa: BLE001 - an unreadable minute is expected
                    errors.append(f"{url}: {exc}")
            if not results:
                raise RuntimeError("; ".join(errors))
            new = self.db.insert_samples([r.sample for r in results])
            rows_new = len(new)
            self._store_metadata(results[0])
            if new:
                self.events.publish({"type": "samples", "records": [s.as_dict() for s in new],
                                     "metadata": results[0].metadata})
            self.last_error = None
            return results[0]
        except Exception as exc:
            status, message = "error", str(exc)
            self.last_error = message
            raise
        finally:
            duration_ms = int((time.time() - start) * 1000)
            try:
                self.db.log_fetch(status, len(results), rows_new, duration_ms, message)
            except Exception as exc:  # noqa: BLE001
                log.warning("could not write fetch log: %s", exc)

    def backfill(self, workers: int = 8, attempts: int = 3) -> Tuple[int, int, int]:
        """Fetch every archived minute missing from the DB.

        Returns (files_parsed, rows_inserted, files_failed).
        """
        existing = self.db.existing_times()
        pending = [u for u in self.discover_all_urls() if self.time_from_url(u) not in existing]
        log.info("backfill: %d files to fetch", len(pending))
        samples: List[Sample] = []
        failures: Dict[str, str] = {}
        newest: Optional[ParseResult] = None
        for _ in range(attempts):
            if not pending:
                break
            failures = {}
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(self.parse_url, u): u for u in pending}
                for future in as_completed(futures):
                    url = futures[future]
                    try:
                        parsed = future.result()
                    except Exception as exc:  # noqa: BLE001
                        failures[url] = str(exc)
                        continue
                    samples.append(parsed.sample)
                    if newest is None or parsed.sample.time_ms > newest.sample.time_ms:
                        newest = parsed
            pending = list(failures)
        samples.sort(key=lambda s: s.time_ms)
        inserted = self.db.insert_samples(samples)
        if newest is not None:
            self._store_metadata(newest)
        if inserted:
            self.events.publish({"type": "reload"})
        self.last_backfill_errors = failures
        message = f"{len(failures)} files failed after {attempts} attempts" if failures else "all files parsed"
        self.db.log_fetch("backfill", len(samples), len(inserted), 0, message)
        return len(samples), len(inserted), len(failures)
