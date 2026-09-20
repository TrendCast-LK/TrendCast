import { test, expect } from "./fixtures.js";

test.describe("A. Navigation and routing", () => {
  test("UI-TC-013 direct URLs render every implemented page", async ({ authenticatedPage: page }) => {
    const routes = [
      ["/dashboard", "Dashboard Overview"],
      ["/new-prediction", "Create New Prediction"],
      ["/prediction-result/42", "Prediction Results"],
      ["/trends", "Trends"],
      ["/settings", "Settings"],
      ["/channel", "Channel Data"],
    ];
    for (const [route, heading] of routes) {
      await page.goto(route);
      await expect(page.getByText(heading, { exact: true }).first()).toBeVisible();
    }
  });

  test("UI-TC-014 unknown URLs redirect to sign-in", async ({ page }) => {
    await page.goto("/does-not-exist");
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("button", { name: "Sign In" })).toBeVisible();
  });

  test("UI-TC-015 sidebar and result links navigate to their destinations", async ({ authenticatedPage: page }) => {
    await page.goto("/dashboard");
    await page.getByRole("link", { name: "Predictions" }).click();
    await expect(page).toHaveURL(/\/new-prediction$/);
    await page.getByRole("link", { name: "Trends" }).click();
    await expect(page).toHaveURL(/\/trends$/);
    await page.getByRole("link", { name: "Settings" }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await page.getByTitle("Channel data").click();
    await expect(page).toHaveURL(/\/channel$/);
  });

  test("UI-TC-016 browser back and forward restore page history", async ({ authenticatedPage: page }) => {
    await page.goto("/dashboard");
    await page.goto("/trends");
    await page.goBack();
    await expect(page).toHaveURL(/\/dashboard$/);
    await page.goForward();
    await expect(page).toHaveURL(/\/trends$/);
  });

  test("UI-TC-017 keeps the implemented static document title on direct page loads", async ({ authenticatedPage: page }) => {
    for (const route of ["/dashboard", "/new-prediction", "/trends", "/settings", "/channel"]) {
      await page.goto(route);
      await expect(page).toHaveTitle("TrendCast");
    }
  });
});