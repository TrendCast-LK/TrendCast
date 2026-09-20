import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test, expect } from "./fixtures.js";

const rawDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../docs/testing/raw");
const routes = ["/", "/sign-up", "/dashboard", "/new-prediction", "/prediction-result/42", "/trends", "/settings", "/channel"];

test("UI-TC-035 captures browser console errors and warnings across pages", async ({ page }) => {
  const messages = [];
  page.on("console", (message) => {
    if (message.type() === "error" || message.type() === "warning") {
      messages.push({ type: message.type(), text: message.text(), location: message.location() });
    }
  });
  for (const route of routes) {
    await page.goto(route);
    await page.waitForTimeout(100);
  }
  await fs.mkdir(rawDir, { recursive: true });
  await fs.writeFile(path.join(rawDir, "browser-console.json"), JSON.stringify(messages, null, 2));
  expect(messages.filter((message) => message.type === "error")).toEqual([]);
});