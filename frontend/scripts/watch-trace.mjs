// Opens /runs/<id> before (or while) the run happens and logs how the timeline fills in.
//   node scripts/watch-trace.mjs <runId> [seconds] [baseUrl]
import { chromium } from "playwright";

const [runId, secs = "60", base = "http://localhost:4173"] = process.argv.slice(2);
const t0 = Date.now();
const log = (m) => console.log(`[${((Date.now() - t0) / 1000).toFixed(1).padStart(5)}s] ${m}`);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
await page.goto(`${base}/runs/${runId}`);
let last = "";
let shots = 0;
while (Date.now() - t0 < Number(secs) * 1000) {
  const snap = await page.evaluate(() => {
    const rows = [...document.querySelectorAll("[aria-label='Agent timeline'] ol li")];
    const pill = document.querySelector("main [class*='tone-']")?.textContent?.trim() ?? "-";
    const done = rows.filter((r) => r.textContent.includes("finished")).length;
    return { rows: rows.length, done, pill, notFound: document.body.textContent.includes("Run not found") };
  });
  const key = `${snap.rows}/${snap.done}/${snap.pill}/${snap.notFound}`;
  if (key !== last) {
    log(snap.notFound ? "page open, run doc not created yet" : `status "${snap.pill}", ${snap.rows} timeline rows (${snap.done} finished)`);
    last = key;
    if (snap.rows && shots < 2 && snap.pill.startsWith("Running")) {
      await page.screenshot({ path: `../.cache/screenshots/run-trace-live-midrun-${++shots}.png`, fullPage: true });
    }
    if (!snap.notFound && !snap.pill.startsWith("Running") && snap.rows) break;
  }
  await page.waitForTimeout(250);
}
await page.screenshot({ path: "../.cache/screenshots/run-trace-live-final.png", fullPage: true });
await browser.close();
