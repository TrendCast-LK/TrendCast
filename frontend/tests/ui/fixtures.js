import { test as base, expect } from "@playwright/test";

export const user = {
  id: 1,
  full_name: "Nimal Perera",
  email: "nimal@example.com",
  subscribers: 12500,
  monthly_views: 240000,
  channel_thumbnail_url: "https://example.com/avatar.jpg",
};

export const channel = {
  channel_id: "UC1234567890123456789012",
  title: "Nimal Creates",
  description: "Sri Lankan creator experiments and tutorials.",
  thumbnail_url: "https://example.com/channel.jpg",
  banner_url: null,
  country: "LK",
  published_at: "2020-01-01T00:00:00Z",
  subscriber_count: 12500,
  view_count: 1250000,
  video_count: 86,
  subscriber_hidden: false,
  channel_url: "https://www.youtube.com/@nimalcreates",
  fetched_at: "2026-09-20T10:00:00Z",
};

export const prediction = {
  id: 42,
  title: "Sri Lankan Street Food Tour",
  category: "Travel & Events",
  tags: ["Sri Lanka", "food"],
  target_date: "2026-10-01",
  predicted_views: 185000,
  confidence: 0.82,
  change_vs_avg: 14.5,
  thumbnail_url: null,
  trajectory: [
    { day: 1, views: 15000 },
    { day: 2, views: 42000 },
    { day: 3, views: 78000 },
    { day: 7, views: 185000 },
  ],
};

export const summary = {
  full_name: user.full_name,
  subscribers: user.subscribers,
  monthly_views: user.monthly_views,
};

export const trends = {
  total_predictions: 3,
  completed_predictions: 2,
  draft_predictions: 1,
  average_predicted_views: 120000,
  average_confidence: 0.76,
  best_category: "Travel & Events",
  timeline: [
    { title: "Street Food", predicted_views: 185000 },
    { title: "Colombo Walk", predicted_views: 55000 },
  ],
  category_breakdown: [
    { category: "Travel & Events", count: 1, average_views: 185000 },
    { category: "Education", count: 1, average_views: 55000 },
  ],
};

export async function mockApi(page, { predictions = [], notifications = [], channelData = channel } = {}) {
  await page.route("http://localhost:8000/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path === "/auth/me" && request.method() === "GET") return route.fulfill({ json: user });
    if (path === "/notifications" && request.method() === "GET") {
      return route.fulfill({ json: { notifications, unread_count: notifications.filter((item) => !item.read).length } });
    }
    if (path === "/notifications/read-all" || path.match(/^\/notifications\/\d+\/read$/)) {
      return route.fulfill({ status: 204 });
    }
    if (path === "/dashboard/summary") return route.fulfill({ json: summary });
    if (path === "/predictions" && request.method() === "GET") return route.fulfill({ json: predictions });
    if (path === "/predictions" && request.method() === "POST") return route.fulfill({ json: { id: 42 } });
    if (path.match(/^\/predictions\/\d+$/)) return route.fulfill({ json: prediction });
    if (path === "/trends/summary") return route.fulfill({ json: trends });
    if (path === "/channel/me") return route.fulfill({ json: channelData });
    if (path === "/channel/refresh" && request.method() === "POST") return route.fulfill({ json: channel });
    if (path === "/auth/me" && request.method() === "PATCH") return route.fulfill({ json: user });
    if (path === "/auth/change-password") return route.fulfill({ status: 204 });
    return route.fulfill({ status: 404, json: { detail: `Unhandled mock endpoint: ${path}` } });
  });
}

export const test = base.extend({
  authenticatedPage: async ({ page }, use) => {
    await page.addInitScript(({ storedUser }) => {
      localStorage.setItem("trendcast_token", "ui-test-token");
      localStorage.setItem("trendcast_user", JSON.stringify(storedUser));
      localStorage.setItem("trendcast_theme", "light");
    }, { storedUser: user });
    await mockApi(page);
    await use(page);
  },
});

export { expect };