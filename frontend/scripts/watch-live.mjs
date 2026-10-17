// Watches the Overview's live activity feed and logs what the open page shows over time.
//   node scripts/watch-live.mjs [seconds]
import { chromium } from "playwright";

const seconds = Number(process.argv[2] ?? 150);
const t0 = Date.now();
const log = (m) => console.log(`[${((Date.now() - t0) / 1000).toFixed(1).padStart(6)}s] ${m}`);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto("http://localhost:4173/");
await page.waitForSelector("section[aria-labelledby=activity] li a[href^='/runs/']", { timeout: 30000 });
const firstRow = async () =>
  page.evaluate(() => {
    const li = document.querySelector("section[aria-labelledby=activity] ul li");
    const a = li?.querySelector("a[href^='/runs/']");
    return { text: li?.textContent?.replace(/\s+/g, " ").trim() ?? "", href: a?.getAttribute("href") ?? "" };
  });
const start = await firstRow();
log(`page open; first row: ${start.text.slice(0, 110)}`);
let last = start;
let newHref = null;
while (Date.now() - t0 < seconds * 1000) {
  await page.waitForTimeout(1000);
  const now = await firstRow();
  if (now.text !== last.text) {
    log(`first row changed (no reload): ${now.text.slice(0, 120)}`);
    if (now.href !== start.href) newHref = now.href;
    last = now;
  }
  if (newHref && !/Running/.test(now.text)) break;
}
if (newHref) {
  const trace = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await trace.goto("http://localhost:4173" + newHref);
  await trace.waitForSelector("[aria-label='Agent timeline'] ol li", { timeout: 30000 });
  const rows = await trace.locator("[aria-label='Agent timeline'] ol li").count();
  const status = await trace.locator("main h1 ~ * , main").first().evaluate(() =>
    document.querySelector("main [class*=tone-]")?.textContent?.trim(),
  );
  log(`trace ${newHref}: ${rows} timeline rows, status pill "${status}"`);
  await trace.screenshot({ path: "../.cache/screenshots/run-trace-guard-live-1440-light.png", fullPage: true });
}
await browser.close();
