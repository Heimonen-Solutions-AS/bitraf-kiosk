// Header, alert banner, room cards, charts grid and footer.
// Everything updates in place (keyed elements) so refreshes never flash or reflow.
import { CONFIG } from "./config.js";
import { buildInsights, pickFact } from "./insights.js";
import { LineChart } from "./chart.js";
import { $, el, escapeHtml, fmtDate, fmtNum, fmtTime, setClass, setHtml, setText } from "./format.js";
import { STATUS_RANK, STATUS_WORD, valueWord } from "./sensors.js";

export function roomName(nodeId, meta) {
  const m = (meta.nodes || {})[nodeId] || {};
  return CONFIG.rooms[nodeId] || m.location || m.description || nodeId;
}

/**
 * The ONE identifier for a node, used identically on the card title, chart legends and
 * alerts: the room name; when two nodes share a room, ` · manufacturer` is appended
 * (short, e.g. "First floor: 217 · Airthings" vs "First floor: 217 · Raspberry Pi").
 */
export function displayNames(nodes, meta) {
  const names = new Map();
  const count = new Map();
  for (const node of nodes.values()) { const n = roomName(node.id, meta); count.set(n, (count.get(n) || 0) + 1); }
  for (const node of nodes.values()) {
    const n = roomName(node.id, meta);
    if (count.get(n) > 1) {
      const m = (meta.nodes || {})[node.id] || {};
      names.set(node.id, `${n} · ${m.manufacturer || m.model || node.id}`);
    } else names.set(node.id, n);
  }
  return names;
}

/** Split "room · suffix" for the card title: room big, suffix small and muted. */
function splitName(label) {
  const i = label.indexOf(" · ");
  return i < 0 ? [label, ""] : [label.slice(0, i), label.slice(i + 3)];
}

/** Minutes of silence before a node counts as quiet: per device type, else the default. */
export function quietMinFor(m) {
  const device = `${m.manufacturer || ""} ${m.model || ""}`.toLowerCase();
  for (const [needle, min] of Object.entries(CONFIG.nodeQuietMinByDevice || {})) {
    if (needle && device.includes(needle.toLowerCase())) return min;
  }
  return CONFIG.nodeQuietMin;
}

function deviceTags(node, meta, nowMs) {
  const nodeId = node.id;
  const m = (meta.nodes || {})[nodeId] || {};
  const name = roomName(nodeId, meta);
  // At most two info pills: device (manufacturer + model) and node-id; the description
  // stands in for the device when the node has no ietf-hardware info. Warnings follow.
  const device = [m.manufacturer, m.model].filter(Boolean).join(" ") || m.description || "";
  const tags = [];
  if (device && device !== name) tags.push({ text: device });
  tags.push({ text: nodeId });
  if (!m.location && !CONFIG.rooms[nodeId]) tags.push({ text: "location not set", cls: "warn" });
  if (node.lastSeen != null && nowMs - node.lastSeen > quietMinFor(m) * 60_000) {
    tags.push({ text: `last seen ${fmtTime(node.lastSeen)} · ${fmtAgo(nowMs - node.lastSeen)}`, cls: "quiet" });
  }
  return tags;
}

/** "45 min ago", "1 h 30 min ago", "2 d 3 h ago" — never rounded down to a bare hour or day. */
export function fmtAgo(ms) {
  const min = Math.round(ms / 60_000);
  if (min < 60) return `${min} min ago`;
  const h = Math.floor(min / 60), m = min % 60;
  if (h < 48) return m ? `${h} h ${m} min ago` : `${h} h ago`;
  const d = Math.floor(h / 24), hh = h % 24;
  return hh ? `${d} d ${hh} h ago` : `${d} d ago`;
}

function trendArrow(s) {
  if (!s.prev) return "";
  const d = s.last[1] - s.prev[1];
  const eps = Math.max(0.5 * Math.pow(10, -(s.info.decimals || 0)), Math.abs(s.prev[1]) * 0.01);
  return d > eps ? "↗" : d < -eps ? "↘" : "→";
}

/** Set a value node's text and flash it briefly if it changed. */
function setValue(node, text) {
  if (setText(node, text)) {
    node.classList.add("fresh");
    clearTimeout(node._t);
    node._t = setTimeout(() => node.classList.remove("fresh"), 1500);
  }
}

export class Header {
  constructor() {
    setText($("#eyebrow"), CONFIG.eyebrow);
    setText($("#headline"), CONFIG.headline);
    this.tick(); setInterval(() => this.tick(), 1000);
  }
  tick() {
    const now = Date.now();
    setText($("#clock"), fmtTime(now));
    setText($("#date"), fmtDate(now));
  }
}

