import { CONFIG } from "./config.js";

export const $ = (sel, root = document) => root.querySelector(sel);
export const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
export const remPx = () => parseFloat(getComputedStyle(document.documentElement).fontSize);

export function fmtNum(v, decimals = 0) {
  if (v == null || Number.isNaN(v)) return "·";
  return v.toLocaleString(CONFIG.locale, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}
export const fmtTime = (ms) => new Date(ms).toLocaleTimeString(CONFIG.locale, { hour: "2-digit", minute: "2-digit" });
export const fmtDate = (ms) => new Date(ms).toLocaleDateString(CONFIG.locale, { weekday: "long", day: "numeric", month: "long" });

export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

/** Create an element with optional class and text. */
export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

/** Only touch the DOM when something actually changed (no needless reflow). */
export function setText(node, text) {
  if (node.textContent !== text) { node.textContent = text; return true; }
  return false;
}
export function setClass(node, className) {
  if (node.className !== className) node.className = className;
}
export function setHtml(node, html) {
  if (node.innerHTML !== html) node.innerHTML = html;
}
