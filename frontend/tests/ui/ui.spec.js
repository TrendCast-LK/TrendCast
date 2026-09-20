import path from "node:path";
import { fileURLToPath } from "node:url";
import AxeBuilder from "@axe-core/playwright";
import { test, expect, mockApi, prediction, channel } from "./fixtures.js";

const screenshotsDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../docs/testing/screenshots");

test.describe("public authentication UI", () => {
  test("UI-TC-001 sign-in validates credentials and links to sign-up", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "TrendCast" })).toBeVisible();
    await expect(page.getByLabel("Email")).toHaveAttribute("type", "email");
    await expect(page.getByLabel("Password")).toHaveAttribute("type", "password");
    await expect(page.getByRole("link", { name: "Sign up" })).toHaveAttribute("href", "/sign-up");
    await page.screenshot({ path: path.join(screenshotsDir, "01-sign-in.png"), fullPage: true });
  });

  test("UI-TC-002 sign-in has no automatically detectable critical accessibility violations", async ({ page }) => {
    await page.goto("/");
    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations.filter((violation) => violation.impact === "critical")).toEqual([]);
  });

  test("UI-TC-003 sign-up exposes required fields and password visibility toggle", async ({ page }) => {
    await page.goto("/sign-up");
    await expect(page.getByRole("heading", { name: "Create an account" })).toBeVisible();
    await expect(page.getByLabel("Full Name")).toHaveAttribute("required", "");
    await expect(page.getByLabel("YouTube Channel URL")).toHaveAttribute("type", "url");
    const password = page.locator("#password");
    await expect(password).toHaveAttribute("type", "password");
    await page.getByRole("button", { name: "Show password" }).click();
    await expect(password).toHaveAttribute("type", "text");
  });
});

