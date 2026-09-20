# TrendCast UI Testing Report

## 1. Introduction and Scope

**Project:** TrendCast, Group 20, CS3501  
**Date:** 2026-09-20  
**Scope:** React/Vite frontend UI only. Tests cover rendered controls, routing, browser validation, mocked API states, charts, accessibility, responsiveness, console output, and browser performance evidence. Backend logic, database behavior, and ML accuracy are out of scope.

All normal UI tests mock API calls with `page.route()`. The separate `@live` test checks backend health and skips when `http://127.0.0.1:8000/forecast/health` is not ready.

## 2. Target Test Items

The source router contains `/`, `/sign-up`, `/dashboard`, `/new-prediction`, `/prediction-result/:id`, `/trends`, `/settings`, `/channel`, and a wildcard redirect to `/`.

| Page | Source-grounded UI items |
|---|---|
| `/` | Email, password, Google, Sign In, Forgot password anchor, Sign up, theme toggle, loading text, API error. |
| `/sign-up` | Full Name, Email, YouTube Channel URL, Password, password visibility, Create Account, Google, Sign in, loading text, API error, theme toggle. |
| `/dashboard` | Welcome summary, subscribers, monthly views, Analyze New Content, recent prediction cards, draft/empty states, sidebar, search, notifications, help, channel, logout, New Prediction. |
| `/new-prediction` | Title, description, minutes, seconds, category select, tags, target date/time, file input/dropzone, Save as Draft, Initialize Prediction, title/API errors and submit loading states. |
| `/prediction-result/:id` | Loading/error/not-found states, Back to Dashboard, Export Report, Submit Another, thumbnail, tags, confidence, forecast value, change indicator, Views button, Chart.js canvas. |
| `/trends` | Loading/error/empty states, four stat tiles, Chart.js canvas, category breakdown bars, Create a prediction link. |
| `/settings` | Light/Dark buttons, profile fields, disabled email, Save Changes/Saved, password fields, Update Password/Password updated, mismatch/API errors. |
| `/channel` | Loading/error/not-fetched states, Refresh/Try again, channel metadata, YouTube link, three stat tiles, last updated. |
| Shared | Sidebar destinations, result topbar, search inputs, theme, notifications loading/empty/unread/read-all, help FAQs/contact, logout. |

No `Explorer.jsx`, `ChannelDetail.jsx`, `Forecast.jsx`, `frontend/src/api/client.js`, Recharts charts, HTML tables, thumbnail URL field, or “No specific channel” control exists in this checkout. These requested items are recorded as not applicable, not guessed or tested as implemented features. The implemented equivalents are `Channel.jsx`, `PredictionResult.jsx`, `NewPrediction.jsx`, and `frontend/src/lib/api.js`.

## 3. Test Approach

| Item | Approach |
|---|---|
| Technique Objective | Check that implemented UI controls, routes, states, charts, forms, errors, accessibility, and responsive layouts behave as specified by the current source. |
| Technique | Playwright end-to-end tests in Chromium and Firefox; `page.route()` API mocks; direct URL and history checks; axe-core scans; keyboard traversal; browser validity checks; screenshots; Performance API metrics; Lighthouse CLI attempt. |
| Oracles | URL, heading, visible text, control state, DOM attributes, browser validity, canvas presence/non-blank pixels, document overflow, axe result, console output, API request count, and command exit/output. |
| Required Tools | Node `v24.17.0`, npm `11.13.0`, Playwright `1.63.0`, Chromium, Firefox, axe-core Playwright `4.13.0`, Vite `8.2.2`, ESLint `10.11.0`, cspell, Lighthouse `13.5.0`. Raw tool output is under [raw](raw/). |
| Success Criteria | A test passes only when its oracle is satisfied. A product failure remains FAIL. A missing feature or unavailable tool remains Skipped/Not applicable. |
| Special Considerations | No `src/` application files were changed. WebKit installation was attempted but Playwright reported missing `jxl_cms.dll`. Lighthouse produced one valid home report on retry, but later route runs hit Windows `EPERM` Chrome Launcher cleanup errors. |

## 4. Test Environment

