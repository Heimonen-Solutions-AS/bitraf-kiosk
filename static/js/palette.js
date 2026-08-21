// Series colours past the fixed --s1..--sN palette in kiosk.css: generated in OKLCH
// so the kiosk never wraps back to a colour already in use, however many devices
// report. Hue steps by the golden angle (no two nearby indices share a hue), the
// lightness alternates between three levels so hue neighbours still differ.
const GOLDEN_ANGLE = 137.50776405;
const LIGHTNESS = [0.74, 0.62, 0.86];
const CHROMA = 0.16;

function oklabToLinear(L, a, b) {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
}

const toSrgb = (c) => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055);
const hex2 = (v) => Math.round(Math.min(1, Math.max(0, v)) * 255).toString(16).padStart(2, "0");

/** OKLCH → #rrggbb, lowering chroma until the colour is inside the sRGB gamut. */
export function oklchToHex(L, C, hueDeg) {
  const h = (hueDeg * Math.PI) / 180;
  for (let c = C; c >= 0; c -= 0.01) {
    const lin = oklabToLinear(L, c * Math.cos(h), c * Math.sin(h));
    if (lin.every((v) => v >= -0.001 && v <= 1.001)) return `#${lin.map((v) => hex2(toSrgb(v))).join("")}`;
  }
  return "#808080";
}

/** Colour for series slot `index` (0-based) beyond the fixed palette. */
export function generatedColor(index) {
  const hue = (index * GOLDEN_ANGLE) % 360;
  return oklchToHex(LIGHTNESS[index % LIGHTNESS.length], CHROMA, hue);
}