export class Banner {
  constructor() { this.el = $("#banner"); this.lead = $("#bannerLead"); this.items = $("#bannerItems"); }

  update(nodes, meta, latestMs, nowMs) {
    const names = displayNames(nodes, meta);
    const items = [];
    let worst = "ok";
    for (const node of nodes.values()) {
      for (const s of node.sensors.values()) {
        if (s.info.hidden || (s.status !== "poor" && s.status !== "fair")) continue;
        if (STATUS_RANK[s.status] > STATUS_RANK[worst]) worst = s.status;
        const advice = s.status === "poor" && s.info.advice ? ` · ${s.info.advice}` : "";
        const value = valueWord(s.info, s.last[1]) ?? `${fmtNum(s.last[1], s.info.decimals)} ${s.info.unit}`;
        items.push({ rank: STATUS_RANK[s.status], html:
          `<span class="item ${s.status}"><b>${escapeHtml(names.get(node.id))}</b> ${escapeHtml(s.info.label)} ${escapeHtml(value)}${escapeHtml(advice)}</span>` });
      }
      if (!node.sensors.size) items.push({ rank: 2, html: `<span class="item stale"><b>${escapeHtml(roomName(node.id, meta))}</b> no data</span>` });
    }
    items.sort((a, b) => b.rank - a.rank);
    const html = items.slice(0, 5).map((i) => i.html).join("") + (items.length > 5 ? `<span class="item">+${items.length - 5} more</span>` : "");
    const staleMin = latestMs ? (nowMs - latestMs) / 60_000 : Infinity;
    let cls, lead;
    if (staleMin > CONFIG.staleAfterMin) { cls = "stale"; lead = latestMs ? `No new samples since ${fmtTime(latestMs)}` : "No samples in the database"; }
    else if (worst === "poor") { cls = "poor"; lead = "Poor air quality"; }
    else if (worst === "fair") { cls = "fair"; lead = "Fair air quality"; }
    else { cls = "ok"; lead = "Good air quality in all rooms"; }
    setClass(this.el, `banner ${cls}`);
    setText(this.lead, lead);
    setHtml(this.items, html);
  }

  error(message) { setClass(this.el, "banner error"); setText(this.lead, message); setHtml(this.items, ""); }
}

export class Rooms {
  constructor() {
    this.host = $("#rooms"); this.cards = new Map();
    this.offset = 0; this.timer = null; this.last = null;
    this.onWindow = null; // callback(visibleIds) — the legend and charts highlight these
    // stats round: first thing on page load, then one slot after every full card pass
    this.altEl = null;          // the stats panel's element (set from main.js)
    this.canStats = null;       // () => panel has stats to show
    this.onStatsRound = null;   // called when a new stats round begins (re-rolls facts)
    this.statsRound = false; this.boot = true; this.pagesShown = 1; this.idleTimer = null;
  }

  /** Which node ids are visible right now: all of them, or a wrapping window of maxRoomCards. */
  _visibleIds(ids) {
    const max = CONFIG.maxRoomCards;
    if (ids.length <= max) return ids;
    this.offset %= ids.length;
    return Array.from({ length: max }, (_, i) => ids[(this.offset + i) % ids.length]);
  }

  _statsReady() { return CONFIG.statsRound && this.altEl && this.canStats && this.canStats(); }

  _beginStatsRound() {
    this.statsRound = true;
    if (this.onStatsRound) this.onStatsRound();
  }

  _fade(on) {
    this.host.classList.toggle("fading", on);
    if (this.altEl) this.altEl.classList.toggle("fading", on);
  }

  _rerender() { this.update(this.last.nodes, this.last.meta, this.last.nowMs); }

  _advance() {
    if (!this.last) return;
    const ids = [...this.last.nodes.keys()];
    if (ids.length <= CONFIG.maxRoomCards && !this.statsRound) return;
    const pages = Math.max(1, Math.ceil(ids.length / CONFIG.maxRoomCards));
    this._fade(true);
    setTimeout(() => {
      if (this.statsRound) {
        this.statsRound = false;
        // the boot round precedes page 0; later rounds continue where the cards left off
        if (!this.boot) this.offset = (this.offset + CONFIG.maxRoomCards) % ids.length;
        this.boot = false; this.pagesShown = 1;
      } else if (this.pagesShown >= pages && this._statsReady()) { this._beginStatsRound(); }
      else { this.boot = false; this.offset = (this.offset + CONFIG.maxRoomCards) % ids.length; this.pagesShown += 1; }
      this._rerender();
      this._fade(false);
    }, CONFIG.roomFadeMs);
  }

