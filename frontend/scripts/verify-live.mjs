// Checks what the live site actually shows (activity rows, KPIs, PR #1 questions).
import { chromium } from "playwright";

const base = process.argv[2] ?? "https://chesterton-aaditya-26.web.app";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("console", (m) => m.type() === "error" && errors.push(m.text().replace(/key=[\w-]+/, "key=<redacted>")));
const wait = () => page.waitForFunction(() => !document.querySelector(".skeleton"), null, { timeout: 20000 });

await page.goto(base + "/");
await wait();
const title = await page.title();
const rows = await page.$$eval("section[aria-labelledby=activity] ul li", (lis) =>
  lis.map((li) => li.textContent.replace(/\(opens in a new tab\)/g, "").replace(/View trace.*/, "").replace(/\s+/g, " ").trim()),
);
const kpis = await page.$$eval("section[aria-labelledby=kpis] .mono", (e) => e.map((x) => x.textContent));
console.log(`title: ${title}`);
console.log(`KPIs: ${kpis.join(" | ")}`);
console.log(`activity rows: ${rows.length}`);
rows.forEach((r) => console.log(`  ${r.slice(0, 120)}`));
console.log(`selftest rows: ${rows.filter((r) => /selftest/i.test(r)).length}, paused rows: ${rows.filter((r) => /Paused/.test(r)).length}`);

await page.goto(base + "/pr/Aaditya-Nepal00/chesterton-demo/1");
await wait();
const qs = await page.$$eval("section[aria-labelledby=questions] li p", (ps) => ps.map((p) => p.textContent.trim()));
console.log("PR #1 questions:");
qs.forEach((q) => console.log(`  ${q}`));
console.log(`invented baselines left: ${qs.filter((q) => /instead of|default/i.test(q)).length}`);
console.log(`console errors: ${errors.length}${errors.length ? " " + errors[0] : ""}`);
await browser.close();
