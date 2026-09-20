# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: responsive.spec.js >> E. Responsive layout >> UI-TC-768-prediction-result-42 768x1024 prediction-result/42
- Location: tests\ui\responsive.spec.js:12:7

# Error details

```
Error: expect(received).toBeLessThanOrEqual(expected)

Expected: <= 0
Received:    66
```

# Page snapshot

```yaml
- generic [ref=e3]:
  - banner [ref=e4]:
    - generic [ref=e5]:
      - generic [ref=e7]:
        - generic [ref=e8]: search
        - textbox "Search predictions..." [ref=e9]
      - navigation [ref=e11]:
        - link "Home" [ref=e12] [cursor=pointer]:
          - /url: /dashboard
        - link "Predictions" [ref=e13] [cursor=pointer]:
          - /url: /new-prediction
      - generic [ref=e14]:
        - button "Switch to dark mode" [ref=e15] [cursor=pointer]:
          - generic [ref=e16]: dark_mode
        - button "Notifications" [ref=e18] [cursor=pointer]:
          - generic [ref=e19]: notifications
        - button "Help" [ref=e21] [cursor=pointer]:
          - generic [ref=e22]: help
        - link "Creator profile" [ref=e23] [cursor=pointer]:
          - /url: /channel
          - img "Creator profile"
  - complementary [ref=e24]:
    - link "insights TrendCast Predictive Brilliance" [ref=e25] [cursor=pointer]:
      - /url: /dashboard
      - generic [ref=e26]: insights
      - generic [ref=e28]:
        - heading "TrendCast" [level=1] [ref=e29]
        - paragraph [ref=e30]: Predictive Brilliance
    - navigation [ref=e31]:
      - link "dashboard Dashboard" [ref=e32] [cursor=pointer]:
        - /url: /dashboard
        - generic [ref=e33]: dashboard
        - generic [ref=e34]: Dashboard
      - link "query_stats Predictions" [ref=e35] [cursor=pointer]:
        - /url: /new-prediction
        - generic [ref=e36]: query_stats
        - generic [ref=e37]: Predictions
      - link "trending_up Trends" [ref=e38] [cursor=pointer]:
        - /url: /trends
        - generic [ref=e39]: trending_up
        - generic [ref=e40]: Trends
      - link "settings Settings" [ref=e41] [cursor=pointer]:
        - /url: /settings
        - generic [ref=e42]: settings
        - generic [ref=e43]: Settings
    - button "logout Log out" [ref=e44] [cursor=pointer]:
      - generic [ref=e45]: logout
      - generic [ref=e46]: Log out
    - link "add_circle New Prediction" [ref=e47] [cursor=pointer]:
      - /url: /new-prediction
      - generic [ref=e48]: add_circle
      - text: New Prediction
  - main [ref=e49]:
    - generic [ref=e50]:
      - generic [ref=e51]:
        - paragraph [ref=e52]:
          - generic [ref=e53]: check_circle
          - text: Analysis Complete
        - heading "Prediction Results" [level=2] [ref=e54]
      - generic [ref=e55]:
        - button "download Export Report" [ref=e56] [cursor=pointer]:
          - generic [ref=e57]: download
          - text: Export Report
        - button "Submit Another" [ref=e58] [cursor=pointer]
    - generic [ref=e59]:
      - img "Video thumbnail" [ref=e61]
      - generic [ref=e62]:
        - generic [ref=e63]:
          - generic [ref=e64]:
            - generic [ref=e65]: Travel & Events
            - generic [ref=e66]: Sri Lanka
            - generic [ref=e67]: food
          - heading "Sri Lankan Street Food Tour" [level=3] [ref=e68]
          - paragraph [ref=e69]: Predicted with 82% confidence · targeting 2026-10-01
        - generic [ref=e70]:
          - paragraph [ref=e71]: 7-Day View Forecast
          - generic [ref=e72]:
            - generic [ref=e73]: 185K
            - generic [ref=e74]:
              - generic [ref=e75]: trending_up
              - text: +14.5% vs avg
    - generic [ref=e77]:
      - heading "Predicted Trajectory" [level=4] [ref=e78]
      - button "Views" [ref=e80] [cursor=pointer]
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