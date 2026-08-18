// Wire the data source to the UI. The board re-renders in place on every new
// sample; the time axis rolls forward on a timer even when nothing new arrives.
import { CONFIG } from "./config.js";
import { LiveSource, SnapshotSource } from "./api.js";
import { $ } from "./format.js";
import { addDerivedMetrics } from "./derived.js";
import { Store } from "./model.js";
import { Banner, Charts, Footer, Header, Rooms } from "./ui.js";

// Per-screen safe-area override: ?inset=4 (percent, both axes) or ?insetX=2&insetY=5.
{
  const q = new URLSearchParams(location.search);
  const both = q.get("inset"), x = q.get("insetX") ?? both, y = q.get("insetY") ?? both;
  if (x != null && !Number.isNaN(+x)) document.documentElement.style.setProperty("--inset-x", `${+x}vw`);
  if (y != null && !Number.isNaN(+y)) document.documentElement.style.setProperty("--inset-y", `${+y}vh`);
}

const store = new Store();
const header = new Header();
const banner = new Banner();
const rooms = new Rooms();
const charts = new Charts();
const footer = new Footer();

let snapshot = null;
try { const raw = $("#snapshot").textContent.trim(); if (raw) snapshot = JSON.parse(raw); } catch (e) { console.warn("snapshot parse failed", e); }
const now = () => (snapshot ? snapshot.nowMs : Date.now());

function render() {
  const nowMs = now();
  store.prune(nowMs);
  const nodes = store.model(nowMs);
  addDerivedMetrics(nodes, store.meta);
  const gapMs = Math.max(store.bucketMs * 3.5, 6 * 60_000); // break lines across missing minutes
  banner.update(nodes, store.meta, store.latestMs, nowMs);
  rooms.update(nodes, store.meta, nowMs);
  charts.update(nodes, store.meta, nowMs - CONFIG.windowHours * 3600_000, nowMs, gapMs);
  footer.setRange(store, store.latestMs);
  footer.setSource(snapshot?.nowMs);
}

const handlers = {
  onInitial(payload, meta) { store.replace(payload, meta); render(); },
  onSamples(records, meta) { if (meta) store.setMeta(meta); if (store.append(records)) render(); },
  onReload() { source.loadInitial().catch((err) => banner.error(`Reload failed: ${err.message}`)); },
  onState(mode, detail) { footer.setLive(mode, detail); },
};

const source = snapshot ? new SnapshotSource(snapshot, handlers) : new LiveSource(handlers);
source.start().catch((err) => {
  console.error(err);
  banner.error(`Cannot reach the kiosk service: ${err.message}`);
  footer.setLive("off", "offline – retrying");
  setTimeout(() => source.start().catch(() => {}), CONFIG.pollFallbackMs);
});

// Roll the axis / stale check even without new data.
setInterval(() => { if (store.rows.size) render(); }, CONFIG.redrawMs);
window.addEventListener("resize", () => charts.redraw());
