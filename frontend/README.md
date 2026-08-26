# Frontend — ViewCast

The React dashboard for TrendCast. Users sign up with their YouTube
channel, then run predictions for planned uploads.

Branded as **ViewCast**. Built from a design called "Insight Glow"
(purple → pink glassmorphism).

See the [root README](../README.md) for how this fits into the whole system.

## Stack

- React 19 + Vite
- React Router (routing + login-required route guards)
- Tailwind CSS v3
- Chart.js — the prediction trajectory chart
- Plain `fetch` for API calls (`src/lib/api.js`), no extra HTTP library

## Screens

| Route | Screen | Needs login? |
| --- | --- | --- |
| `/` | Sign In | no |
| `/sign-up` | Sign Up | no |
| `/dashboard` | Stats + recent predictions | yes |
| `/new-prediction` | Create a prediction (form + file upload) | yes |
| `/prediction-result/:id` | One prediction's forecast + chart | yes |
| `/trends` | Stat tiles, views-over-time chart, category breakdown | yes |
| `/settings` | Edit profile/channel stats, appearance, password | yes |
| `/channel` | Real YouTube channel data (banner, avatar, counts) | yes |

If you're not logged in, protected routes redirect to Sign In, then send
you back to the page you wanted.

There is no admin or monitoring screen. Every page here is for the
signed-in creator only.

## Setup

Start the backend first — see [../backend/README.md](../backend/README.md).

```bash
cd frontend
npm install
cp .env.example .env    # points at http://localhost:8000 by default
```

## Run it

```bash
npm run dev      # http://localhost:5173
npm run build     # production build, output to dist/
```

## Test it's working

1. Start the backend and this dev server.
2. Open `http://localhost:5173`, sign up with a real YouTube channel URL.
3. Check the Channel page shows real channel data (needs a working
   `YOUTUBE_API_KEY` on the backend).
4. Run a prediction from `/new-prediction` and confirm you get back a
   real forecast chart — this calls the trained model on the backend, not
   a stub.

There's no automated test suite for the frontend yet (`npm run lint` runs
oxlint, but that's the only check).

## Key files

- `src/lib/api.js` — thin `fetch` wrapper for every backend call. Attaches
  the login token, throws a readable `ApiError` on failure.
- `src/context/AuthContext.jsx` — holds the current user + token
  (saved to `localStorage`), exposes `login`/`signup`/`logout`.
- `src/components/RequireAuth.jsx` — route guard for the pages above.
- `src/components/Sidebar.jsx` — shared nav for Dashboard, New Prediction,
  and Prediction Result.
- `src/components/NotificationsMenu.jsx` — the bell icon dropdown. Fetches
  `GET /notifications` and marks things read.
- `src/components/HelpMenu.jsx` — static FAQ panel, no backend calls.
- `src/context/ThemeContext.jsx` — dark/light mode toggle.
- `src/lib/chartTheme.js` — light/dark color sets for Chart.js (Chart.js
  can't read CSS variables directly).

## Notes on how things work

- **New Prediction** sends one file. The backend decides if it's a
  thumbnail or a dataset by its file type. The two submit buttons (save
  draft / run prediction) hit the same endpoint with a different flag.
- **Sign Up** needs a YouTube channel URL. Right after account creation,
  the backend looks it up via the YouTube Data API. If that fails (bad
  URL, no API key, channel not found), signup still succeeds — the
  Channel page shows the error with a retry button.
- **`VITE_API_URL`** (in `.env`) points at the backend. Change it if the
  API isn't on `localhost:8000`.
- **Dark mode** follows your OS setting on first visit, then remembers
  your choice in `localStorage`. Toggle it from the sun/moon icon or
  Settings → Appearance. Applied before React mounts, so there's no flash
  of the wrong theme.
