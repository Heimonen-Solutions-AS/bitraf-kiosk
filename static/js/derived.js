// Derived (computed) metrics — added to each node's sensor map after the raw
// series are built, so they get cards, charts, thresholds and alerts for free.
import { CONFIG } from "./config.js";
import { statusOf, typeInfo } from "./sensors.js";

/**
 * Flatulence index, ppm (tongue-in-cheek but deterministic and explainable):
 *
 *   excessVoc  = max(0, voc - baseline)          baseline = 10th percentile of the
 *                                                room's VOC over the window (ppb)
 *   occupancy  = clamp((co2 - 420) / 600, 0, 1)  nobody in the room → nobody farting
 *   humidBoost = 1 + clamp((rh - 40) / 100, -0.2, 0.3)  humid air holds odour longer
 *   fart_ppm   = excessVoc / 1000 * occupancy * humidBoost
 *
 * Needs voc + co2 (humidity optional). Result is in ppm (VOC ppb / 1000).
 */
function percentile(values, p) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))];
}
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

export function flatulenceSeries(voc, co2, humidity) {
  const baseline = percentile(voc.values.map(([, v]) => v), 0.10);
  const co2At = new Map(co2.values);
  const rhAt = humidity ? new Map(humidity.values) : null;
  const out = [];
  for (const [t, v] of voc.values) {
    const c = co2At.get(t);
    if (c == null) continue;
    const occupancy = clamp((c - 420) / 600, 0, 1);
    const rh = rhAt?.get(t);
    const humidBoost = rh == null ? 1 : 1 + clamp((rh - 40) / 100, -0.2, 0.3);
    out.push([t, Math.round((Math.max(0, v - baseline) / 1000) * occupancy * humidBoost * 1000) / 1000]);
  }
  return out;
}

/** Mutates `nodes` (Map from Store.model) adding derived sensors where inputs exist. */
export function addDerivedMetrics(nodes, meta) {
  const excluded = new Set((CONFIG.flatulenceExclude || []).map((s) => s.toLowerCase()));
  for (const node of nodes.values()) {
    const location = ((meta.nodes || {})[node.id] || {}).location || "";
    if (excluded.has(node.id.toLowerCase()) || excluded.has(location.toLowerCase())) continue;
    const voc = node.sensors.get("voc"), co2 = node.sensors.get("co2");
    if (!voc || !co2) continue;
    const values = flatulenceSeries(voc, co2, node.sensors.get("humidity"));
    if (!values.length) continue;
    const last = values[values.length - 1];
    let prev = null;
    for (const p of values) { if (p[0] <= last[0] - 3600_000) prev = p; else break; }
    const key = `${node.id}.flatulence`;
    node.sensors.set("flatulence", {
      key, type: "flatulence", sensor: "flatulence", values, last, prev, derived: true,
      info: typeInfo("flatulence", key, meta), status: statusOf("flatulence", last[1]),
    });
  }
}
