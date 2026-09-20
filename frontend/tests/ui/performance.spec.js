import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test, expect } from "./fixtures.js";

const rawDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../docs/testing/raw");
const routes = ["/", "/sign-up", "/dashboard", "/new-prediction", "/prediction-result/42", "/trends", "/settings", "/channel"];

test("UI-TC-036 records request count and transferred response bytes per page", async ({ page }) => {
  const results = [];
  for (const route of routes) {
    const responses = [];
    page.on("response", (response) => responses.push(response));
    const started = Date.now();
    await page.goto(route);
    await page.waitForTimeout(250);
    const entries = await page.evaluate(() => performance.getEntriesByType("resource").map((entry) => ({ name: entry.name, transferSize: entry.transferSize || 0 })));
    results.push({
      route,
      loadTimeMs: Date.now() - started,
      requestCount: responses.length,
      responseCount: responses.filter((response) => response.ok()).length,
      pageWeightBytes: entries.reduce((total, entry) => total + entry.transferSize, 0),
      resources: entries,
    });
    await page.reload();
  }
  await fs.mkdir(rawDir, { recursive: true });
  await fs.writeFile(path.join(rawDir, "playwright-network-metrics.json"), JSON.stringify(results, null, 2));
  expect(results).toHaveLength(routes.length);
});