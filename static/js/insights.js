// Noteworthy facts per device for the stats panel. Every candidate gets a score;
// all above MIN_SCORE are returned, best first. A device with nothing noteworthy
// gets an empty list and the panel shows nothing for it.
import { CONFIG } from "./config.js";
import { fmtNum } from "./format.js";
import { SENSOR_TYPES } from "./sensors.js";

const H = 3600_000;
const MIN_SCORE = 1;
const pc = (f) => `${Math.round(f * 100)} %`;

function when(ms) {
  return new Date(ms).toLocaleString(CONFIG.locale, { weekday: "short", hour: "2-digit", minute: "2-digit" });
}

function dur(ms) {
  const min = Math.round(ms / 60_000);
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  if (h < 48) return min % 60 ? `${h} h ${min % 60} min` : `${h} h`;
  const d = Math.floor(h / 24);
  return h % 24 ? `${d} d ${h % 24} h` : `${d} d`;
}

// Cross-room bragging rights: [type, comparator, wording].
const SUPERLATIVES = [
  ["temperature", (a, b) => a.avg > b.avg, (t) => `hottest room of the week, avg <b>${fmtNum(t.avg, 1)} °C</b>, peak ${fmtNum(t.max, 1)} °C ${when(t.maxAt)}`],
  ["temperature", (a, b) => a.avg < b.avg, (t) => `coldest room of the week, avg <b>${fmtNum(t.avg, 1)} °C</b>, low ${fmtNum(t.min, 1)} °C ${when(t.minAt)}`],
  ["humidity",    (a, b) => a.avg > b.avg, (t) => `most humid room of the week, avg <b>${fmtNum(t.avg, 0)} %</b> RH`],
  ["humidity",    (a, b) => a.avg < b.avg, (t) => `driest room of the week, avg <b>${fmtNum(t.avg, 0)} %</b> RH`],
  ["co2",         (a, b) => a.avg > b.avg, (t) => `stuffiest room of the week, CO₂ avg <b>${fmtNum(t.avg, 0)} ppm</b>`],
  ["co2",         (a, b) => a.avg < b.avg, (t) => `freshest air of the week, CO₂ avg <b>${fmtNum(t.avg, 0)} ppm</b>`],
];

/** Map(nodeId → [{score, html, critical}...]), best first, only facts above MIN_SCORE. */
export function buildInsights(stats) {
  const out = new Map();
  if (!stats || !stats.nodes) return out;
  const cands = new Map();
  const add = (id, score, html, critical = false) => {
    if (!cands.has(id)) cands.set(id, []);
    cands.get(id).push({ score, html, critical });
  };

  for (const [id, st] of Object.entries(stats.nodes)) {
    // availability: repeated dropouts are the whole point, they must be seen
    if (st.gapCount >= 2) add(id, 1 + st.gapCount / 2, `unplugged or unreachable <b>${st.gapCount}×</b> this week, ${dur(st.downtimeMs)} down`, true);
    else if (st.downtimeMs > 6 * H) add(id, 1.3, `offline <b>${dur(st.downtimeMs)}</b> in total this week`, true);
    if (st.silentNow) add(id, 2 + (stats.toMs - st.lastMs) / (24 * H), `silent since ${when(st.lastMs)}`, true);
    if (st.firstMs - stats.fromMs > 6 * H) add(id, 1.5, `new here, first seen ${when(st.firstMs)}`);

    for (const [type, t] of Object.entries(st.types || {})) {
      const info = SENSOR_TYPES[type];
      if (!t.pct || !info) continue;
      const poor = t.pct.poor || 0, fair = t.pct.fair || 0;
      if (type === "airquality") {
        if (fair + poor > 0.25) add(id, 1 + poor * 6 + fair * 2, `air rated below "Good" <b>${pc(fair + poor)}</b> of the week`);
        continue;
      }
      if (poor > 0.05) add(id, 1 + poor * 8, `${info.label} in the red <b>${pc(poor)}</b> of the week, avg ${fmtNum(t.avg, info.decimals)} ${info.unit}`);
      else if (fair + poor > 0.2 && info.good) add(id, 1 + (fair + poor) * 2, `${info.label} outside the good range <b>${pc(fair + poor)}</b> of the week`);
    }
    // last 24 h against the days before
    for (const type of ["temperature", "humidity", "co2"]) {
      const t = (st.types || {})[type], info = SENSOR_TYPES[type];
      if (!t || t.avg24h == null || t.avgPrior == null || !info.good) continue;
      const band = info.good[1] - info.good[0];
      const d = t.avg24h - t.avgPrior;
      if (Math.abs(d) >= band * 0.18) add(id, 1 + Math.abs(d) / band, `${info.label.toLowerCase()} <b>${fmtNum(Math.abs(d), info.decimals)} ${info.unit} ${d > 0 ? "above" : "below"}</b> its weekly normal today`);
    }
  }

  // superlatives need an actual contest
  const entries = Object.entries(stats.nodes);
  for (const [type, beats, word] of SUPERLATIVES) {
    let winner = null;
    let contenders = 0;
    for (const [id, st] of entries) {
      const t = (st.types || {})[type];
      if (!t || t.n < 10) continue;
      contenders += 1;
      if (!winner || beats(t, winner[1])) winner = [id, t];
    }
    if (winner && contenders >= 3) add(winner[0], 1.2, word(winner[1]));
  }

  for (const [id, list] of cands) {
    const keep = list.filter((c) => c.score >= MIN_SCORE).sort((a, b) => b.score - a.score);
    if (keep.length) out.set(id, keep);
  }
  return out;
}

/** One fact per stats round: random, weighted by score² so the worst news dominates. */
export function pickFact(facts) {
  if (!facts || !facts.length) return null;
  const weights = facts.map((f) => f.score * f.score);
  let r = Math.random() * weights.reduce((a, b) => a + b, 0);
  for (let i = 0; i < facts.length; i++) { r -= weights[i]; if (r <= 0) return facts[i]; }
  return facts[0];
}
