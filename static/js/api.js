// Data source: initial range fetch, then a server-sent-events stream for new
// samples, with incremental polling as fallback and a periodic catch-up.
import { CONFIG } from "./config.js";

async function fetchJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

export class LiveSource {
  /**
   * handlers: { onInitial(payload, meta), onSamples(records, meta), onReload(), onState(mode, detail) }
   * mode: "sse" | "poll" | "off"
   */
  constructor(handlers) {
    this.h = handlers;
    this.lastMs = 0;
    this.es = null;
    this.pollTimer = null;
    this.catchupTimer = null;
  }

  async start() {
    await this.loadInitial();
    this.connectStream();
    this.catchupTimer = setInterval(() => this.fetchSince().catch(() => {}), CONFIG.catchupMs);
  }

  async loadInitial() {
    const nowMs = Date.now();
    const fromMs = nowMs - CONFIG.windowHours * 3600_000;
    const [payload, meta] = await Promise.all([
      fetchJSON(`/api/data?fromMs=${fromMs}&toMs=${nowMs + 60_000}&maxPoints=${CONFIG.maxPoints}`),
      fetchJSON("/api/meta"),
    ]);
    for (const r of payload.records || []) if (r.time > this.lastMs) this.lastMs = r.time;
    this.h.onInitial(payload, meta);
  }

  /** Incremental fetch of everything newer than what we have. */
  async fetchSince() {
    const payload = await fetchJSON(`/api/data?fromMs=${this.lastMs + 1}&toMs=${Date.now() + 60_000}&maxPoints=${CONFIG.maxPoints}`);
    const records = payload.records || [];
    if (records.length) {
      for (const r of records) if (r.time > this.lastMs) this.lastMs = r.time;
      const meta = await fetchJSON("/api/meta").catch(() => null);
      this.h.onSamples(records, meta);
    }
    return records.length;
  }

  connectStream() {
    if (!("EventSource" in window)) return this.startPolling("no EventSource");
    this.stopPolling();
    this.es = new EventSource("/api/events");
    this.es.addEventListener("hello", () => {
      this.h.onState("sse", "live");
      this.fetchSince().catch(() => {}); // cover the gap between initial load and stream start
    });
    this.es.addEventListener("samples", (ev) => {
      const data = JSON.parse(ev.data);
      const records = data.records || [];
      for (const r of records) if (r.time > this.lastMs) this.lastMs = r.time;
      this.h.onSamples(records, data.metadata);
    });
    this.es.addEventListener("reload", () => this.h.onReload());
    this.es.onerror = () => {
      // EventSource reconnects by itself; poll meanwhile so the board stays fresh.
      this.h.onState("poll", "reconnecting");
      this.startPolling("stream lost");
    };
    this.es.onopen = () => this.stopPolling();
  }

  startPolling(reason) {
    if (this.pollTimer) return;
    this.h.onState("poll", `polling (${reason})`);
    this.pollTimer = setInterval(async () => {
      try { await this.fetchSince(); this.h.onState("poll", "polling"); }
      catch (err) { this.h.onState("off", `server unreachable: ${err.message}`); }
    }, CONFIG.pollFallbackMs);
  }

  stopPolling() {
    if (this.pollTimer) { clearInterval(this.pollTimer); this.pollTimer = null; }
    if (this.es && this.es.readyState === EventSource.OPEN) this.h.onState("sse", "live");
  }
}

/** Static snapshot embedded by tools/build_preview.py: behaves like a frozen live source. */
export class SnapshotSource {
  constructor(snapshot, handlers) { this.snapshot = snapshot; this.h = handlers; }
  async start() {
    this.h.onInitial(this.snapshot.data, this.snapshot.meta);
    this.h.onState("off", "preview – static snapshot");
  }
}
