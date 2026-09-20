import path from "node:path";
import { fileURLToPath } from "node:url";
import { test, expect } from "./fixtures.js";

const screenshotsDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../docs/testing/screenshots/responsive");
const viewports = [[375, 667], [768, 1024], [1366, 768], [1920, 1080]];
const pages = ["dashboard", "new-prediction", "prediction-result/42", "trends", "settings", "channel"];

test.describe("E. Responsive layout", () => {
  for (const [width, height] of viewports) {
    for (const route of pages) {
      test(`UI-TC-${width}-${route.replaceAll("/", "-")} ${width}x${height} ${route}`, async ({ authenticatedPage: page }) => {
        await page.setViewportSize({ width, height });
        await page.goto(`/${route}`);
        await expect(page.locator("body")).toBeVisible();
        await page.screenshot({ path: path.join(screenshotsDir, `${route.replaceAll("/", "-")}-${width}x${height}.png`), fullPage: true });
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        expect(overflow).toBeLessThanOrEqual(0);
      });
    }
  }
});