#!/usr/bin/env python3
"""Bundle the kiosk into ONE self-contained HTML file with a frozen data
snapshot, for sharing/previewing without the server.

    python tools/build_preview.py --db bitraf_data.sqlite --out preview.html

The ES modules under static/js are concatenated in dependency order with
their import/export statements stripped (all top-level names are unique).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bitraf.db import META_KEY, SensorDB  # noqa: E402
from bitraf.server import chart_payload  # noqa: E402

MODULE_ORDER = ["config", "format", "sensors", "model", "chart", "ui", "api", "main"]


def bundle_js() -> str:
    parts = []
    for name in MODULE_ORDER:
        src = (ROOT / "static" / "js" / f"{name}.js").read_text()
        src = re.sub(r'^import\s.*?from\s+"\./[^"]+";\s*$', "", src, flags=re.M)
        src = re.sub(r"^export\s+", "", src, flags=re.M)
        parts.append(f"// ---- {name}.js ----\n{src}")
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=ROOT / "bitraf_data.sqlite")
    ap.add_argument("--out", type=Path, default=ROOT / "preview.html")
    ap.add_argument("--hours", type=float, default=24)
    args = ap.parse_args()

    db = SensorDB(args.db)
    lo, hi = db.time_bounds()
    if hi is None:
        print("database is empty", file=sys.stderr)
        return 1
    now_ms = hi
    rows, bucket_ms = db.rows_in_range(int(now_ms - args.hours * 3600_000), now_ms, max_points=1500)
    meta = db.get_meta(META_KEY) or {"nodes": {}, "metrics": {}}
    meta["status"] = {"sourceUrl": "https://lightside-instruments.com/bitraf/data/", "serverTime": int(time.time() * 1000)}
    snapshot = {"nowMs": now_ms, "data": chart_payload(rows, aggregated=bucket_ms > 0, bucketMs=bucket_ms), "meta": meta}

    html = (ROOT / "static" / "index.html").read_text()
    css = (ROOT / "static" / "css" / "kiosk.css").read_text()
    html = html.replace('<link rel="stylesheet" href="/static/css/kiosk.css">', f"<style>\n{css}\n</style>")
    html = html.replace('<script id="snapshot" type="application/json"></script>',
                        '<script id="snapshot" type="application/json">' + json.dumps(snapshot).replace("</", "<\\/") + "</script>")
    html = html.replace('<script type="module" src="/static/js/main.js"></script>',
                        f'<script type="module">\n{bundle_js()}\n</script>')
    args.out.write_text(html)
    print(f"wrote {args.out} ({args.out.stat().st_size // 1024} kB, {len(rows)} rows, snapshot at {now_ms})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
