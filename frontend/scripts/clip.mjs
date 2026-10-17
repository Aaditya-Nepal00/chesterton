// Element screenshot helper for visual checks: node scripts/clip.mjs <path> <selector> <out.png> [width]
import { chromium } from "playwright";

const [path, selector, outFile, width = "390"] = process.argv.slice(2);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: Number(width), height: 900 }, deviceScaleFactor: 2 });
await page.goto(`http://localhost:4173${path}`);
await page.waitForFunction(() => !document.querySelector(".skeleton"), null, { timeout: 20000 }).catch(() => {});
await page.evaluate(() => document.fonts.ready);
await page.locator(selector).first().screenshot({ path: outFile });
await browser.close();
