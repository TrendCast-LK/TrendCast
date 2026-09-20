import AxeBuilder from "@axe-core/playwright";
import { test, expect, channel } from "./fixtures.js";

const pages = [
  ["/dashboard", "Dashboard Overview"],
  ["/new-prediction", "Create New Prediction"],
  ["/prediction-result/42", "Prediction Results"],
  ["/trends", "Trends"],
  ["/settings", "Settings"],
  ["/channel", "Channel Data"],
];

test.describe("C and G. Data display and accessibility", () => {
  for (const [route, heading] of pages) {
    test(`UI-TC-${String(23 + pages.findIndex((item) => item[0] === route)).padStart(3, "0")} axe scan ${route}`, async ({ authenticatedPage: page }, testInfo) => {
      await page.goto(route);
      const results = await new AxeBuilder({ page }).analyze();
      await testInfo.attach(`axe-${route.slice(1).replaceAll("/", "-") || "home"}.json`, {
        body: JSON.stringify(results, null, 2),
        contentType: "application/json",
      });
      expect(results.violations).toEqual([]);
      await expect(page.getByText(heading, { exact: true }).first()).toBeVisible();
    });
  }

  test("UI-TC-029 prediction canvas is rendered and non-empty", async ({ authenticatedPage: page }) => {
    await page.goto("/prediction-result/42");
    const canvas = page.locator("canvas");
    await expect(canvas).toBeVisible();
    const nonBlankPixels = await canvas.evaluate((element) => {
      const context = element.getContext("2d");
      const pixels = context.getImageData(0, 0, element.width, element.height).data;
      let nonBlank = 0;
      for (let index = 3; index < pixels.length; index += 4) if (pixels[index] > 0) nonBlank += 1;
      return nonBlank;
    });
    expect(nonBlankPixels).toBeGreaterThan(0);
  });

  test("UI-TC-030 keyboard navigation reaches Forecast form controls and image alt text exists", async ({ authenticatedPage: page }) => {
    await page.goto("/new-prediction");
    const focusable = [];
    for (let index = 0; index < 35; index += 1) {
      await page.keyboard.press("Tab");
      focusable.push(await page.evaluate(() => document.activeElement?.tagName + ":" + (document.activeElement?.getAttribute("type") || document.activeElement?.textContent?.trim().slice(0, 30))));
    }
    expect(focusable.some((item) => item.startsWith("INPUT"))).toBeTruthy();
    expect(focusable.some((item) => item.startsWith("BUTTON"))).toBeTruthy();
    await page.goto("/channel");
    for (const image of await page.locator("img").all()) expect(await image.getAttribute("alt")).toBeTruthy();
  });
});

test.describe("D. Error handling", () => {
  test("UI-TC-031 shows a clear 400 error on prediction result", async ({ authenticatedPage: page }) => {
    await page.route("http://localhost:8000/predictions/42", (route) => route.fulfill({ status: 400, json: { detail: "Invalid prediction id" } }));
    await page.goto("/prediction-result/42");
    await expect(page.getByText("Invalid prediction id")).toBeVisible();
    await expect(page.getByRole("link", { name: "Back to Dashboard" })).toBeVisible();
  });

  test("UI-TC-032 shows a clear 500 error on channel load", async ({ authenticatedPage: page }) => {
    await page.route("http://localhost:8000/channel/me", (route) => route.fulfill({ status: 500, json: { detail: "Channel service failed" } }));
    await page.goto("/channel");
    await expect(page.getByText("Channel service failed")).toBeVisible();
  });

  test("UI-TC-033 shows a network error without hanging", async ({ authenticatedPage: page }) => {
    await page.route("http://localhost:8000/trends/summary", (route) => route.abort("failed"));
    await page.goto("/trends");
    await expect(page.getByText(/Can't reach the server|Couldn.t load trends/)).toBeVisible({ timeout: 5_000 });
  });

  test("UI-TC-034 shows the loading state during a slow response", async ({ authenticatedPage: page }) => {
    await page.route("http://localhost:8000/channel/me", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 500));
      await route.fulfill({ json: channel });
    });
    await page.goto("/channel");
    await expect(page.getByText("progress_activity")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Channel Data" })).toBeVisible();
  });
});