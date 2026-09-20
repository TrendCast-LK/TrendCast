import { test, expect } from "@playwright/test";

test.describe("@live backend availability", () => {
  let backendReady = false;

  test.beforeAll(async ({ request }) => {
    try {
      const response = await request.get("http://127.0.0.1:8000/forecast/health", { timeout: 3000 });
      backendReady = response.ok();
    } catch {
      backendReady = false;
    }
  });

  test("frontend can be opened while the real backend health endpoint is ready", async ({ page }) => {
    test.skip(!backendReady, "Live backend http://127.0.0.1:8000/forecast/health is not ready");
    const health = await page.request.get("http://127.0.0.1:8000/forecast/health");
    expect(health.ok()).toBeTruthy();
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Sign In" })).toBeVisible();
  });
});