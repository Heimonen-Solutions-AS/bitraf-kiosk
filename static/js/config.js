// Kiosk settings. Change things here, not in the other modules.
export const CONFIG = {
  windowHours: 24,        // time window shown in the charts
  maxPoints: 1500,        // points per initial fetch (24 h × 60 = 1440 → raw per-minute rows)
  pollFallbackMs: 20_000, // polling interval when SSE is unavailable
  catchupMs: 5 * 60_000,  // periodic incremental fetch to cover anything the stream missed
  redrawMs: 30_000,       // roll the time axis even when no new data arrived
  staleAfterMin: 15,      // banner: warn when the newest sample (any node) is older than this
  nodeQuietMin: 30,       // room card: show "last seen" when a device has not reported for this long
  infoLink: "wiki.bitraf.no/wiki/Sensornettverk", // shown in the footer
  maxRoomCards: 3,        // more nodes than this → the cards rotate in a sliding window
  roomRotateMs: 30_000,   // how long each window of cards is shown
  roomFadeMs: 700,        // crossfade duration when the window advances
  locale: "en-GB",
  eyebrow: "Bitraf · Indoor climate — Oslo, Norway",
  headline: "Air quality now",
  // Room names for nodes whose metadata lacks an ietf-system <location>. Key = node-id.
  rooms: {
    // "airthings0-ietf-hardware": "Electronics lab",
    // "airthings1-ietf-hardware": "Workshop",
  },
};
