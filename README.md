# Bitraf indoor-climate kiosk

Polls the minute-by-minute sensor archive at
`https://lightside-instruments.com/bitraf/data/YYYY/MM/DD/HH/MM/data.xml`,
stores one row per minute in SQLite and serves a portrait kiosk display that
updates live (server-sent events) without any visual interruption.

No dependencies beyond the Python 3.9+ standard library.

```
python bitraf_kiosk.py                 # serve on http://0.0.0.0:8006/, poll every 60 s
python bitraf_kiosk.py --backfill      # first fetch the whole archive (~27k files, a few minutes)
python bitraf_kiosk.py --source http://127.0.0.1/data/   # on the Pi that writes the archive: read it locally, no lag
python tools/seed_recent.py --hours 26 # quick alternative: last 26 h, every 5th minute
python -m unittest discover -s tests   # run the tests
python tools/build_preview.py          # single-file HTML with a frozen snapshot (for sharing)
```

Open `http://<host>:8006/` full-screen on the kiosk (portrait; landscape also works).

## Layout

```
bitraf/            Python package
  parser.py        data.xml → {node.sensor: value} + node/sensor metadata
  db.py            SQLite (samples, fetch_log, metadata)
  poller.py        archive discovery, fetch loop, backfill, event bus
  server.py        static files, JSON API, /api/events (SSE)
  cli.py           argument parsing / entry point
static/
  index.html       kiosk shell
  css/kiosk.css    styling (visual language from heimonen.no)
  js/config.js     settings: window, refresh, thresholds override, room names
  js/sensors.js    sensor types, units, Airthings thresholds, status logic
  js/model.js      in-memory store + per-node/per-sensor view model
  js/chart.js      canvas line chart (grid, thresholds, end labels, hover)
  js/ui.js         header, alert banner, room cards, chart grid, footer (in-place updates)
  js/api.js        initial fetch → SSE stream, polling fallback, periodic catch-up
  js/main.js       wiring
tests/             unittest suite (parser fixture with the malformed radon component)
tools/             seed_recent.py, build_preview.py
```

## API

| Route | Description |
|---|---|
| `GET /api/data?fromMs&toMs&maxPoints` | rows in range; averaged into minute buckets above `maxPoints` |
| `GET /api/meta` | node/sensor metadata (description, location, model, serial, units) + status |
| `GET /api/status` | DB row count, last fetch, SSE client count |
| `GET /api/events` | SSE stream: `hello`, `samples` (new rows + metadata), `reload` |
| `GET /api/export.csv` | whole database as CSV |
| `POST /api/poll`, `POST /api/backfill` | trigger a fetch / a full backfill |

## Room names

The kiosk names each card from the node's ietf-system `<location>` in the
data, then `CONFIG.rooms[nodeId]` in `static/js/config.js`, then the node
`<description>`. Right now only `sensor-pi0` publishes a location
("First floor: 217"); the two Airthings units don't, so their cards show the
serial number (printed on the device) and a "location not set" tag until a
`<location>` is added on those NETCONF nodes.

## Data quirks handled

* `radon-short-term-average` is missing its `<component>` wrapper upstream
  (it sits inside the pressure component) – the parser pairs each
  `<sensor-data>` with the preceding `<name>`.
* On 2026-08-05/06 `airthings0` temperature was reported as e.g. `2360 milli`
  (2.36 °C); values in (-10, 10) for that node are scaled ×10.
* lsi-thermometers (`th0`) report hundredths of a degree.
* The archive path timestamp is used as the sample time; sensors' own
  `value-timestamp` can lag by minutes.