| Field | Value |
|---|---|
| OS | Windows, as provided by the VS Code environment |
| Frontend | `http://localhost:5173`; Vite preview `http://localhost:4173` |
| Backend | `http://127.0.0.1:8000`; live health test skipped when unavailable |
| Node/npm | Node `v24.17.0`; npm `11.13.0` |
| Browsers | Chromium and Firefox, Playwright `1.63.0`; WebKit unavailable due `jxl_cms.dll` |
| Viewports | 375x667, 768x1024, 1366x768, 1920x1080 |
| Date | 2026-09-20 |
| Git commit | `d66b3e6f683eed5f5068b491888711d3740f63de` |

## 5. Test Cases and Results

The test source contains IDs in the test title. The full raw run is [playwright-final.txt](raw/playwright-final.txt). The focused raw runs are [quality-chromium.txt](raw/quality-chromium.txt), [responsive-chromium.txt](raw/responsive-chromium.txt), and [console-performance.txt](raw/console-performance.txt).

### A. Navigation and routing

| IDs | Page | Description | Steps | Expected / actual | Browser | Mode |
|---|---|---|---|---|---|---|
| UI-TC-013 to UI-TC-017 | All implemented routes | Direct URLs, unknown URL, nav links, back/forward, titles | Open routes; click links; use history | Implemented routes and wildcard redirect passed. Static title `TrendCast` passed on all checked routes. | Chromium, Firefox | Mocked |

### B. Forecast form

| IDs | Page | Description | Expected / actual | Status | Browser | Mode |
|---|---|---|---|---|---|---|
| UI-TC-018 | New Prediction | Valid input, upload time, file | Passed with implemented fields and file input. | Pass | Chromium, Firefox | Mocked |
| UI-TC-019 | New Prediction | Empty title | Client error shown; no create call. | Pass | Chromium, Firefox | Mocked |
| UI-TC-020 | New Prediction | Loading and double-click submit | `Predicting…` shown; one create call; navigation completed. | Pass | Chromium, Firefox | Mocked |
| UI-TC-021 | Prediction Result | Long Sinhala/Tamil/mixed-script and HTML text | Text rendered; script did not execute. | Pass | Chromium, Firefox | Mocked |
| UI-TC-022 | Sign-up | Invalid implemented channel URL | Browser validity was false. | Pass | Chromium, Firefox | Mocked |
| N/A | Forecast form | Empty thumbnail URL, “No specific channel” warning | No such controls exist in source. | Skipped/N/A | Both | N/A |

### C. Data display

| IDs | Page | Description | Expected / actual | Status | Browser | Mode |
|---|---|---|---|---|---|---|
| UI-TC-023 to UI-TC-028 | Implemented pages | Axe-backed page rendering and data surfaces | Dashboard, Trends and Channel data surfaces rendered. New Prediction, Prediction Result and Settings axe checks failed due source accessibility defects. | Mixed; see defects | Chromium, Firefox | Mocked |
| UI-TC-029 | Prediction Result | Forecast chart presence and pixels | Canvas rendered with non-zero pixels. | Pass | Chromium, Firefox | Mocked |
| N/A | Explorer / ChannelDetail | Channel/video lists | Routes/components do not exist. | Skipped/N/A | Both | N/A |
| N/A | Forecast | SVG axes and 7 points | Current chart is Chart.js canvas, not SVG; no 7-point requirement in source. | Skipped/N/A | Both | N/A |

### D. Error handling

| ID | Page | Description | Expected / actual | Status | Browser | Mode |
|---|---|---|---|---|---|---|
| UI-TC-031 | Prediction Result | API 400 | Clear API detail and Back to Dashboard rendered. | Pass | Chromium, Firefox | Mocked |
| UI-TC-032 | Channel | API 500 | Clear API detail rendered. | Pass | Chromium, Firefox | Mocked |
| UI-TC-033 | Trends | Network failure | Reachability/load error rendered without hanging. | Pass | Chromium, Firefox | Mocked |
| UI-TC-034 | Channel | Slow response | Loading indicator appeared, then content rendered. | Pass | Chromium, Firefox | Mocked |

### E. Responsive layout

| IDs | Page/viewports | Description | Expected / actual | Status | Browser | Mode |
|---|---|---|---|---|---|---|
| UI-TC-375-* to UI-TC-1920-* | Dashboard, New Prediction, Result, Trends, Settings, Channel at 375x667, 768x1024, 1366x768, 1920x1080 | No horizontal overflow and screenshot per page/viewport | Chromium run: 24 cases, 18 passed, 6 failed for horizontal overflow at 375/768 on Dashboard, Trends, Settings, Channel, and Result. Screenshots are in [responsive screenshots](screenshots/responsive/). | Mixed | Chromium | Mocked |
| Same IDs | Same | Firefox execution | Included in full suite; raw output records browser result. | See raw final | Firefox | Mocked |

