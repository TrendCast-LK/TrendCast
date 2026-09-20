# TrendCast UI Testing Report

**Project:** TrendCast, Group 20, CS3501  
**Scope:** React/Vite frontend UI only. Backend logic and ML accuracy were not evaluated.  
**Test date:** 2026-09-20

## 1. Test Setup

- Frontend URL: `http://localhost:5173`
- Backend URL used by the client: `http://localhost:8000`
- Framework: Playwright Test with `@axe-core/playwright`
- Browsers: Chromium and Firefox
- Optional WebKit installation was attempted, but Playwright reported the missing Windows dependency `jxl_cms.dll`; WebKit was therefore not included in the runnable configuration.
- Playwright starts Vite automatically with `npm run dev -- --host localhost`.
- Configuration: [frontend/playwright.config.js](../../frontend/playwright.config.js)
- UI tests: [frontend/tests/ui/ui.spec.js](../../frontend/tests/ui/ui.spec.js)
- Live health-gated test: [frontend/tests/ui/live.spec.js](../../frontend/tests/ui/live.spec.js)

All normal UI tests mock frontend API calls with `page.route()` for repeatability. The separate `@live` test checks `http://127.0.0.1:8000/forecast/health` and skips automatically when the backend is unavailable. In the recorded run it was skipped in both browsers because the health endpoint was not ready.

## 2. Source-Grounded Target Test Items

The router defines these routes:

| Route | UI target items |
|---|---|
| `/` | Sign-in page; Google button; email and password fields; forgot-password anchor (`#forgot`); Sign In button; Sign up link; theme toggle; loading text `Signing in…`; API error message. |
| `/sign-up` | Full Name, Email, YouTube Channel URL, Password fields; required/minimum-length/browser URL validation; show/hide password button; Create Account button; Google button; Sign in link; loading text `Creating account…`; API error message; theme toggle. |
| `/dashboard` | Welcome heading; subscriber/monthly-view summary; Analyze New Content / Start Prediction link; recent prediction cards; complete and draft card states; empty state and Create a prediction link; sidebar, search field, theme toggle, notifications, help, channel link, logout, New Prediction link. |
| `/new-prediction` | Prediction Title input; Video Description textarea; Video Length minutes and seconds number inputs; Category select with 15 implemented options; tag input with Enter/comma/blur add behavior and removable tag buttons; Target Date and optional Target Time; hidden file input accepting CSV/JSON/PNG/JPG/JPEG; thumbnail/dataset dropzone text; Save as Draft and Initialize Prediction buttons; title validation error; submitting labels `Saving…`/`Predicting…`; API error message. |
| `/prediction-result/:id` | Loading state; not-found/API error state and Back to Dashboard link; Export Report button; Submit Another navigation; thumbnail; category/tags; confidence and target date text; 7-Day View Forecast; predicted value/change indicator; Predicted Trajectory canvas chart; Views control; shared result topbar navigation. |
| `/trends` | Loading state; API error; zero-completed empty state and Create a prediction link; total predictions, average views, confidence and top-category stat tiles; Predicted Views Over Time canvas chart; Category Breakdown bars. |
| `/settings` | Light/Dark appearance buttons; profile Full Name, disabled Email, Subscribers and Monthly Views inputs; Save Changes and Saved state; current/new/confirm password inputs; Update Password and Password updated state; mismatched-password error; API errors; shared shell. |
| `/channel` | Loading state; Channel Data heading; Refresh button and `Refreshing…` state; fetch-error state with Try again; channel banner/thumbnail/title/country/year/description; View on YouTube link; subscriber, total views and videos stat tiles; last-updated text. |
| `*` | Redirect to `/`. |

### Shared controls and states

- `Sidebar`: Dashboard, Predictions (`/new-prediction`), Trends, Settings, logo/dashboard link, Log out, New Prediction.
- `Topbar`/`ResultsTopbar`: search input, theme toggle, notifications menu, help menu, channel link; result topbar also has Home and Predictions links.
- Notifications: loading text, empty text, unread badge, notification list, Mark all read, per-notification read action.
- Help: Help & Support panel, four FAQ entries, Contact support mailto link, outside-click close.
- Theme: light/dark document class and local-storage persistence.
- No `Explorer.jsx`, `ChannelDetail.jsx`, `Forecast.jsx`, or `frontend/src/api/client.js` exists in the current source tree. The implemented equivalents are `Channel.jsx`, `PredictionResult.jsx`, `NewPrediction.jsx`, and `frontend/src/lib/api.js`; those were tested instead.
- There are no HTML tables in the implemented pages. Charts are Chart.js canvases, not Recharts components.

## 3. Test Cases Executed

- Public sign-in fields, sign-up link, and screenshot evidence.
- Sign-up required fields, URL type, password visibility toggle.
- Protected-route redirect for an unauthenticated user.
- Dashboard empty state, shell controls, help menu, completed prediction card, draft card and card destinations.
- New prediction title validation, category selection, tag add/remove, mocked submission and result navigation.
- Prediction result loading/content surface, forecast canvas and Submit Another navigation.
- Trends summary, canvas chart and category breakdown.
- Settings theme switch, profile save confirmation and password mismatch validation.
- Channel data display and refresh control.
- Notifications unread display and Mark all read behavior.
- Axe scan of sign-in: zero automatically detected critical violations.
- Live backend health-gated check: skipped when `/forecast/health` was unavailable.

## 4. Results

The final cross-browser run executed **26 tests: 24 passed and 2 skipped**. The 2 skipped tests were the `@live` health-gated test, once for Chromium and once for Firefox. The focused Chromium run executed **12 tests: 12 passed**, including the axe check.

The tests use mocked responses for `/auth/me`, `/notifications`, notification read actions, dashboard summary, predictions list/create/detail, trends summary, channel load/refresh, profile update and password change. No backend or ML behavior is asserted by the mocked UI tests.

## 5. Screenshot Evidence

- [Sign-in page](screenshots/01-sign-in.png)
- [Dashboard empty state with help menu](screenshots/02-dashboard-empty.png)

## 6. Execution Commands

From `frontend/`:

```powershell
npm install
npx playwright install chromium firefox
npm run test:ui
```

The HTML and JSON Playwright reports are generated under `frontend/playwright-report/` after a run. Screenshots are configured for every test and explicit report evidence is saved under `docs/testing/screenshots/`. `npm run build` also passed; Vite emitted only its existing large-chunk warning.

## 7. Limitations

The tests verify rendered UI, browser validation, navigation, loading/empty/error states, API interaction wiring and chart presence. They do not verify API correctness, database behavior, forecast calculations, model accuracy, or visual pixel fidelity. WebKit could not be enabled on this Windows environment because its host dependency check reported `jxl_cms.dll` missing.
