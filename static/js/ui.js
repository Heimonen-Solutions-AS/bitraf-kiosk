// Header, alert banner, room cards, charts grid and footer.
// Everything updates in place (keyed elements) so refreshes never flash or reflow.
import { CONFIG } from "./config.js";
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
  if (node.lastSeen != null && nowMs - node.lastSeen > CONFIG.nodeQuietMin * 60_000) {
    tags.push({ text: `last seen ${fmtTime(node.lastSeen)} · ${fmtAgo(nowMs - node.lastSeen)}`, cls: "quiet" });
  }
  return tags;
}

function fmtAgo(ms) {
  const min = Math.round(ms / 60_000);
  if (min < 60) return `${min} min ago`;
  const h = Math.floor(min / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.floor(h / 24)} d ago`;
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
        if (s.status !== "poor" && s.status !== "fair") continue;
        if (STATUS_RANK[s.status] > STATUS_RANK[worst]) worst = s.status;
        const advice = s.status === "poor" && s.info.advice ? ` – ${s.info.advice}` : "";
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
  }

  /** Which node ids are visible right now: all of them, or a wrapping window of maxRoomCards. */
  _visibleIds(ids) {
    const max = CONFIG.maxRoomCards;
    if (ids.length <= max) return ids;
    this.offset %= ids.length;
    return Array.from({ length: max }, (_, i) => ids[(this.offset + i) % ids.length]);
  }

  _advance() {
    if (!this.last) return;
    const ids = [...this.last.nodes.keys()];
    if (ids.length <= CONFIG.maxRoomCards) return;
    this.host.classList.add("fading");
    setTimeout(() => {
      this.offset = (this.offset + CONFIG.maxRoomCards) % ids.length;
      this.update(this.last.nodes, this.last.meta, this.last.nowMs);
      this.host.classList.remove("fading");
    }, CONFIG.roomFadeMs);
  }

  update(nodes, meta, nowMs) {
    this.last = { nodes, meta, nowMs };
    const ids = [...nodes.keys()];
    const rotating = ids.length > CONFIG.maxRoomCards;
    if (rotating && !this.timer) this.timer = setInterval(() => this._advance(), CONFIG.roomRotateMs);
    if (!rotating && this.timer) { clearInterval(this.timer); this.timer = null; this.offset = 0; }
    const visible = this._visibleIds(ids);

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

      const sensors = [...node.sensors.values()].sort((a, b) => a.info.order - b.info.order);
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
      card.empty.hidden = !!(hero || rest.length);
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

export class Charts {
  constructor() { this.host = $("#charts"); this.charts = new Map(); }

  update(nodes, meta, fromMs, toMs, gapMs) {
    const names = displayNames(nodes, meta);
    const byType = new Map();
    for (const node of nodes.values()) for (const s of node.sensors.values()) {
      if (s.info.noChart) continue; // rating enums live on the cards, not in the grid
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
        series: entries.map(({ node, s }) => ({ name: names.get(node.id), color: node.color, values: s.values })),
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
    setText(this.range, `last ${CONFIG.windowHours} h · ${res} · newest sample ${latestMs ? fmtTime(latestMs) : "–"}`);
  }
  setSource(previewMs) {
    setHtml(this.source, (previewMs ? `<span class="tag">PREVIEW · ${escapeHtml(new Date(previewMs).toLocaleString(CONFIG.locale))}</span>` : "") + escapeHtml(CONFIG.infoLink));
  }
}
