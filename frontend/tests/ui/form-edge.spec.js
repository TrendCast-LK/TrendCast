import { test, expect } from "./fixtures.js";

test.describe("B. Forecast form", () => {
  test("UI-TC-018 accepts valid form data, schedule time, and a file", async ({ authenticatedPage: page }) => {
    await page.goto("/new-prediction");
    await page.getByPlaceholder("e.g., Q4 Revenue Forecast").fill("Valid forecast");
    await page.getByText("Target Time (Optional)").locator("..").locator("input").fill("13:45");
    await page.locator('input[type="file"]').setInputFiles({ name: "thumbnail.png", mimeType: "image/png", buffer: Buffer.from("png") });
    await expect(page.getByText("thumbnail.png")).toBeVisible();
    await expect(page.getByRole("button", { name: "Initialize Prediction" })).toBeEnabled();
  });

  test("UI-TC-019 rejects an empty title without an API call", async ({ authenticatedPage: page }) => {
    let createCalls = 0;
    await page.route("http://localhost:8000/predictions", async (route) => {
      createCalls += 1;
      await route.fulfill({ json: { id: 42 } });
    });
    await page.goto("/new-prediction");
    await page.getByRole("button", { name: "Initialize Prediction" }).click();
    await expect(page.getByText("Give the prediction a title first.")).toBeVisible();
    expect(createCalls).toBe(0);
  });

  test("UI-TC-020 shows the submit loading state and prevents duplicate submissions", async ({ authenticatedPage: page }) => {
    let createCalls = 0;
    await page.route("http://localhost:8000/predictions", async (route) => {
      createCalls += 1;
      await new Promise((resolve) => setTimeout(resolve, 700));
      await route.fulfill({ json: { id: 42 } });
    });
    await page.goto("/new-prediction");
    await page.getByPlaceholder("e.g., Q4 Revenue Forecast").fill("Double click test");
    const submit = page.getByRole("button", { name: "Initialize Prediction" });
    await submit.dblclick();
    await expect(page.getByText("Predicting…")).toBeVisible();
    await expect(page).toHaveURL(/\/prediction-result\/42$/, { timeout: 5_000 });
    expect(createCalls).toBe(1);
  });

  test("UI-TC-021 renders long, Sinhala, Tamil, mixed-script, and HTML title text as text", async ({ authenticatedPage: page }) => {
    const title = "<script>window.__uiTestXss = true</script> සිංහල தமிழ் TrendCast ".repeat(4);
    await page.route("http://localhost:8000/predictions/42", async (route) => {
      await route.fulfill({ json: { id: 42, title, category: "Education", tags: [], predicted_views: 100, confidence: 0.5, change_vs_avg: 0, thumbnail_url: null, trajectory: [{ day: 1, views: 100 }] } });
    });
    await page.goto("/prediction-result/42");
    await expect(page.getByRole("heading", { name: title })).toBeVisible();
    expect(await page.evaluate(() => window.__uiTestXss)).toBeUndefined();
  });

  test("UI-TC-022 uses browser URL validation for the implemented channel URL field", async ({ page }) => {
    await page.goto("/sign-up");
    await page.getByLabel("Full Name").fill("Test User");
    await page.getByLabel("Email").fill("test@example.com");
    await page.getByLabel("YouTube Channel URL").fill("not-a-url");
    await page.locator("#password").fill("password123");
    expect(await page.getByLabel("YouTube Channel URL").evaluate((element) => element.validity.valid)).toBe(false);
  });
});