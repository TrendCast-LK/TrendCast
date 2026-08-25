/** @type {import('tailwindcss').Config} */

// Each design-system color is backed by a `--color-x: R G B` CSS custom
// property (see src/index.css for the light/.dark value tables), so a single
// set of `bg-surface` / `text-on-background` / etc. classes works in both
// themes and still supports Tailwind's opacity modifiers (`bg-primary/20`).
function withOpacity(variableName) {
  return `rgb(var(${variableName}) / <alpha-value>)`;
}

const colorTokens = [
  "tertiary-fixed-dim",
  "primary-fixed-dim",
  "on-primary-fixed",
  "on-tertiary-fixed-variant",
  "tertiary-container",
  "surface-container",
  "surface-container-highest",
  "error",
  "on-primary-fixed-variant",
  "on-secondary-fixed",
  "surface-container-high",
  "surface",
  "on-tertiary",
  "on-primary",
  "secondary-fixed-dim",
  "on-secondary-container",
  "tertiary-fixed",
  "surface-tint",
  "primary-container",
  "on-error",
  "on-secondary",
  "primary",
  "secondary-container",
  "inverse-on-surface",
  "background",
  "outline-variant",
  "tertiary",
  "secondary",
  "inverse-primary",
  "primary-fixed",
  "on-surface-variant",
  "surface-dim",
  "surface-container-lowest",
  "inverse-surface",
  "on-primary-container",
  "on-background",
  "on-tertiary-container",
  "secondary-fixed",
  "surface-container-low",
  "on-surface",
  "on-error-container",
  "surface-variant",
  "surface-bright",
  "on-secondary-fixed-variant",
  "on-tertiary-fixed",
  "outline",
  "error-container",
];

const themeColors = Object.fromEntries(
  colorTokens.map((token) => [token, withOpacity(`--color-${token}`)]),
);

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: themeColors,
      borderRadius: {
        DEFAULT: "0.25rem",
        lg: "0.5rem",
        xl: "0.75rem",
        full: "9999px",
      },
      spacing: {
        "margin-desktop": "40px",
        unit: "8px",
        gutter: "24px",
        "margin-mobile": "16px",
        "container-max": "1280px",
      },
      maxWidth: {
        "container-max": "1280px",
      },
      fontFamily: {
        "body-lg": ["Inter", "sans-serif"],
        "label-sm": ["Geist", "sans-serif"],
        "label-md": ["Geist", "sans-serif"],
        "headline-md": ["Geist", "sans-serif"],
        "body-md": ["Inter", "sans-serif"],
        "headline-lg": ["Geist", "sans-serif"],
        "headline-lg-mobile": ["Geist", "sans-serif"],
        "display-lg": ["Geist", "sans-serif"],
      },
      fontSize: {
        "body-lg": ["18px", { lineHeight: "28px", fontWeight: "400" }],
        "label-sm": ["12px", { lineHeight: "16px", fontWeight: "500" }],
        "label-md": [
          "14px",
          { lineHeight: "20px", letterSpacing: "0.05em", fontWeight: "500" },
        ],
        "headline-md": ["24px", { lineHeight: "32px", fontWeight: "600" }],
        "body-md": ["16px", { lineHeight: "24px", fontWeight: "400" }],
        "headline-lg": [
          "32px",
          { lineHeight: "40px", letterSpacing: "-0.02em", fontWeight: "600" },
        ],
        "headline-lg-mobile": [
          "24px",
          { lineHeight: "32px", letterSpacing: "-0.02em", fontWeight: "600" },
        ],
        "display-lg": [
          "48px",
          { lineHeight: "56px", letterSpacing: "-0.04em", fontWeight: "700" },
        ],
      },
    },
  },
  plugins: [require("@tailwindcss/forms"), require("@tailwindcss/container-queries")],
};
