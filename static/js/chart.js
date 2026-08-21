// Canvas line chart: thin 2px lines, hairline grid, threshold lines,
// ringed end markers, selective end labels and a crosshair tooltip.
import { CONFIG } from "./config.js";
import { cssVar, el, escapeHtml, fmtNum, fmtTime, remPx } from "./format.js";
import { thresholdsFor } from "./sensors.js";

const FONT = () => getComputedStyle(document.documentElement).getPropertyValue("--mono").trim();

function niceTicks(min, max, count) {
  const span = max - min || 1;
  const rough = span / Math.max(1, count);
  const pow = Math.pow(10, Math.floor(Math.log10(rough)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= rough) || pow * 10;
  const ticks = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) ticks.push(+v.toFixed(10));
  return ticks;
}

export class LineChart {
  constructor(type, index) {
    this.type = type;
    this.el = el("article", "chart");
    this.el.innerHTML = `<div class="head"><span class="num"></span><span class="title"></span><span class="unit"></span></div>
      <div class="plot"><canvas></canvas><div class="tip"></div></div>`;
    this.canvas = this.el.querySelector("canvas");
    this.tip = this.el.querySelector(".tip");
    this.numEl = this.el.querySelector(".num");
    this.titleEl = this.el.querySelector(".title");
    this.unitEl = this.el.querySelector(".unit");
    this.series = []; this.info = {}; this.fromMs = 0; this.toMs = 1; this.gapMs = 6 * 60_000;
    this.setIndex(index);
    const plot = this.el.querySelector(".plot");
    new ResizeObserver(() => this.draw()).observe(plot);
    plot.addEventListener("pointermove", (ev) => this.hover(ev));
    plot.addEventListener("pointerleave", () => { this.tip.style.display = "none"; this.draw(); });
  }

  setIndex(index) { this.numEl.textContent = String(index + 1).padStart(2, "0"); }

  /** series: [{name, color, values:[[t,v]...], active}] — active series are drawn last, with a dark casing */
  update({ info, series, fromMs, toMs, gapMs }) {
    this.info = info; this.series = series; this.fromMs = fromMs; this.toMs = toMs; this.gapMs = gapMs;
    if (this.titleEl.textContent !== info.label) this.titleEl.textContent = info.label;
    if (this.unitEl.textContent !== info.unit) this.unitEl.textContent = info.unit;
    this.draw();
  }

  draw() {
    const { canvas, series, info, fromMs, toMs } = this;
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.clientWidth, H = canvas.clientHeight;
    if (W < 10 || H < 10 || !series.length) return;
    if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const rem = remPx();
    const mono = FONT();
    const font = (px, weight = 400) => `${weight} ${px}px ${mono}`;
    const padR = 4.6 * rem, padT = 0.7 * rem, padB = 1.6 * rem;
    const ph = H - padT - padB;

    // y extent, pulling in the nearest threshold when it is within reach
    let lo = Infinity, hi = -Infinity;
    for (const s of series) for (const [, v] of s.values) { if (v < lo) lo = v; if (v > hi) hi = v; }
    if (!Number.isFinite(lo)) return;
    const thresholds = thresholdsFor(info);
    const lo0 = lo, hi0 = hi, span0 = hi0 - lo0 || Math.abs(hi0) * 0.1 || 1;
    for (const t of thresholds) {
      if (t.value > hi0 && t.value - hi0 < span0 * 1.5) hi = Math.max(hi, t.value);
      if (t.value < lo0 && lo0 - t.value < span0 * 1.5) lo = Math.min(lo, t.value);
    }
    const span = hi - lo || Math.abs(hi) * 0.1 || 1;
    lo -= span * 0.12; hi += span * 0.12;
    if (info.fair && info.fair[0] === 0 && lo < 0) lo = 0;
    // left padding follows the widest tick label so long values (1,007.5) never clip
    const yTicks = niceTicks(lo, hi, Math.max(2, Math.floor(ph / (2.6 * rem))));
    ctx.font = font(0.74 * rem);
    const tickW = Math.max(...yTicks.map((t) => ctx.measureText(fmtNum(t, Number.isInteger(t) ? 0 : 1)).width), 0);
    const padL = Math.max(2.4 * rem, tickW + 0.9 * rem);
    const pw = W - padL - padR;
    const X = (t) => padL + ((t - fromMs) / (toMs - fromMs)) * pw;
    const Y = (v) => padT + (1 - (v - lo) / (hi - lo)) * ph;
    this.geom = { X, Y, padL, padR, padT, ph, pw };

    // grid + y ticks
    ctx.lineWidth = 1; ctx.strokeStyle = cssVar("--hair-soft"); ctx.fillStyle = cssVar("--muted");
    ctx.font = font(0.74 * rem); ctx.textAlign = "right"; ctx.textBaseline = "middle";
    for (const t of yTicks) {
      const y = Math.round(Y(t)) + 0.5;
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
      ctx.fillText(fmtNum(t, Number.isInteger(t) ? 0 : 1), padL - 0.5 * rem, y);
    }
    // x ticks on whole hours
    const stepH = CONFIG.windowHours > 30 ? 12 : CONFIG.windowHours > 12 ? 6 : CONFIG.windowHours > 6 ? 3 : 1;
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    const first = new Date(fromMs); first.setMinutes(0, 0, 0);
    for (const d = new Date(first); d.getTime() <= toMs; d.setHours(d.getHours() + 1)) {
      if (d.getHours() % stepH || d.getTime() < fromMs) continue;
      const x = Math.round(X(d.getTime())) + 0.5;
      ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, padT + ph); ctx.stroke();
      ctx.fillText(fmtTime(d.getTime()), x, padT + ph + 0.4 * rem);
    }
    ctx.strokeStyle = cssVar("--hair"); ctx.beginPath(); ctx.moveTo(padL, padT + ph + 0.5); ctx.lineTo(W - padR, padT + ph + 0.5); ctx.stroke();