### F. Cross-browser

| Browser | Scope | Result |
|---|---|---|
| Chromium | Full UI suite and responsive suite | Executed. Exact final total is recorded in raw final output. |
| Firefox | Full UI suite | Executed. Accessibility failures reproduced. |
| WebKit | Installation/run | Skipped. Playwright reported missing `jxl_cms.dll` on Windows. |

### G. Accessibility

| ID | Page | Description | Actual result | Status |
|---|---|---|---|---|
| UI-TC-002 | Sign-in | axe critical scan | Passed in prior and current suite. |
| UI-TC-023 to UI-TC-028 | All implemented pages | axe scan | Standalone Chromium: 12 tests, 9 passed, 3 failed. Failures: New Prediction, Prediction Result, Settings. The reported violations include missing explicit/implicit labels and missing accessible names for inputs. Firefox reproduced the page failures. |
| UI-TC-030 | New Prediction / Channel | Keyboard focus and image alt text | Passed keyboard reachability and non-empty image alt checks. |

### H. Code inspection

| Check | Actual result | Status |
|---|---|---|
| ESLint `npx eslint src` | Could not run with installed ESLint `10.11.0`: no `eslint.config.*` exists. Exit code 2. No source lint result is claimed. | Skipped/tooling |
| cspell | `23` files checked; `27` issues in `12` files. Raw output: [cspell-src.txt](raw/cspell-src.txt). | Fail/inspection |
| Unused imports/variables | No separate unused-import tool was configured. ESLint did not run, so none are claimed. | Skipped |
| Browser console | `browser-console.json` is `[]`: no captured errors or warnings across the implemented routes in the passing console test. | Pass |

### I. UI performance

| Check | Actual result | Status |
|---|---|---|
| Vite build | Passed. `index.html` 1.33 kB / 0.66 kB gzip; CSS 43.91 kB / 8.34 kB gzip; JS 509.53 kB / 160.22 kB gzip. Vite emitted a chunk-over-500 kB warning. | Pass |
| Lighthouse | Lighthouse `13.5.0`. Home retry produced a JSON report: performance `0.96`, accessibility `1`, best practices `1`. Repeated route run hit Windows `EPERM` temporary-directory cleanup errors, so route scores are not claimed. | Partial/tool failure |
| Requests and weight | Playwright raw metrics: `/` 36 requests, 6,808,543 bytes; `/sign-up` 39, 39,068 bytes; each protected route measured 38/39 requests and 7,800 bytes in the captured run. Full CSV: [playwright-network-summary.csv](raw/playwright-network-summary.csv). | Pass |

## 6. Test Evaluation Summary

The final cross-browser run executed **118 test cases: 101 passed, 15 failed, and 2 skipped**. Excluding skipped tests, **116 tests executed**: pass rate **87.07%** and fail rate **12.93%**. The skipped tests were the live backend health check in Chromium and Firefox. Standalone verified runs were: quality Chromium **12 total, 9 passed, 3 failed**; responsive Chromium **24 total, 18 passed, 6 failed**; console/performance Chromium **2 passed**. Raw final output: [playwright-final.txt](raw/playwright-final.txt). JSON reporter output: [results.json](../../frontend/playwright-report/results.json).

Per-browser execution from the Playwright JSON report: Chromium had 51 passed, 9 failed, and 1 skipped; Firefox had 50 passed, 10 failed, and 1 skipped. WebKit was not executable because of the missing Windows dependency. Tests tagged/live are mocked unless explicitly marked live in `live.spec.js`.

## 7. Defects Found