  /** Boards with no rotation still get a stats round now and then. */
  _idleStatsRound() {
    if (this.statsRound || !this.last || !this._statsReady()) return;
    this._fade(true);
    setTimeout(() => { this._beginStatsRound(); this._rerender(); this._fade(false); }, CONFIG.roomFadeMs);
    setTimeout(() => {
      this._fade(true);
      setTimeout(() => { this.statsRound = false; this.boot = false; this._rerender(); this._fade(false); }, CONFIG.roomFadeMs);
    }, CONFIG.roomRotateMs);
  }

  update(nodes, meta, nowMs) {
    this.last = { nodes, meta, nowMs };
    const ids = [...nodes.keys()];
    const rotating = ids.length > CONFIG.maxRoomCards;
    if (rotating && !this.timer) this.timer = setInterval(() => this._advance(), CONFIG.roomRotateMs);
    if (!rotating && this.timer) { clearInterval(this.timer); this.timer = null; this.offset = 0; }
    if (CONFIG.statsRound && !rotating && ids.length && !this.idleTimer) {
      this.idleTimer = setInterval(() => this._idleStatsRound(), CONFIG.statsIdleMin * 60_000);
    }
    if ((rotating || !ids.length) && this.idleTimer) { clearInterval(this.idleTimer); this.idleTimer = null; }
    // page load opens on the stats panel as soon as the weekly stats have arrived
    if (this.boot && !this.statsRound && this.pagesShown === 1 && this._statsReady()) {
      this._beginStatsRound();
      if (!rotating) setTimeout(() => { if (this.boot) { this.boot = false; this.statsRound = false; this._rerender(); } }, CONFIG.roomRotateMs);
    }
    this.host.hidden = this.statsRound;
    if (this.altEl) this.altEl.hidden = !this.statsRound;
    if (this.statsRound) { if (this.onWindow) this.onWindow([]); return; }  // cards stay built underneath
    const visible = this._visibleIds(ids);
    if (this.onWindow) this.onWindow(visible);

    const names = displayNames(nodes, meta);
    for (const [id, card] of this.cards) if (!nodes.has(id)) { card.el.remove(); this.cards.delete(id); }
    for (const id of visible) {
      const node = nodes.get(id);
      let card = this.cards.get(node.id);
      if (!card) { card = this._create(node); this.cards.set(node.id, card); }
      this.host.appendChild(card.el); // keeps DOM order = window order
      card.el.style.setProperty("--series", node.color);
      const [room, suffix] = splitName(names.get(node.id));
      setHtml(card.name, `${escapeHtml(room)}${suffix ? `<small> · ${escapeHtml(suffix)}</small>` : ""}`);
      setHtml(card.tags, deviceTags(node, meta, nowMs).map((t) => `<span class="tag ${t.cls || ""}">${escapeHtml(t.text)}</span>`).join(""));

      const sensors = [...node.sensors.values()].filter((s) => !s.info.hidden).sort((a, b) => a.info.order - b.info.order);
      const hero = sensors.find((s) => s.info.hero);
      const rest = sensors.filter((s) => s !== hero);
      // hero
      card.hero.hidden = !hero;
      if (hero) {
        setValue(card.heroV, fmtNum(hero.last[1], hero.info.decimals));
        setText(card.heroU, hero.info.unit);
        setClass(card.heroS, `st ${hero.status}`);
        setText(card.heroS, `${STATUS_WORD[hero.status]} ${trendArrow(hero)}`.trim());
      }
      // stats (keyed by type)
      const seen = new Set();
      for (const s of rest) {
        seen.add(s.type);
        let row = card.stats.get(s.type);
        if (!row) {
          const root = el("div", "stat");
          root.innerHTML = `<span class="l"></span><span class="v"></span><span class="st"></span>`;
          row = { el: root, l: root.children[0], v: root.children[1], st: root.children[2] };
          card.stats.set(s.type, row);
        }
        card.statsEl.appendChild(row.el);
        setText(row.l, s.info.label);
        const value = fmtNum(s.last[1], s.info.decimals);
        if (row.v.firstChild?.nodeValue !== value) { row.v.innerHTML = `${escapeHtml(value)}<small>${escapeHtml(s.info.unit)}</small>`; setValue(row.v, row.v.textContent); }
        setClass(row.st, `st ${s.status}`);
        setText(row.st, `${STATUS_WORD[s.status]} ${trendArrow(s)}`.trim());
      }
      for (const [type, row] of card.stats) if (!seen.has(type)) { row.el.remove(); card.stats.delete(type); }
      card.empty.hidden = !!(hero || rest.length) || node.lastSeen != null;
    }
    for (const [id, card] of this.cards) if (!visible.includes(id) && card.el.parentNode) card.el.remove();
  }