    // threshold lines (solid hairline in the status colour)
    for (const t of thresholds) {
      if (t.value < lo || t.value > hi) continue;
      const y = Math.round(Y(t.value)) + 0.5;
      ctx.globalAlpha = 0.6; ctx.strokeStyle = t.kind === "poor" ? cssVar("--critical") : cssVar("--warning");
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke(); ctx.globalAlpha = 1;
    }

    // series: everything at the normal 2 px; the active ones (cards showing) are drawn
    // last, on top, with a thin ground-coloured casing on each side — nothing else changes
    const ends = [];
    const ordered = [...series.filter((s) => !s.active), ...series.filter((s) => s.active)];
    ctx.lineJoin = "round"; ctx.lineCap = "round";
    for (const s of ordered) {
      ctx.beginPath();
      let prevT = null;
      for (const [t, v] of s.values) {
        const x = X(t), y = Y(v);
        if (prevT == null || t - prevT > this.gapMs) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        prevT = t;
      }
      if (s.active && CONFIG.highlightCasing > 0) {
        ctx.strokeStyle = cssVar("--ground"); ctx.lineWidth = CONFIG.highlightWidth + 2 * CONFIG.highlightCasing; ctx.stroke();
      }
      ctx.strokeStyle = s.color; ctx.lineWidth = s.active ? CONFIG.highlightWidth : 2;
      ctx.stroke();
      const [lt, lv] = s.values[s.values.length - 1];
      ends.push({ s, x: X(lt), y: Y(lv), v: lv });
    }
    // end marker (2px ring in the ground colour) + end label where it fits; active
    // series get a slightly larger marker and a bold label
    ends.sort((a, b) => a.y - b.y);
    let lastLabelY = -Infinity;
    for (const e of ends) {
      const r = e.s.active ? 5 : 4;
      ctx.beginPath(); ctx.arc(e.x, e.y, r + 1, 0, Math.PI * 2); ctx.fillStyle = cssVar("--ground"); ctx.fill();
      ctx.beginPath(); ctx.arc(e.x, e.y, r, 0, Math.PI * 2); ctx.fillStyle = e.s.color; ctx.fill();
      if (e.y - lastLabelY >= 1.15 * rem) {
        ctx.fillStyle = cssVar("--snow"); ctx.font = font(0.85 * rem, e.s.active ? 700 : 400); ctx.textAlign = "left"; ctx.textBaseline = "middle";
        ctx.fillText(fmtNum(e.v, info.decimals), e.x + 0.65 * rem, e.y);
        lastLabelY = e.y;
      }
    }
  }

  hover(ev) {
    if (!this.geom) return;
    const rect = this.canvas.getBoundingClientRect();
    const x = ev.clientX - rect.left;
    const t = this.fromMs + ((x - this.geom.padL) / this.geom.pw) * (this.toMs - this.fromMs);
    this.draw();
    const ctx = this.canvas.getContext("2d");
    const rows = [];
    for (const s of this.series) {
      let best = null, bd = Infinity;
      for (const p of s.values) { const d = Math.abs(p[0] - t); if (d < bd) { bd = d; best = p; } }
      if (best && bd < this.gapMs) rows.push({ s, p: best });
    }
    if (!rows.length) { this.tip.style.display = "none"; return; }
    const cx = Math.round(this.geom.X(rows[0].p[0])) + 0.5;
    ctx.strokeStyle = cssVar("--muted"); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(cx, this.geom.padT); ctx.lineTo(cx, this.geom.padT + this.geom.ph); ctx.stroke();
    for (const r of rows) {
      const px = this.geom.X(r.p[0]), py = this.geom.Y(r.p[1]);
      ctx.beginPath(); ctx.arc(px, py, 5, 0, Math.PI * 2); ctx.fillStyle = cssVar("--ground"); ctx.fill();
      ctx.beginPath(); ctx.arc(px, py, 4, 0, Math.PI * 2); ctx.fillStyle = r.s.color; ctx.fill();
    }
    this.tip.innerHTML = `<b>${fmtTime(rows[0].p[0])}</b>` + rows.map((r) =>
      `<div class="r"><i style="background:${r.s.color}"></i>${escapeHtml(r.s.name)} <b>${fmtNum(r.p[1], this.info.decimals)}</b> ${escapeHtml(this.info.unit)}</div>`).join("");
    this.tip.style.display = "block";
    const tw = this.tip.offsetWidth;
    const left = x + 14 + tw > rect.width ? x - tw - 14 : x + 14;
    this.tip.style.left = `${Math.max(0, left)}px`;
    this.tip.style.top = `${Math.max(0, ev.clientY - rect.top - 10)}px`;
  }
}
