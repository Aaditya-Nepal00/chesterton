// Verifies the Install page copy button puts the workflow YAML on the clipboard.
import { chromium } from "playwright";

const base = process.argv[2] ?? "http://localhost:4173";
const browser = await chromium.launch();
const ctx = await browser.newContext({ permissions: ["clipboard-read", "clipboard-write"] });
const page = await ctx.newPage();
await page.goto(`${base}/install`);
await page.getByRole("button", { name: "Copy" }).click();
await page.getByText("Copied").waitFor({ timeout: 5000 });
const clip = await page.evaluate(() => navigator.clipboard.readText());
const expected = await page.locator("pre code").innerText();
const norm = (s) => s.replace(/\r\n/g, "\n").trim();
console.log(
  `clipboard ${clip.length} chars, CRLF=${clip.includes("\r\n")}, matches code block (newline-normalized): ${norm(clip) === norm(expected)}`,
);
// keyboard focus is visible on the nav
await page.keyboard.press("Tab");
await page.keyboard.press("Tab");
const focused = await page.evaluate(() => {
  const el = document.activeElement;
  return `${el?.tagName} "${el?.textContent?.trim().slice(0, 30)}" outline=${getComputedStyle(el).outlineStyle}`;
});
console.log(`focus after 2×Tab: ${focused}`);
await browser.close();