  _create(node) {
    const root = el("article", "room");
    root.innerHTML = `<div class="name"></div><div class="tags"></div>
      <div class="hero"><span class="v"></span><span class="u"></span><span class="st"></span></div>
      <div class="stats"></div><div class="empty" hidden>No samples in the last ${CONFIG.windowHours} h</div>`;
    return {
      el: root, name: root.querySelector(".name"), tags: root.querySelector(".tags"),
      hero: root.querySelector(".hero"), heroV: root.querySelector(".hero .v"), heroU: root.querySelector(".hero .u"),
      heroS: root.querySelector(".hero .st"), statsEl: root.querySelector(".stats"), stats: new Map(),
      empty: root.querySelector(".empty"),
    };
  }
}

/**
 * One legend for all charts: a stationary chip per device, in the order the cards
 * rotate through. Only which chips are lit changes: lit = its card is showing now.
 */
export class Legend {
  constructor() { this.host = $("#legend"); this.chips = new Map(); this.active = []; }

  setActive(ids) {
    this.active = ids;
    for (const [id, chip] of this.chips) this._mark(id, chip);
  }

  _mark(id, chip) {
    setClass(chip.el, `chip ${this.active.includes(id) ? "lit" : "dim"}`);
  }

  update(nodes, meta) {
    const names = displayNames(nodes, meta);
    for (const [id, chip] of this.chips) if (!nodes.has(id)) { chip.el.remove(); this.chips.delete(id); }
    for (const node of nodes.values()) {
      let chip = this.chips.get(node.id);
      if (!chip) {
        const root = el("span", "chip");
        root.innerHTML = `<i></i><span class="n"></span>`;
        chip = { el: root, name: root.querySelector(".n") };
        this.chips.set(node.id, chip);
      }
      this.host.appendChild(chip.el); // DOM order = node order = rotation order, never reordered
      chip.el.style.setProperty("--c", node.color);
      setText(chip.name, names.get(node.id));
      this._mark(node.id, chip);
    }
  }
}

/** Stats panel: one compact block per device, name + averages line + one fact per round. */
export class StatsBoard {
  constructor() {
    this.host = $("#stats");
    this.host.innerHTML = `<div class="st-head"><span class="st-title">Stats</span><span class="st-sub"></span></div><div class="st-rows"></div>`;
    this.sub = this.host.querySelector(".st-sub");
    this.rowsEl = this.host.querySelector(".st-rows");
    this.rows = new Map(); this.stats = null; this.ready = false;
    this.facts = new Map(); this.picked = new Map();
  }

  setStats(stats) {
    this.stats = stats;
    this.ready = !!(stats && stats.nodes && Object.keys(stats.nodes).length);
    this.facts = buildInsights(stats);
    for (const id of this.picked.keys()) if (!this.facts.has(id)) this.picked.delete(id);
  }

  /** A new stats round: re-roll which fact each device tells this time and render it
   *  right away, while the panel is still hidden, so the content is final before it shows. */
  newRound() {
    this.picked = new Map();
    for (const [id, facts] of this.facts) this.picked.set(id, pickFact(facts));
    if (this.lastNodes) this._render(this.lastNodes, this.lastMeta);
  }

  update(nodes, meta) {
    this.lastNodes = nodes; this.lastMeta = meta;
    if (!this.ready || !nodes.size) return;
    // never change the content while someone is looking at it: refresh only while hidden
    if (!this.host.hidden) return;
    this._render(nodes, meta);
  }

