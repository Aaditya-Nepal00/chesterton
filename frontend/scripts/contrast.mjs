// WCAG contrast of the main token pairs, parsed from src/index.css (light :root and the dark block).
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../src/index.css", import.meta.url), "utf8");
const block = (start) => {
  const i = css.indexOf(start);
  return css.slice(i, css.indexOf("}", css.indexOf("{", i)));
};
const vars = (text) => Object.fromEntries([...text.matchAll(/--([\w-]+):\s*(#[0-9a-f]{6})/gi)].map((m) => [m[1], m[2]]));
const themes = { light: vars(block(":root {")), dark: vars(block(':root[data-theme="dark"]')) };

const lum = (hex) => {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const l = c.map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * l[0] + 0.7152 * l[1] + 0.0722 * l[2];
};
const ratio = (a, b) => {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
};
const pairs = [
  ["ink", "paper"], ["ink-2", "surface"], ["ink-3", "paper"], ["ink-3", "surface"], ["ink-3", "surface-2"],
  ["accent", "surface"], ["accent", "paper"], ["accent", "accent-soft"], ["accent-ink", "accent"],
  ["ok", "ok-soft"], ["missing", "missing-soft"], ["partial", "partial-soft"], ["slate", "slate-soft"],
  ["muted", "muted-soft"], ["fail", "fail-soft"], ["guard", "guard-soft"],
];
let worst = Infinity;
for (const [name, t] of Object.entries(themes)) {
  const out = pairs.map(([fg, bg]) => {
    const r = ratio(t[fg], t[bg]);
    worst = Math.min(worst, r);
    return `${fg}/${bg} ${r.toFixed(2)}${r < 4.5 ? " (<4.5!)" : ""}`;
  });
  console.log(`${name}: ${out.join(" · ")}`);
}
console.log(`lowest ratio: ${worst.toFixed(2)} (AA body text needs 4.5)`);
