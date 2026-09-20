# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: responsive.spec.js >> E. Responsive layout >> UI-TC-768-dashboard 768x1024 dashboard
- Location: tests\ui\responsive.spec.js:12:7

# Error details

```
Error: expect(received).toBeLessThanOrEqual(expected)

Expected: <= 0
Received:    3
```

# Page snapshot

```yaml
- generic [ref=e3]:
  - complementary [ref=e4]:
    - link "insights TrendCast Predictive Brilliance" [ref=e5] [cursor=pointer]:
      - /url: /dashboard
      - generic [ref=e6]: insights
      - generic [ref=e8]:
        - heading "TrendCast" [level=1] [ref=e9]
        - paragraph [ref=e10]: Predictive Brilliance
    - navigation [ref=e11]:
      - link "dashboard Dashboard" [ref=e12] [cursor=pointer]:
        - /url: /dashboard
        - generic [ref=e13]: dashboard
        - generic [ref=e14]: Dashboard
      - link "query_stats Predictions" [ref=e15] [cursor=pointer]:
        - /url: /new-prediction
        - generic [ref=e16]: query_stats
        - generic [ref=e17]: Predictions
      - link "trending_up Trends" [ref=e18] [cursor=pointer]:
        - /url: /trends
        - generic [ref=e19]: trending_up
        - generic [ref=e20]: Trends
      - link "settings Settings" [ref=e21] [cursor=pointer]:
        - /url: /settings
        - generic [ref=e22]: settings
        - generic [ref=e23]: Settings
    - button "logout Log out" [ref=e24] [cursor=pointer]:
      - generic [ref=e25]: logout
      - generic [ref=e26]: Log out
    - link "add_circle New Prediction" [ref=e27] [cursor=pointer]:
      - /url: /new-prediction
      - generic [ref=e28]: add_circle
      - text: New Prediction
  - banner [ref=e29]:
    - generic [ref=e31]:
      - generic [ref=e32]: search
      - textbox "Search predictions..." [ref=e33]
    - generic [ref=e34]:
      - button "Switch to dark mode" [ref=e35] [cursor=pointer]:
        - generic [ref=e36]: dark_mode
      - button "Notifications" [ref=e38] [cursor=pointer]:
        - generic [ref=e39]: notifications
      - button "Help" [ref=e41] [cursor=pointer]:
        - generic [ref=e42]: help
      - link [ref=e43] [cursor=pointer]:
        - /url: /channel
        - img "Creator profile" [ref=e44]
  - main [ref=e45]:
    - generic [ref=e46]:
      - generic [ref=e47]:
        - paragraph [ref=e48]: Dashboard Overview
        - heading "Welcome back, Nimal" [level=2] [ref=e49]
        - generic [ref=e50]:
          - generic [ref=e51]:
            - generic [ref=e52]: group
            - generic [ref=e53]: 13K
            - generic [ref=e54]: Subscribers
          - generic [ref=e56]:
            - generic [ref=e57]: visibility
            - generic [ref=e58]: 240K
            - generic [ref=e59]: Monthly Views
      - link "auto_awesome Analyze New Content Upload a thumbnail or title to predict performance before publishing. Start Prediction arrow_forward" [ref=e60] [cursor=pointer]:
        - /url: /new-prediction
        - generic:
          - generic [ref=e63]: auto_awesome
          - generic [ref=e65]:
            - heading "Analyze New Content" [level=3] [ref=e66]
            - paragraph [ref=e67]: Upload a thumbnail or title to predict performance before publishing.
          - generic [ref=e68]:
            - text: Start Prediction
            - generic [ref=e69]: arrow_forward
    - generic [ref=e70]:
      - heading "Recent Predictions" [level=3] [ref=e72]
      - generic [ref=e73]:
        - generic [ref=e74]: query_stats
        - paragraph [ref=e75]: No predictions yet — analyze your first piece of content to see it here.
        - link "Create a prediction" [ref=e76] [cursor=pointer]:
          - /url: /new-prediction
```

# Test source

```ts
  1  | import path from "node:path";
  2  | import { fileURLToPath } from "node:url";
  3  | import { test, expect } from "./fixtures.js";
  4  | 
  5  | const screenshotsDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../docs/testing/screenshots/responsive");
  6  | const viewports = [[375, 667], [768, 1024], [1366, 768], [1920, 1080]];
  7  | const pages = ["dashboard", "new-prediction", "prediction-result/42", "trends", "settings", "channel"];
  8  | 
  9  | test.describe("E. Responsive layout", () => {
  10 |   for (const [width, height] of viewports) {
  11 |     for (const route of pages) {
  12 |       test(`UI-TC-${width}-${route.replaceAll("/", "-")} ${width}x${height} ${route}`, async ({ authenticatedPage: page }) => {
  13 |         await page.setViewportSize({ width, height });
  14 |         await page.goto(`/${route}`);
  15 |         await expect(page.locator("body")).toBeVisible();
  16 |         await page.screenshot({ path: path.join(screenshotsDir, `${route.replaceAll("/", "-")}-${width}x${height}.png`), fullPage: true });
  17 |         const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
> 18 |         expect(overflow).toBeLessThanOrEqual(0);
     |                          ^ Error: expect(received).toBeLessThanOrEqual(expected)
  19 |       });
  20 |     }
  21 |   }
  22 | });
```