  _render(nodes, meta) {
    const stats = this.stats;
    if (!this.ready || !nodes.size) return;
    setText(this.sub, `last ${stats.days} days · updated ${fmtTime(stats.generatedAt)}`);
    const names = displayNames(nodes, meta);
    for (const [id, row] of this.rows) if (!nodes.has(id)) { row.el.remove(); this.rows.delete(id); }
    for (const node of nodes.values()) {
      let row = this.rows.get(node.id);
      if (!row) {
        const root = el("div", "st-row");
        root.innerHTML = `<div class="st-name"><i></i><span class="n"></span></div><div class="st-avgs"></div><div class="st-fact"></div>`;
        row = { el: root, name: root.querySelector(".n"), avgs: root.querySelector(".st-avgs"), fact: root.querySelector(".st-fact") };
        this.rows.set(node.id, row);
      }
      this.rowsEl.appendChild(row.el); // node order, same as the legend and the card rotation
      row.el.style.setProperty("--c", node.color);
      setText(row.name, names.get(node.id));
      const st = stats.nodes[node.id];
      setHtml(row.avgs, st ? statAvgLine(st) : "no data this week");
      const fact = this.picked.get(node.id) || pickFact(this.facts.get(node.id));
      if (fact && !this.picked.has(node.id)) this.picked.set(node.id, fact);
      row.fact.hidden = !fact;
      if (fact) { setHtml(row.fact, fact.html); setClass(row.fact, `st-fact${fact.critical ? " crit" : ""}`); }
    }
  }
}

function statAvgLine(st) {
  const t = st.types || {};
  const parts = [];
  if (t.temperature) parts.push(`avg <b>${fmtNum(t.temperature.avg, 1)}</b> °C`);
  if (t.humidity) parts.push(`<b>${fmtNum(t.humidity.avg, 0)}</b> % RH`);
  if (t.co2) parts.push(`CO₂ <b>${fmtNum(t.co2.avg, 0)}</b> ppm`);
  if (t.radon) parts.push(`radon <b>${fmtNum(t.radon.avg, 0)}</b> Bq/m³`);
  return parts.slice(0, 4).join(" · ") || "·";
}

export class Charts {
  constructor() { this.host = $("#charts"); this.charts = new Map(); this.active = null; }

  /** ids whose cards are showing; null = highlight everything. */
  setActive(ids) {
    this.active = ids;
    this.last && this.update(...this.last);
  }

  update(nodes, meta, fromMs, toMs, gapMs) {
    this.last = [nodes, meta, fromMs, toMs, gapMs];
    const names = displayNames(nodes, meta);
    const lit = this.active ? new Set(this.active) : null;
    const byType = new Map();
    for (const node of nodes.values()) for (const s of node.sensors.values()) {
      if (s.info.noChart || s.info.hidden) continue; // rating enums live on the cards, not in the grid
      if (!byType.has(s.type)) byType.set(s.type, []);
      byType.get(s.type).push({ node, s });
    }
    const types = [...byType.keys()].sort((a, b) =>
      (byType.get(a)[0].s.info.order ?? 50) - (byType.get(b)[0].s.info.order ?? 50) || a.localeCompare(b));
    for (const [type, chart] of this.charts) if (!byType.has(type)) { chart.el.remove(); this.charts.delete(type); }
    types.forEach((type, index) => {
      const entries = byType.get(type);
      let chart = this.charts.get(type);
      if (!chart) { chart = new LineChart(type, index); this.charts.set(type, chart); }
      chart.setIndex(index);
      this.host.appendChild(chart.el);
      chart.update({
        info: entries[0].s.info, fromMs, toMs, gapMs,
        series: entries.map(({ node, s }) => ({ name: names.get(node.id), color: node.color, values: s.values,
                                                active: !lit || lit.has(node.id) })),
      });
    });
    const cols = getComputedStyle(this.host).gridTemplateColumns.split(" ").length || 2;
    this.host.style.gridTemplateRows = `repeat(${Math.max(1, Math.ceil(types.length / cols))}, minmax(0, 1fr))`;
  }

  redraw() { for (const c of this.charts.values()) c.draw(); }
}

export class Footer {
  constructor() { this.live = $("#liveText"); this.range = $("#rangeText"); this.source = $("#sourceText"); }
  setLive(mode, detail) {
    setClass(this.live, `live ${mode === "off" ? "off" : ""}`.trim());
    setText(this.live, detail);
  }
  setRange(store, latestMs) {
    const res = store.aggregated ? `avg per ${Math.round(store.bucketMs / 60_000)} min` : "per minute";
    setText(this.range, `last ${CONFIG.windowHours} h · ${res} · newest sample ${latestMs ? fmtTime(latestMs) : "·"}`);
  }
  setSource(previewMs) {
    setHtml(this.source, (previewMs ? `<span class="tag">PREVIEW · ${escapeHtml(new Date(previewMs).toLocaleString(CONFIG.locale))}</span>` : "") + escapeHtml(CONFIG.infoLink));
  }
}