test.describe("protected route and dashboard", () => {
  test("UI-TC-004 unauthenticated users are redirected to sign-in", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("button", { name: "Sign In" })).toBeVisible();
  });

  test("UI-TC-005 dashboard renders empty state and navigation shell", async ({ authenticatedPage: page }) => {
    await page.goto("/dashboard");
    await expect(page.getByText("Welcome back, Nimal")).toBeVisible();
    await expect(page.getByText(/No predictions yet/)).toBeVisible();
    await expect(page.getByRole("link", { name: "Create a prediction" })).toHaveAttribute("href", "/new-prediction");
    await expect(page.getByRole("button", { name: "Notifications" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Help" })).toBeVisible();
    await page.getByRole("button", { name: "Help" }).click();
    await expect(page.getByText("Help & Support")).toBeVisible();
    await page.screenshot({ path: path.join(screenshotsDir, "02-dashboard-empty.png"), fullPage: true });
  });

  test("UI-TC-006 dashboard renders completed and draft prediction cards", async ({ authenticatedPage: page }) => {
    await mockApi(page, {
      predictions: [
        { id: 42, title: prediction.title, category: prediction.category, status: "complete", predicted_views: 185000 },
        { id: 43, title: "Draft Idea", category: "Education", status: "draft", predicted_views: null },
      ],
    });
    await page.goto("/dashboard");
    await expect(page.getByText("Sri Lankan Street Food Tour")).toBeVisible();
    await expect(page.getByText("Draft Idea")).toBeVisible();
    await expect(page.getByText("Draft", { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: /Sri Lankan Street Food Tour/ })).toHaveAttribute("href", "/prediction-result/42");
  });
});

test.describe("prediction form and results", () => {
  test("UI-TC-007 new prediction validates title, manages tags, and submits a form", async ({ authenticatedPage: page }) => {
    await page.goto("/new-prediction");
    await page.getByRole("button", { name: "Initialize Prediction" }).click();
    await expect(page.getByText("Give the prediction a title first.")).toBeVisible();

    await page.getByPlaceholder("e.g., Q4 Revenue Forecast").fill("My Test Prediction");
    await page.locator("select").selectOption({ label: "Education" });
    await page.getByPlaceholder("Add tags and press enter").fill("testing");
    await page.getByPlaceholder("Add tags and press enter").press("Enter");
    await expect(page.getByRole("button", { name: /testing/ })).toBeVisible();
    await page.getByRole("button", { name: /testing/ }).click();
    await expect(page.getByRole("button", { name: /testing/ })).toHaveCount(0);

    await page.getByPlaceholder("e.g., Q4 Revenue Forecast").fill("Submitted Prediction");
    await page.getByRole("button", { name: "Initialize Prediction" }).click();
    await expect(page).toHaveURL(/\/prediction-result\/42$/);
  });

  test("UI-TC-008 prediction result renders forecast canvas and submit-another navigation", async ({ authenticatedPage: page }) => {
    await page.goto("/prediction-result/42");
    await expect(page.getByRole("heading", { name: "Prediction Results" })).toBeVisible();
    await expect(page.getByText("7-Day View Forecast")).toBeVisible();
    await expect(page.locator("canvas")).toBeVisible();
    await page.getByRole("button", { name: "Submit Another" }).click();
    await expect(page).toHaveURL(/\/new-prediction$/);
  });
});

test.describe("trends, settings, channel, and shared controls", () => {
  test("UI-TC-009 trends renders summary cards, chart, and category breakdown", async ({ authenticatedPage: page }) => {
    await page.goto("/trends");
    await expect(page.getByRole("heading", { name: "Trends" })).toBeVisible();
    await expect(page.getByText("Total Predictions")).toBeVisible();
    await expect(page.getByText("Predicted Views Over Time")).toBeVisible();
    await expect(page.locator("canvas")).toBeVisible();
    await expect(page.getByText(/Travel & Events/).first()).toBeVisible();
  });

  test("UI-TC-010 settings saves profile, validates password confirmation, and toggles theme", async ({ authenticatedPage: page }) => {
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
    await page.getByRole("button", { name: /dark_mode Dark/ }).click();
    await expect(page.locator("html")).toHaveClass(/dark/);
    await page.locator('input[type="text"]').nth(1).fill("Updated Creator");
    await Promise.all([
      page.waitForResponse((response) => response.url().endsWith("/auth/me") && response.request().method() === "PATCH"),
      page.getByRole("button", { name: "Save Changes" }).click(),
    ]);
    await expect(page.locator("section").filter({ hasText: "Profile" }).getByText("Saved")).toBeVisible();
    const passwordSection = page.locator("section").filter({ has: page.getByRole("heading", { name: "Password" }) });
    const passwordInputs = passwordSection.locator('input[type="password"]');
    await passwordInputs.nth(0).fill("old-password");
    await passwordInputs.nth(1).fill("new-password");
    await passwordInputs.nth(2).fill("different-password");
    await page.getByRole("button", { name: "Update Password" }).click();
    await expect(page.getByText("New passwords don't match.")).toBeVisible();
  });

  test("UI-TC-011 channel displays data and supports refresh", async ({ authenticatedPage: page }) => {
    await page.goto("/channel");
    await expect(page.getByRole("heading", { name: "Channel Data" })).toBeVisible();
    await expect(page.getByRole("heading", { name: channel.title })).toBeVisible();
    await expect(page.getByText("Subscribers")).toBeVisible();
    await expect(page.getByRole("link", { name: "View on YouTube" })).toHaveAttribute("href", channel.channel_url);
    await page.getByRole("button", { name: "Refresh" }).click();
    await expect(page.getByRole("button", { name: "Refresh" })).toBeEnabled();
  });

  test("UI-TC-012 notifications show unread items and mark-all-read", async ({ authenticatedPage: page }) => {
    await mockApi(page, {
      notifications: [{ id: 1, type: "welcome", title: "Welcome", message: "Welcome to TrendCast", read: false, created_at: "2026-09-20T10:00:00Z" }],
    });
    await page.goto("/dashboard");
    await page.getByRole("button", { name: "Notifications" }).click();
    await expect(page.getByText("Welcome", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Mark all read" }).click();
    await expect(page.getByRole("button", { name: "Mark all read" })).toHaveCount(0);
  });
});