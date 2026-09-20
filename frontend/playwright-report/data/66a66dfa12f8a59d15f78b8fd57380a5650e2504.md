# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: responsive.spec.js >> E. Responsive layout >> UI-TC-375-channel 375x667 channel
- Location: tests\ui\responsive.spec.js:12:7

# Error details

```
Error: expect(received).toBeLessThanOrEqual(expected)

Expected: <= 0
Received:    170
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
    - generic [ref=e30]:
      - button "Switch to dark mode" [ref=e31] [cursor=pointer]:
        - generic [ref=e32]: dark_mode
      - button "Notifications" [ref=e34] [cursor=pointer]:
        - generic [ref=e35]: notifications
      - button "Help" [ref=e37] [cursor=pointer]:
        - generic [ref=e38]: help
      - link [ref=e39] [cursor=pointer]:
        - /url: /channel
        - img "Creator profile" [ref=e40]
  - main [ref=e41]:
    - generic [ref=e42]:
      - generic [ref=e43]:
        - paragraph [ref=e44]: Your Channel
        - heading "Channel Data" [level=2] [ref=e45]
      - button "refresh Refresh" [ref=e46] [cursor=pointer]:
        - generic [ref=e47]: refresh
        - text: Refresh
    - generic [ref=e50]:
      - generic:
        - img "Nimal Creates" [ref=e52]
        - generic:
          - heading "Nimal Creates" [level=3]
          - paragraph: LK · Since 2020
        - link "open_in_new View on YouTube" [ref=e53] [cursor=pointer]:
          - /url: https://www.youtube.com/@nimalcreates
          - generic [ref=e54]: open_in_new
          - text: View on YouTube
      - paragraph: Sri Lankan creator experiments and tutorials.
    - generic [ref=e55]:
      - generic [ref=e56]:
        - generic: group
        - generic: 13K
        - generic: Subscribers
      - generic [ref=e57]:
        - generic: visibility
        - generic: 1.3M
        - generic: Total Views
      - generic [ref=e58]:
        - generic: video_library
        - generic: "86"
        - generic: Videos
    - paragraph [ref=e59]: Last updated 9/20/2026, 3:30:00 PM
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