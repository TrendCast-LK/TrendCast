# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: responsive.spec.js >> E. Responsive layout >> UI-TC-375-settings 375x667 settings
- Location: tests\ui\responsive.spec.js:12:7

# Error details

```
Error: expect(received).toBeLessThanOrEqual(expected)

Expected: <= 0
Received:    201
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
      - paragraph [ref=e43]: Account
      - heading "Settings" [level=2] [ref=e44]
      - paragraph [ref=e45]: Manage your profile, channel stats, appearance, and password.
    - generic [ref=e46]:
      - heading "Appearance" [level=3]
      - paragraph: Choose how TrendCast looks on this device.
      - generic:
        - button "light_mode Light" [ref=e47] [cursor=pointer]:
          - generic [ref=e48]: light_mode
          - text: Light
        - button "dark_mode Dark" [ref=e49] [cursor=pointer]:
          - generic [ref=e50]: dark_mode
          - text: Dark
    - generic [ref=e51]:
      - heading "Profile" [level=3]
      - generic:
        - generic:
          - generic:
            - generic: Full Name
            - textbox [ref=e52]: Nimal Perera
          - generic:
            - generic: Email
            - textbox "Email changes aren't supported yet" [disabled] [ref=e53]: nimal@example.com
        - generic:
          - generic:
            - generic: Subscribers
            - spinbutton [ref=e54]: "12500"
            - paragraph: Used as the baseline for prediction forecasts.
          - generic:
            - generic: Monthly Views
            - spinbutton [ref=e55]: "240000"
        - button "Save Changes" [ref=e56] [cursor=pointer]
    - generic [ref=e57]:
      - heading "Password" [level=3]
      - generic:
        - generic:
          - generic: Current Password
          - textbox [ref=e58]
        - generic:
          - generic:
            - generic: New Password
            - textbox [ref=e59]
          - generic:
            - generic: Confirm New Password
            - textbox [ref=e60]
        - button "Update Password" [ref=e61] [cursor=pointer]
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