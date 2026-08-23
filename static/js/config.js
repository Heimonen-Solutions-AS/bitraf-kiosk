// Kiosk settings. Change things here, not in the other modules.
export const CONFIG = {
  windowHours: 24,        // time window shown in the charts
  maxPoints: 1500,        // points per initial fetch (24 h × 60 = 1440 → raw per-minute rows)
  pollFallbackMs: 20_000, // polling interval when SSE is unavailable
  catchupMs: 5 * 60_000,  // periodic incremental fetch to cover anything the stream missed
  redrawMs: 30_000,       // roll the time axis even when no new data arrived
  staleAfterMin: 15,      // banner: warn when the newest sample (any node) is older than this
  nodeQuietMin: 30,       // room card: show "last seen" when a device has not reported for this long
  // Per device type: minutes before "last seen" shows, matched case-insensitively as a
  // substring of the node's "manufacturer model" from the archive metadata; first match wins.
  nodeQuietMinByDevice: {
    "Aqara": 60,          // lumi.weather reports about once an hour
  },
  infoLink: "wiki.bitraf.no/wiki/Sensornettverk", // shown in the footer
  maxRoomCards: 3,        // more nodes than this → the cards rotate in a sliding window
  roomRotateMs: 30_000,   // how long each window of cards is shown
  roomFadeMs: 700,        // crossfade duration when the window advances
  statsRound: true,       // stats panel: first thing on page load, then after every full card pass
  statsDays: 7,           // statistics window (server /api/stats)
  statsIdleMin: 5,        // boards with no card rotation show a stats round this often instead
  // Chart lines of the devices whose cards are showing are drawn on top with a thin
  // ground-coloured casing on each side. Nothing else changes.
  highlightWidth: 2,      // same as every other line
  highlightCasing: 1.5,   // px of dark edge on each side (0 = off)
  // Chart y axis: the data plus a margin; a limit line is drawn only when it falls
  // inside that margin. Margin = max(yPad × data span, yPadBand × width of the good band).
  yPad: 0.15,
  yPadBand: 0.1,          // temperature: 0.7 °C, CO₂: 80 ppm, humidity: 3 %
  locale: "en-GB",
  eyebrow: "Bitraf · Indoor climate · Oslo, Norway",
  headline: "Air quality now",
  // Node ids or locations that skip the derived flatulence metric.
  flatulenceExclude: ["Room 217"],
  // Room names for nodes whose metadata lacks an ietf-system <location>. Key = node-id.
  rooms: {
    // "airthings0-ietf-hardware": "Electronics lab",
    // "airthings1-ietf-hardware": "Workshop",
  },
};
