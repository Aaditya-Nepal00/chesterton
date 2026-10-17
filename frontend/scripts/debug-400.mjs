// Logs failing (4xx/5xx) network responses and console errors for one page.
import { chromium } from "playwright";

const url = process.argv[2] ?? "http://localhost:4173/";
const browser = await chromium.launch();
const page = await browser.newPage();
page.on("response", async (r) => {
  if (r.status() >= 400) {
    let body = "";
    try {
      body = (await r.text()).slice(0, 600);
    } catch {}
    console.log(`HTTP ${r.status()} ${r.request().method()} ${r.url().slice(0, 160)}\n  ${body.replace(/\s+/g, " ")}`);
  }
});
page.on("console", (m) => m.type() === "error" && console.log(`console: ${m.text().slice(0, 300)}`));
await page.goto(url);
await page.waitForTimeout(6000);
const kpis = await page.evaluate(() =>
  [...document.querySelectorAll("section[aria-labelledby=kpis] .mono")].map((e) => e.textContent),
);
console.log("KPI values:", kpis);
await browser.close();
