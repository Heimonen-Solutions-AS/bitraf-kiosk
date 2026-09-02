// Data store: rows (time → metrics) + metadata, and the derived per-node/per-sensor model.
import { CONFIG } from "./config.js";
import { cssVar } from "./format.js";
import { generatedColor } from "./palette.js";
import { sensorType, statusOf, typeInfo } from "./sensors.js";

const SERIES_SLOTS = Array.from({ length: 32 }, (_, i) => `--s${i + 1}`); // --s1..--s32 in kiosk.css

export class Store {
  constructor() {
    this.rows = new Map();     // time → metrics
    this.meta = { nodes: {}, metrics: {} };
    this.bucketMs = 60_000;
    this.aggregated = false;
    this.latestMs = null;
    this.slotByNode = new Map();
  }

  windowMs() { return CONFIG.windowHours * 3600_000; }

  /** Replace everything (initial load / reload). */
  replace(payload, meta) {
    this.rows.clear();
    this.latestMs = null;
    this.bucketMs = payload.bucketMs || 60_000;
    this.aggregated = !!payload.aggregated;
    this.append(payload.records || []);
    if (meta) this.setMeta(meta);
  }

  /** Add rows (SSE / incremental polling). Returns how many were new. */
  append(records) {
    let added = 0;
    for (const r of records) {
      if (!this.rows.has(r.time)) added++;
      this.rows.set(r.time, r.metrics);
      if (this.latestMs == null || r.time > this.latestMs) this.latestMs = r.time;
    }
    return added;
  }

  setMeta(meta) {
    if (meta.nodes) this.meta.nodes = { ...this.meta.nodes, ...meta.nodes };
    if (meta.metrics) this.meta.metrics = { ...this.meta.metrics, ...meta.metrics };
    if (meta.status) this.meta.status = meta.status;
  }

  prune(nowMs) {
    const cutoff = nowMs - this.windowMs() - 3600_000;
    for (const t of this.rows.keys()) if (t < cutoff) this.rows.delete(t);
  }

  /** Stable colour slot per node: first appearance claims the next free slot.
   *  The fixed palette covers the first 32; after that colours are generated, never reused. */
  colorFor(nodeId) {
    if (!this.slotByNode.has(nodeId)) this.slotByNode.set(nodeId, this.slotByNode.size);
    const slot = this.slotByNode.get(nodeId);
    return slot < SERIES_SLOTS.length ? cssVar(SERIES_SLOTS[slot]) : generatedColor(slot);
  }

  /**
   * View model: Map(nodeId → { id, color, sensors: Map(type → series) }).
   * Series are limited to [nowMs - window, nowMs].
   */
  model(nowMs) {
    const times = [...this.rows.keys()].sort((a, b) => a - b);
    const cutoff = nowMs - this.windowMs();
    const nodeIds = new Set(Object.keys(this.meta.nodes || {}));
    const keys = new Set();
    for (const t of times) if (t >= cutoff) for (const k of Object.keys(this.rows.get(t))) keys.add(k);
    for (const k of keys) nodeIds.add(k.slice(0, k.indexOf(".")));

    const nodes = new Map();
    for (const id of [...nodeIds].sort()) nodes.set(id, { id, color: this.colorFor(id), sensors: new Map(), lastSeen: null });

    for (const key of keys) {
      const dot = key.indexOf(".");
      const nodeId = key.slice(0, dot), sensor = key.slice(dot + 1);
      const type = sensorType(sensor, (this.meta.metrics || {})[key]?.unitsDisplay);
      const values = [];
      for (const t of times) {
        if (t < cutoff) continue;
        const v = this.rows.get(t)[key];
        if (v != null) values.push([t, v]);
      }
      if (!values.length) continue;
      const last = values[values.length - 1];
      let prev = null; // value ~1 h before the latest, for the trend arrow
      for (const p of values) { if (p[0] <= last[0] - 3600_000) prev = p; else break; }
      const node = nodes.get(nodeId);
      let info = typeInfo(type, key, this.meta);
      // ppb estimated from the index (bitraf/vocppb.py): charted with the real
      // VOC series, but labeled honestly wherever this device's row shows up
      if (sensor === "voc-est") info = { ...info, label: "VOC estimate" };
      node.sensors.set(type, {
        key, type, sensor, values, last, prev,
        info, status: statusOf(type, last[1]),
      });
      // A device can die while the gateway keeps republishing its last value, so
      // prefer the sensor's own value-timestamp (Airthings) over the archive row time.
      const own = (this.meta.metrics || {})[key]?.valueTimestamp;
      const seen = own && this.latestMs && last[0] >= this.latestMs ? Math.min(own, last[0]) : last[0];
      if (node.lastSeen == null || seen > node.lastSeen) node.lastSeen = seen;
    }
    return nodes;
  }
}