| ID | Title | Page | Expected vs actual | Severity | Evidence |
|---|---|---|---|---|---|
| DEF-UI-001 | Form controls lack accessible labels | New Prediction, Prediction Result, Settings | Inputs should have explicit/implicit labels or accessible names. axe reported missing label/name attributes. | High | Failing axe tests and attached axe JSON in Playwright results; raw [quality-chromium.txt](raw/quality-chromium.txt). |
| DEF-UI-002 | Horizontal overflow at narrow viewports | Dashboard, Trends, Settings, Channel, Prediction Result | At 375x667 and/or 768x1024, document width should not exceed viewport. Six Chromium responsive cases failed. | Medium | Raw [responsive-chromium.txt](raw/responsive-chromium.txt) and screenshots under [responsive](screenshots/responsive/). |
| DEF-UI-003 | Route-specific document titles are absent | All routes | Each page should have an informative title. Actual title remains static `TrendCast` on all routes. | Low | Passing UI-TC-017 records the actual behavior; no app source changes made. |
| DEF-UI-004 | ESLint inspection is not configured | Frontend source | ESLint should run over `frontend/src`. Actual ESLint 10 exits because `eslint.config.*` is missing. | Medium/tooling | [eslint-src.txt](raw/eslint-src.txt). |
| DEF-UI-005 | Lighthouse route run fails on Windows cleanup | Performance tooling | Lighthouse should produce scores for each page. Actual repeated runs hit `EPERM` cleanup errors. | Medium/tooling | [lighthouse-command.txt](raw/lighthouse-command.txt). |

## 8. Accessibility, Code Inspection and Performance Evidence

Axe found no violations on the sign-in, dashboard and Trends scans that passed, but it found label/name violations on New Prediction, Prediction Result and Settings. The tests deliberately remain failed. Browser console capture was empty. cspell reported 27 issues in 12 files. ESLint was unavailable because project configuration is absent. Vite build passed with a 509.53 kB minified JS bundle. One valid Lighthouse report exists for `/` with performance `0.96`, accessibility `1`, and best practices `1`; route-level scores are unavailable due the Windows `EPERM` failure. Network metrics and raw reports are under [docs/testing/raw](raw/).

## 9. Limitations and Manual Checks Still To Do

- WebKit needs the missing `jxl_cms.dll` dependency or another supported environment.
- Lighthouse route scores need rerunning in an environment where Chrome Launcher can remove its temporary profile.
- ESLint needs a project config or a compatible legacy ESLint version.
- Real backend tests need the backend running and `/forecast/health` ready.
- Explorer, ChannelDetail and Forecast-specific controls need testing only if those pages are added to the frontend.
- Manual visual review is still required for overlap, typography, color contrast, chart readability, and Sinhala/Tamil rendering on target devices.
- Manual phone testing at 375x667 and real-device keyboard/screen-reader testing remain outstanding.
- User acceptance feedback: not performed; result intentionally left empty.

## 10. How to Re-run Everything

From the repository root:

```powershell
Push-Location frontend
npm install
npx playwright install chromium firefox
npm run build 2>&1 | Tee-Object -FilePath ..\docs\testing\raw\vite-build.txt
npx playwright test --workers=1 2>&1 | Tee-Object -FilePath ..\docs\testing\raw\playwright-final.txt
npx playwright test tests/ui/responsive.spec.js --project=chromium --workers=1 2>&1 | Tee-Object -FilePath ..\docs\testing\raw\responsive-chromium.txt
npx eslint src 2>&1 | Tee-Object -FilePath ..\docs\testing\raw\eslint-src.txt
npx cspell "src/**/*.{js,jsx,css,html}" 2>&1 | Tee-Object -FilePath ..\docs\testing\raw\cspell-src.txt
npm run preview -- --host localhost --port 4173
Pop-Location
```

In another terminal, after preview is running:

```powershell
Push-Location frontend
npx lighthouse http://localhost:4173/ --output=json --output-path=../docs/testing/raw/lighthouse-home.json --no-enable-error-reporting --chrome-flags="--headless=new"
Pop-Location
```

The Playwright config starts the Vite dev server automatically. HTML/JSON Playwright reports are generated under `frontend/playwright-report/`.

## 11. References

- Playwright Test, https://playwright.dev/docs/test-intro, accessed 2026-09-20.
- Playwright browsers, https://playwright.dev/docs/browsers, accessed 2026-09-20.
- axe-core Playwright, https://github.com/dequelabs/axe-core-npm, accessed 2026-09-20.
- Vite, https://vite.dev/guide/, accessed 2026-09-20.
- Lighthouse CLI, https://github.com/GoogleChrome/lighthouse, accessed 2026-09-20.
- ESLint configuration, https://eslint.org/docs/latest/use/configure/configuration-files, accessed 2026-09-20.
- cspell, https://cspell.org/docs/Welcome, accessed 2026-09-20.
