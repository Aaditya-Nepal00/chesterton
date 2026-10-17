// Screenshots of the dashboard at desktop and phone widths, light and dark.
//   node scripts/screenshots.mjs [baseUrl]   (default http://localhost:4173)
// Output: <repo>/.cache/screenshots/ (gitignored). Also reports horizontal overflow
// and console errors per page.
import { mkdir } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const base = process.argv[2] ?? "http://localhost:4173";
const out = resolve(dirname(fileURLToPath(import.meta.url)), "../../.cache/screenshots");
const RUN = process.env.TRACE_RUN ?? "20261017T111217Z-85e08f"; // analyze run with parallel steps

const pages = [
  ["overview", "/"],
  ["run-trace", `/runs/${RUN}`],
  ["pr-analysis", "/pr/Aaditya-Nepal00/chesterton-demo/1"],
  ["decisions", "/decisions"],
  ["install", "/install"],
];
const onlyWidths = process.env.SHOT_WIDTHS?.split(","); // e.g. SHOT_WIDTHS=1440
const viewports = [
  ["1440", { width: 1440, height: 900 }],
  ["390", { width: 390, height: 844 }],
].filter(([w]) => !onlyWidths || onlyWidths.includes(w));
const themes = ["light", "dark"];

await mkdir(out, { recursive: true });
const browser = await chromium.launch();
const saved = [];
for (const theme of themes) {
  for (const [vw, viewport] of viewports) {
    const ctx = await browser.newContext({ viewport, colorScheme: theme, deviceScaleFactor: 1 });
    for (const [name, path] of pages) {
      const page = await ctx.newPage();
      const errors = [];
      page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
      page.on("pageerror", (e) => errors.push(String(e)));
      await page.goto(base + path, { waitUntil: "domcontentloaded" });
      // Firestore keeps a listen channel open, so wait for content instead of network idle.
      await page.waitForFunction(() => !document.querySelector(".skeleton"), null, { timeout: 20000 }).catch(() => {});
      await page.evaluate(() => document.fonts.ready);
      await page.waitForTimeout(400);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      const file = join(out, `${name}-${vw}-${theme}.png`);
      await page.screenshot({ path: file, fullPage: true });
      saved.push(file);
      console.log(
        `${name.padEnd(12)} ${vw.padStart(4)} ${theme.padEnd(5)} overflowX=${overflow}px errors=${errors.length}` +
          (errors.length ? `  ${errors[0].slice(0, 160)}` : ""),
      );
      await page.close();
    }
    await ctx.close();
  }
}
await browser.close();
console.log(`\n${saved.length} screenshots in ${out}`);
