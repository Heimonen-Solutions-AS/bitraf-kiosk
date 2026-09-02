// Known sensor types: label, unit, decimals and thresholds.
// good = [lo, hi] is "good", fair = [lo, hi] is "fair", outside fair is "poor".
// Thresholds follow Airthings' own colour bands for the View Plus.
export const SENSOR_TYPES = {
  temperature: { label: "Temperature", unit: "°C",    decimals: 1, good: [18, 25], fair: [15, 28],  hero: true, order: 0 },
  co2:         { label: "CO₂",         unit: "ppm",   decimals: 0, good: [0, 800], fair: [0, 1000], order: 1, advice: "ventilate" },
  humidity:    { label: "Humidity",    unit: "%",     decimals: 0, good: [30, 60], fair: [25, 70],  order: 2 },
  voc:         { label: "VOC",         unit: "ppb",   decimals: 0, good: [0, 250], fair: [0, 2000], order: 3, advice: "ventilate" },
  // Sensirion gas index (native on the newer sensors, derived from ppb for the
  // Airthings units, see bitraf/gasindex.py): 100 = this room's usual air, 500 = worst;
  // bands calibrated against ppb: Sensirion's index-to-ethanol curve puts Airthings' 250 ppb
  // "fair" boundary at index ~235 and the algorithm itself treats > 230 as a VOC event, so
  // good ends at 250; Atmotube/Blueair/AirGradient all call 350-400+ severe, so fair ends at 400
  vocindex:    { label: "VOC index",   unit: "",      decimals: 0, good: [0, 250], fair: [0, 400],  order: 3.5, advice: "ventilate", noChart: true },
  nox:         { label: "NOx index",   unit: "",      decimals: 0, good: [0, 20],  fair: [0, 100],  order: 4, advice: "ventilate" },
  pm25:        { label: "PM2.5",       unit: "µg/m³", decimals: 0, good: [0, 10],  fair: [0, 25],   order: 4, advice: "dust/smoke" },
  pm1:         { label: "PM1",         unit: "µg/m³", decimals: 0, good: [0, 10],  fair: [0, 25],   order: 5 },
  pm10:        { label: "PM10",        unit: "µg/m³", decimals: 0, good: [0, 20],  fair: [0, 50],   order: 6 },
  radon:       { label: "Radon",       unit: "Bq/m³", decimals: 0, good: [0, 100], fair: [0, 150],  order: 7, advice: "short-term avg" },
  pressure:    { label: "Pressure",    unit: "hPa",   decimals: 0, order: 8 },
  // derived (see derived.js): excess VOC × occupancy, in ppm; last so the chart
  // sits in the bottom right corner of the grid
  flatulence:  { label: "Flatulence",  unit: "ppm",   decimals: 2, good: [0, 0.05], fair: [0, 0.2], order: 12, advice: "open a window" },
  illuminance: { label: "Light",       unit: "lx",    decimals: 0, order: 11 },
  // device-reported overall rating (e.g. IKEA Alpstuga's AirQualityEnum,
  // 0=unknown 1=good … 6=extremely poor): shown as a word on the card, never charted
  airquality:  { label: "Air quality", unit: "", decimals: 0, order: 10, noChart: true,
                 enumWords: ["Unknown", "Good", "Fair", "Moderate", "Poor", "Very poor", "Extremely poor"] },
};

export const STATUS_WORD = { ok: "Good", fair: "Fair", poor: "Poor", none: "" };
export const STATUS_RANK = { none: 0, ok: 1, fair: 2, poor: 3 };

/** Sensor name from data.xml → key in SENSOR_TYPES (or the name itself).
 *  `unitsDisplay` (from the server metadata) settles a `voc` sensor that already reports an index. */
export function sensorType(sensorName, unitsDisplay = "") {
  const n = sensorName.toLowerCase();
  const compact = n.replace(/[-_ ]/g, "");
  if (compact === "vocindex" || (n === "voc" && /index/i.test(unitsDisplay || ""))) return "vocindex";
  if (compact === "vocest") return "voc"; // ppb estimated from the index, see bitraf/vocppb.py
  if (compact === "nox" || compact === "noxindex") return "nox";
  if (compact === "light" || compact === "illuminance" || compact === "lux" || compact === "ambientlight" || compact === "brightness") return "illuminance";
  if (n.startsWith("radon")) return "radon";
  if (n.startsWith("temp") || /^th\d*$/.test(n)) return "temperature";
  if (n === "pm25" || n === "pm2.5" || n === "pm2_5") return "pm25";
  if (n.startsWith("humid") || n === "rh") return "humidity";
  if (n.startsWith("press")) return "pressure";
  if (n.replace(/[-_ ]/g, "").endsWith("airquality")) return "airquality";
  return n;
}

/** Display text for a sensor value: the enum word for rating types, a number otherwise. */
export function valueWord(info, value) {
  if (!info.enumWords || value == null) return null;
  return info.enumWords[Math.round(value)] ?? String(value);
}

export function statusOf(type, value) {
  const t = SENSOR_TYPES[type];
  if (t && t.enumWords) {
    const v = value == null ? 0 : Math.round(value);
    if (v === 1) return "ok";
    if (v === 2 || v === 3) return "fair";
    return v >= 4 ? "poor" : "none";
  }
  if (!t || value == null || !t.fair) return "none";
  if (t.good && value >= t.good[0] && value <= t.good[1]) return "ok";
  if (value >= t.fair[0] && value <= t.fair[1]) return t.good ? "fair" : "ok";
  return "poor";
}

/** Display info for a type; unknown types get label/unit from server metadata. */
export function typeInfo(type, key, meta) {
  const known = SENSOR_TYPES[type];
  if (known) return known;
  const mm = (meta.metrics || {})[key] || {};
  const raw = (mm.sensor || type).replace(/[-_]/g, " ");
  return { label: raw.charAt(0).toUpperCase() + raw.slice(1), unit: mm.unitsDisplay || "", decimals: 1, order: 50 };
}

/** Threshold lines for a chart: [{value, kind}] where kind is "fair" or "poor". */
export function thresholdsFor(info) {
  if (!info.fair) return [];
  const out = [];
  if (info.good && info.good[0] > info.fair[0]) out.push({ value: info.good[0], kind: "fair" });
  if (info.good && Number.isFinite(info.good[1]) && info.good[1] < info.fair[1]) out.push({ value: info.good[1], kind: "fair" });
  if (Number.isFinite(info.fair[1]) && info.fair[1] > 0) out.push({ value: info.fair[1], kind: "poor" });
  if (info.fair[0] > 0) out.push({ value: info.fair[0], kind: "poor" });
  return out;
}
