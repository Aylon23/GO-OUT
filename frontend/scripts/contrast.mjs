// WCAG AA check for text tokens on bg/surface, both themes. Run: npm run contrast
import { readFileSync } from "node:fs";
const css = readFileSync(new URL("../src/styles/tokens.css", import.meta.url), "utf8");
const block = (re) => Object.fromEntries([...css.match(re)[1].matchAll(/--([\w-]+):\s*oklch\(([^)]+)\)/g)].map((m) => [m[1], m[2]]));
const light = block(/:root \{([\s\S]*?)\n\}/);
const dark = block(/:root\[data-theme="dark"\] \{([\s\S]*?)\n\}/);
function oklchToLum(s) {
  const [L, C, H] = s.split(/[\s/]+/).map(Number);
  const h = (H * Math.PI) / 180, a = C * Math.cos(h), b = C * Math.sin(h);
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b, m_ = L - 0.1055613458 * a - 0.0638541728 * b, s_ = L - 0.0894841775 * a - 1.291485548 * b;
  const l = l_ ** 3, m = m_ ** 3, s2 = s_ ** 3;
  const rgb = [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s2, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s2, -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s2];
  const [r, g, bl] = rgb.map((c) => Math.min(1, Math.max(0, c)));
  return 0.2126 * r + 0.7152 * g + 0.0722 * bl; // linear already
}
const ratio = (a, b) => { const x = oklchToLum(a), y = oklchToLum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
let fail = 0;
for (const [name, t] of [["light", light], ["dark", dark]]) {
  for (const fg of ["text", "muted", "faint", "gain", "loss", "accent"]) for (const bg of ["bg", "surface", "sunken"]) {
    const r = ratio(t[fg], t[bg]); const ok = r >= 4.5; if (!ok) fail++;
    console.log(`${name.padEnd(5)} ${fg.padEnd(7)} on ${bg.padEnd(7)} ${r.toFixed(2)} ${ok ? "AA" : "FAIL"}`);
  }
  const r = ratio(t["accent-text"], t["accent"]); if (r < 4.5) fail++;
  console.log(`${name.padEnd(5)} accent-text on accent ${r.toFixed(2)} ${r >= 4.5 ? "AA" : "FAIL"}`);
}
process.exit(fail ? 1 : 0);
